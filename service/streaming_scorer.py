"""Streaming fused risk scorer.

Consumes 320-byte s16le frames from file_feeder, audiosocket_server or the
live browser ingest (identical `on_audio(index, chunk)` callback), keeps a
four-second model context, and every HOP_FRAMES frames scores the trailing
window. Two acquisition rates are supported:

  * 8 kHz telephony (AudioSocket / file feeders): each frame is 20 ms, and
    the trailing [SRC_RATE*4] window is resampled 8 kHz -> 16 kHz before
    scoring. This upsample is MANDATORY and is the top integration risk
    (System-Architecture.md): the scored window must be exactly
    MODEL_RATE*4 = 64000 samples. The first window's tensor shape is printed
    and asserted.
  * 16 kHz wideband (live browser ingest): AASIST's native distribution; each
    frame is 10 ms, the window is already at model rate and is used as-is.

The acoustic model needs a 16 kHz raw waveform.
Layer scoring sits behind the `LayerScorer` protocol so published-weights AASIST
(and ECAPA / parselmouth) slot in unchanged. The placeholder here is RMS-derived.
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from typing import Callable, Protocol

import numpy as np
import torch
import torch.nn.functional as F

logger = logging.getLogger(__name__)

try:
    import torchaudio
except (ModuleNotFoundError, ImportError, OSError, RuntimeError) as exc:
    # torchaudio ships a compiled extension: a torch/torchaudio version mismatch
    # or a DLL load failure raises OSError at import time, not just
    # ModuleNotFoundError. Degrade to the linear-interpolation resampler instead
    # of killing the whole service at startup.
    torchaudio = None
    logger.warning("torchaudio unavailable (%s); using linear-interpolation resample", exc)

from layers import EnsembleScorer

SRC_RATE = 8000
MODEL_RATE = 16000
FRAME_SAMPLES = 160          # 20 ms @ 8 kHz  (a 320-byte s16le frame)
WINDOW_SAMPLES = SRC_RATE * 4  # AASIST's approximately four-second context
HOP_FRAMES = 25              # 0.5 s hop @ 8 kHz; 0.25 s hop @ 16 kHz

# The live browser ingest streams wideband 16 kHz PCM so AASIST sees its native
# input distribution (see api.py /ws/live). 8 kHz stays the telephony source
# rate for AudioSocket / file feeders.
LIVE_RATE = 16000
LIVE_FRAME_SAMPLES = 160     # 10 ms @ 16 kHz  (a 320-byte s16le frame)
LIVE_WINDOW_SAMPLES = LIVE_RATE * 4

# Fusion weights — module-level constants, never learned or tuned (Decisions.md D6).
W_ACOUSTIC = 0.60
W_SPEAKER = 0.25
W_PROSODY = 0.15

EMA_ALPHA = 0.3

# State thresholds (api-contract.md): OK < 0.55, ESCALATE 0.55-0.80, BLOCK > 0.80.
ESCALATE_AT = 0.55
BLOCK_AT = 0.80

# Speech-presence gate. AASIST was trained on speech vs. synthetic *speech*; a
# music bed, translucent tone or silence window is out of domain and the
# acoustic layer can flag such non-speech as artificial (a false positive on
# hold music / ringers). A real *voice* - human or cloned - always keeps normal
# voicing AND silence gaps between syllables, so a window is gated to
# non-speech when either (a) it carries almost no voiced F0 (silence/whisper)
# or (b) it is continuously and fully voiced (a held tone, instrument or music
# bed, which Praat reads as ~100 % periodic). Gated windows cannot cross into
# ESCALATE and surface a "non_speech" context flag instead of a spoof verdict.
SPEECH_VOICED_MIN = 0.15
MUSIC_VOICED_MIN = 0.85
SILENCE_FLAT_MAX = 0.04
MUSIC2_VOICED_MIN = 0.75
MUSIC2_SILENCE_MAX = 0.12
MUSIC2_CV_MAX = 0.06
MUSIC3_VOICED_MIN = 0.70
MUSIC3_SILENCE_MAX = 0.10
NON_SPEECH_MAX = 0.48
VOTE_MIN = 0.55   # layer must exceed this to count in the evidence vote
MIXED_MAX = 0.50  # single-layer agreement can never cross into ESCALATE
ACOUSTIC_FLIP = 0.35  # AASIST swinging this hard between hops = out of domain


def _is_non_speech(layers: dict[str, float]) -> bool:
    voiced = float(layers.get("voiced_ratio", 1.0))
    silence = float(layers.get("silence_ratio", 1.0))
    cv = float(layers.get("f0_cv", 1.0))
    if voiced < SPEECH_VOICED_MIN:
        return True                      # silence / whisper-ish window
    if voiced >= MUSIC_VOICED_MIN and silence <= SILENCE_FLAT_MAX:
        return True                      # fully voiced, no rests: tone/instrument
    if voiced >= MUSIC2_VOICED_MIN and silence <= MUSIC2_SILENCE_MAX and cv <= MUSIC2_CV_MAX:
        return True                      # sustained stable pitch: music/singing bed
    if voiced >= MUSIC3_VOICED_MIN and silence <= MUSIC3_SILENCE_MAX:
        return True                      # dense voicing with no real gaps: busy music
    return False


def _evidence_vote(layers: dict[str, float]) -> int:
    return sum(1 for k in ("acoustic", "prosody", "speaker")
               if float(layers.get(k, 0.0)) > VOTE_MIN)


def gate_fused(fused: float, layers: dict[str, float]) -> float:
    """Clamp the fused risk when the window is not speech (music/tones/silence)
    or when only a single layer is alarmed (AASIST alone on narrowband audio
    is not reliable enough to escalate on)."""
    if _is_non_speech(layers):
        return min(fused, NON_SPEECH_MAX)
    if _evidence_vote(layers) < 2:
        return min(fused, MIXED_MAX)
    return fused

ScoreCallback = Callable[[dict], None]


class LayerScorer(Protocol):
    def score(self, window_16k: torch.Tensor) -> dict[str, float]:
        """Return {'acoustic': .., 'prosody': .., 'speaker': ..}, each in [0, 1]."""


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


class RmsPlaceholderScorer:
    """Stand-in until AASIST / ECAPA / parselmouth land. Deterministic, RMS-derived."""

    def score(self, window_16k: torch.Tensor) -> dict[str, float]:
        rms = float(window_16k.pow(2).mean().clamp_min(1e-12).sqrt())
        s = math.tanh(rms * 3.0)
        return {
            "acoustic": _clamp(s),
            "prosody": _clamp(s * 0.85),
            "speaker": _clamp(s * 0.92),
        }


def _default_scorer() -> LayerScorer:
    try:
        scorer = EnsembleScorer.from_environment()
        logger.info("ensemble scorer loaded (AASIST + Praat + ECAPA)")
        return scorer
    except (FileNotFoundError, ImportError, RuntimeError) as exc:
        logger.warning("ensemble unavailable (%s); using RMS placeholder", exc)
        return RmsPlaceholderScorer()


def _state(score: float) -> str:
    if score > BLOCK_AT:
        return "BLOCK"
    if score >= ESCALATE_AT:
        return "ESCALATE"
    return "OK"


class StreamingScorer:
    def __init__(
        self,
        call_id: str,
        on_score: ScoreCallback,
        *,
        scorer: LayerScorer | None = None,
        context_flags: list[str] | None = None,
        src_rate: int = SRC_RATE,
    ) -> None:
        self.call_id = call_id
        self._on_score = on_score
        self._scorer = scorer or _default_scorer()
        self._context_flags = list(context_flags or [])
        self._src_rate = src_rate
        if src_rate == LIVE_RATE:
            self._window = LIVE_WINDOW_SAMPLES
            self._frame_seconds = LIVE_FRAME_SAMPLES / src_rate
        else:
            self._window = WINDOW_SAMPLES
            self._frame_seconds = FRAME_SAMPLES / src_rate
        self._buf: deque[float] = deque(maxlen=self._window)
        self._frames = 0
        self._ema: float | None = None
        self._prev_ac: float | None = None
        self._t0: float | None = None
        self._detected = False
        self._logged_shape = False

    def feed(self, index: int, chunk: bytes) -> None:
        """`on_audio` callback. `index` is the source's monotonic frame index
        (20 ms at 8 kHz, 10 ms at 16 kHz)."""
        if self._t0 is None:
            self._t0 = time.perf_counter()
        samples = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0
        self._buf.extend(samples.tolist())
        self._frames += 1
        if self._frames % HOP_FRAMES == 0 and len(self._buf) == self._window:
            self._score_window()

    def _score_window(self) -> None:
        window_8k = torch.tensor(self._buf, dtype=torch.float32)
        if self._src_rate == LIVE_RATE:
            window_16k = window_8k
        elif torchaudio is not None:
            window_16k = torchaudio.functional.resample(window_8k, self._src_rate, MODEL_RATE)
        else:
            window_16k = F.interpolate(
                window_8k.view(1, 1, -1), size=window_8k.numel() * (MODEL_RATE // self._src_rate),
                mode="linear", align_corners=False
            ).view(-1)

        if not self._logged_shape:
            self._logged_shape = True
            print(f"[scorer] first window tensor shape: {tuple(window_16k.shape)} "
                  f"(src rate {self._src_rate} Hz window in: {tuple(window_8k.shape)})")
            logger.info("first window shape %s", tuple(window_16k.shape))
            assert window_16k.shape[-1] == MODEL_RATE * 4, (
                f"upsample broken: four-second window is {window_16k.shape[-1]} samples, "
                f"expected {MODEL_RATE * 4}. Everything downstream is invalid."
            )

        layers = self._scorer.score(window_16k)
        # AASIST on narrowband (8k) audio is out of distribution: its score
        # flips 0 <-> 1 between adjacent windows on natural speech. When it
        # swings that hard, treat the acoustic call as unreliable and score
        # with a neutral acoustic contribution instead of chasing the flip.
        ac_raw = float(layers["acoustic"])
        ac_flip = self._prev_ac is not None and abs(ac_raw - self._prev_ac) > ACOUSTIC_FLIP
        self._prev_ac = ac_raw
        if ac_flip:
            layers = {**layers, "acoustic": 0.5}
        fused = (
            W_ACOUSTIC * layers["acoustic"]
            + W_SPEAKER * layers["speaker"]
            + W_PROSODY * layers["prosody"]
        )
        # Non-speech gate: music / tones / silence must not read as a voice.
        fused = gate_fused(fused, layers)
        non_speech = _is_non_speech(layers)
        self._ema = (
            fused if self._ema is None
            else EMA_ALPHA * fused + (1 - EMA_ALPHA) * self._ema
        )
        score = round(self._ema, 3)
        state = _state(score)
        t = round(self._frames * self._frame_seconds, 3)

        flags = list(self._context_flags)
        if non_speech:
            flags.append("non_speech")
        elif _evidence_vote(layers) < 2:
            flags.append("single_layer_evidence")
        elif layers.get("ai_consensus"):
            flags.append("ai_detectors_agree")
        if ac_flip:
            flags.append("acoustic_flips")

        if state in ("ESCALATE", "BLOCK") and not self._detected:
            self._detected = True
            wall = time.perf_counter() - self._t0
            logger.warning("call %s: first ESCALATE at t=%.2fs, %.2fs after stream start",
                           self.call_id, t, wall)
            print(f"[scorer] {self.call_id}: first ESCALATE at t={t:.2f}s "
                  f"({wall:.2f}s wall from stream start)")

        self._on_score({
            "call_id": self.call_id,
            "t": t,
            "score": score,
            "layers": {
                "acoustic": round(layers["acoustic"], 3),
                "prosody": round(layers["prosody"], 3),
                "speaker": round(layers["speaker"], 3),
            },
            "context_flags": flags,
            "state": state,
        })


def _selfcheck() -> None:
    msgs: list[dict] = []
    sc = StreamingScorer("selftest", msgs.append, context_flags=["unknown_origin"])
    for i in range(250):  # 5 s of 8 kHz s16le sine -> 250 frames
        n = np.arange(i * FRAME_SAMPLES, (i + 1) * FRAME_SAMPLES)
        frame = (0.3 * np.sin(2 * np.pi * 200.0 * n / SRC_RATE) * 32767)
        sc.feed(i, frame.astype("<i2").tobytes())

    assert msgs, "no score emitted"
    first = msgs[0]
    assert set(first) == {"call_id", "t", "score", "layers", "context_flags", "state"}, set(first)
    assert set(first["layers"]) == {"acoustic", "prosody", "speaker"}, set(first["layers"])
    assert first["t"] == 4.0, first["t"]          # first four-second context closes at frame 200
    assert len(msgs) == 3, len(msgs)              # hops at frame 200/225/250
    assert first["state"] in ("OK", "ESCALATE", "BLOCK")
    print(f"ok: {len(msgs)} score msgs, first at t={first['t']}s, keys frozen, "
          f"state={first['state']} score={first['score']}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _selfcheck()
