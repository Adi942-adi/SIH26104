"""Primary demo audio source.

WAV file -> ffmpeg G.711 (mu-law) round-trip -> 8 kHz 16-bit mono little-endian
PCM, streamed into a callback as 320-byte chunks paced at wall-clock speed
(20 ms per chunk, ~50 chunks/sec).

The callback contract is identical to audiosocket_server.py:

    on_audio(index: int, chunk: bytes) -> None   # monotonic 0-based frame index,
                                                 # then exactly 320 B 8 kHz s16le mono
    on_event(kind: str, data: bytes)             # optional; feeder only emits ("end", b"")

`index` is the source of truth for downstream time: t = index * 0.02 s. The
score-vs-time axis stays exact even when the scorer lags behind realtime.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
import wave
import audioop
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

SAMPLE_RATE = 8000
CHUNK_BYTES = 320          # 20 ms of 8 kHz * 16-bit * mono
CHUNK_SECONDS = 0.020
DRIFT_WARN_SECONDS = 0.5   # wall-clock lag past this => scorer is slower than realtime

AudioCallback = Callable[[int, bytes], None]
EventCallback = Callable[[str, bytes], None]

# winget "Gyan.FFmpeg" full build lands here and is not added to PATH.
_WINGET_FFMPEG = "AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg*/ffmpeg-*/bin/ffmpeg.exe"


def _resolve_ffmpeg(ffmpeg: str | None) -> str:
    if ffmpeg:
        return ffmpeg
    found = shutil.which("ffmpeg")
    if found:
        return found
    for cand in Path.home().glob(_WINGET_FFMPEG):
        return str(cand)
    raise FileNotFoundError(
        "ffmpeg not found on PATH. Pass ffmpeg=<path to ffmpeg.exe> or add it to PATH."
    )


def transcode_ulaw(wav_path: Path, ffmpeg: str | None = None) -> bytes:
    """Return `wav_path` as 8 kHz s16le mono PCM, forced through G.711 mu-law.

    Two ffmpeg passes on purpose: encode to mu-law, then decode back. A single
    resample pass would skip the companding loss, which is the whole reason the
    telephony path degrades speaker embeddings (see Decisions.md D5).
    """
    wav_path = Path(wav_path)
    if not wav_path.is_file():
        raise FileNotFoundError(wav_path)
    try:
        exe = _resolve_ffmpeg(ffmpeg)
    except FileNotFoundError:
        logger.warning("ffmpeg not found; using standard-library WAV fallback")
        with wave.open(str(wav_path), "rb") as source:
            pcm = source.readframes(source.getnframes())
            if source.getsampwidth() != 2:
                pcm = audioop.lin2lin(pcm, source.getsampwidth(), 2)
            if source.getnchannels() > 1:
                pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
            if source.getframerate() != SAMPLE_RATE:
                pcm, _ = audioop.ratecv(pcm, 2, 1, source.getframerate(), SAMPLE_RATE, None)
        return audioop.ulaw2lin(audioop.lin2ulaw(pcm, 2), 2)

    encode = [
        exe, "-hide_banner", "-loglevel", "error",
        "-i", str(wav_path),
        "-ar", str(SAMPLE_RATE), "-ac", "1",
        "-f", "mulaw", "-",
    ]
    ulaw = subprocess.run(encode, capture_output=True, check=True).stdout

    decode = [
        exe, "-hide_banner", "-loglevel", "error",
        "-f", "mulaw", "-ar", str(SAMPLE_RATE), "-ac", "1", "-i", "-",
        "-f", "s16le", "-",
    ]
    return subprocess.run(decode, input=ulaw, capture_output=True, check=True).stdout


def _pace(pcm: bytes, on_audio: AudioCallback) -> int:
    """Emit (index, 320-byte chunk) pairs at 20 ms spacing. Returns chunks sent.

    When ahead of schedule the loop sleeps to the chunk's absolute deadline
    (`start + n * CHUNK_SECONDS`), so scheduler jitter never accumulates. When
    *behind* (scorer callback slower than realtime) it never drops or skips: the
    monotonic `index` keeps downstream t exact, latency just grows. Wall-clock
    lag past DRIFT_WARN_SECONDS is logged once as a scorer-too-slow signal.

    A trailing partial chunk (< 320 B) is dropped: real AudioSocket frames are
    always exactly 320 B and a short frame would be garbage to the scorer.
    """
    full_chunks = len(pcm) // CHUNK_BYTES
    start = time.perf_counter()
    warned = False
    for n in range(full_chunks):
        offset = n * CHUNK_BYTES
        on_audio(n, pcm[offset:offset + CHUNK_BYTES])
        slack = (start + (n + 1) * CHUNK_SECONDS) - time.perf_counter()
        if slack > 0:
            time.sleep(slack)
        elif -slack > DRIFT_WARN_SECONDS and not warned:
            warned = True
            logger.warning(
                "feeder %.0f ms behind realtime at chunk %d: scorer callback is "
                "slower than realtime. t axis stays exact (index*%.0fms); latency grows.",
                -slack * 1000, n, CHUNK_SECONDS * 1000,
            )
    return full_chunks


def feed(
    wav_path: str | Path,
    on_audio: AudioCallback,
    *,
    on_event: EventCallback | None = None,
    ffmpeg: str | None = None,
) -> None:
    """Transcode `wav_path` and stream it into `on_audio` at wall-clock speed."""
    pcm = transcode_ulaw(Path(wav_path), ffmpeg)
    _pace(pcm, on_audio)
    if on_event is not None:
        on_event("end", b"")


def _selfcheck() -> None:
    """ffmpeg-free check of the pacing loop: size, count, and wall-clock rate."""
    seconds = 1.0
    pcm = b"\x00\x01" * (SAMPLE_RATE * int(seconds))  # 1 s of s16le mono
    seen: list[tuple[int, int]] = []
    t0 = time.perf_counter()
    sent = _pace(pcm, lambda i, chunk: seen.append((i, len(chunk))))
    elapsed = time.perf_counter() - t0

    assert sent == 50, sent
    assert [i for i, _ in seen] == list(range(50)), "index not monotonic 0..49"
    assert {size for _, size in seen} == {CHUNK_BYTES}, seen
    assert 0.9 <= elapsed <= 1.25, elapsed  # ~1 s of audio in ~1 s of wall time
    print(f"ok: {sent} chunks of {CHUNK_BYTES} B, index 0..{sent - 1}, in {elapsed:.3f}s")


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 1:
        _selfcheck()
    else:
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
        seen: list[int] = []
        first = last = time.perf_counter()

        def sink(index: int, chunk: bytes) -> None:
            global first, last
            assert len(chunk) == CHUNK_BYTES, len(chunk)
            if not seen:
                first = time.perf_counter()
            last = time.perf_counter()
            seen.append(index)

        feed(sys.argv[1], sink)
        span = last - first
        assert seen == list(range(len(seen))), "index gap"
        print(f"{len(seen)} chunks x {CHUNK_BYTES} B | index 0..{seen[-1]} "
              f"(t_end = {seen[-1] * CHUNK_SECONDS:.2f}s) | {span:.2f}s wall | "
              f"{len(seen) / span:.1f} chunks/s")
