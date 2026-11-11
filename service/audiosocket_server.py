"""Asterisk AudioSocket TCP server on 127.0.0.1:9092.

Wire format (app_audiosocket): a 3-byte header followed by a payload.
    byte 0     message type
    bytes 1-2  payload length, big-endian (unsigned 16-bit)
    bytes 3..  payload  (PCM payloads are little-endian s16le)

Audio frames are type 0x10 with a 320-byte payload (20 ms of 8 kHz 16-bit mono),
323 bytes on the wire. TCP fragments arbitrarily, so every read goes through
`recvall` to pull exactly N bytes.

The callback contract is identical to file_feeder.py:

    on_audio(index: int, chunk: bytes) -> None   # monotonic 0-based audio-frame
                                                 # index, then the 0x10 payload untouched
    on_event(kind: str, data: bytes)             # "uuid" | "dtmf" | "hangup" | "error"

`index` counts audio frames only (control messages do not advance it), so
downstream t = index * 0.02 s stays exact regardless of scorer lag, exactly as
with file_feeder. streaming_scorer.py cannot tell which source it is reading.
"""

from __future__ import annotations

import socket
import socketserver
import struct
from typing import Callable

HOST = "127.0.0.1"
PORT = 9092

TYPE_HANGUP = 0x00
TYPE_UUID = 0x01
TYPE_DTMF = 0x03
TYPE_AUDIO = 0x10
TYPE_ERROR = 0xFF

AUDIO_PAYLOAD_BYTES = 320

AudioCallback = Callable[[int, bytes], None]
EventCallback = Callable[[str, bytes], None]


def recvall(sock: socket.socket, n: int) -> bytes | None:
    """Read exactly `n` bytes from `sock`. Return None if it closed first."""
    buf = bytearray()
    while len(buf) < n:
        part = sock.recv(n - len(buf))
        if not part:
            return None
        buf += part
    return bytes(buf)


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server: "AudioSocketServer" = self.server  # type: ignore[assignment]
        sock = self.request
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        audio_index = 0

        while True:
            header = recvall(sock, 3)
            if header is None:
                break
            msg_type = header[0]
            (length,) = struct.unpack(">H", header[1:3])

            payload = b""
            if length:
                payload = recvall(sock, length)
                if payload is None:
                    break

            if msg_type == TYPE_AUDIO:
                server.on_audio(audio_index, payload)
                audio_index += 1
            elif msg_type == TYPE_UUID:
                server.on_event("uuid", payload)
            elif msg_type == TYPE_DTMF:
                server.on_event("dtmf", payload)
            elif msg_type == TYPE_HANGUP:
                server.on_event("hangup", payload)
                break
            elif msg_type == TYPE_ERROR:
                server.on_event("error", payload)
                break
            # Unknown types: length was already consumed, so the stream stays
            # aligned. Ignore and keep reading.


class AudioSocketServer(socketserver.ThreadingTCPServer):
    """Threaded AudioSocket listener. One handler thread per Asterisk connection."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        on_audio: AudioCallback,
        on_event: EventCallback | None = None,
        host: str = HOST,
        port: int = PORT,
    ) -> None:
        self.on_audio = on_audio
        self.on_event: EventCallback = on_event or (lambda kind, data: None)
        super().__init__((host, port), _Handler)


def serve(on_audio: AudioCallback, on_event: EventCallback | None = None) -> None:
    """Block serving AudioSocket connections until interrupted."""
    with AudioSocketServer(on_audio, on_event) as server:
        print(f"AudioSocket listening on {HOST}:{PORT}")
        server.serve_forever()


def _selfcheck() -> None:
    """Prove framing + recvall against a client that fragments every which way."""
    import threading
    import uuid as _uuid

    received: list[tuple[int, bytes]] = []
    events: list[tuple[str, bytes]] = []

    server = AudioSocketServer(lambda i, c: received.append((i, c)),
                               lambda k, d: events.append((k, d)),
                               host=HOST, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    call_id = _uuid.uuid4().bytes
    frames = bytearray()
    frames += bytes([TYPE_UUID]) + struct.pack(">H", 16) + call_id
    audio = [bytes([i % 256]) * AUDIO_PAYLOAD_BYTES for i in range(1, 4)]
    for a in audio:
        frames += bytes([TYPE_AUDIO]) + struct.pack(">H", AUDIO_PAYLOAD_BYTES) + a
    frames += bytes([TYPE_HANGUP]) + struct.pack(">H", 0)

    c = socket.create_connection(server.server_address)
    # one byte at a time: worst-case fragmentation
    for b in frames:
        c.sendall(bytes([b]))
    c.close()

    import time
    for _ in range(200):
        if events and events[-1][0] == "hangup":
            break
        time.sleep(0.01)
    server.shutdown()

    assert [k for k, _ in events] == ["uuid", "hangup"], events
    assert events[0][1] == call_id, "UUID payload mismatch"
    assert [i for i, _ in received] == [0, 1, 2], "audio index not monotonic"
    assert [c for _, c in received] == audio, "audio payload corrupted through recvall"
    assert all(len(c) == AUDIO_PAYLOAD_BYTES for _, c in received)
    print(f"ok: 3 audio frames of {AUDIO_PAYLOAD_BYTES} B, index 0..2, reassembled "
          f"byte-by-byte, events={[k for k, _ in events]}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "selfcheck":
        _selfcheck()
    else:
        serve(lambda index, chunk: print(f"audio #{index} {len(chunk)} B"),
              lambda kind, data: print(f"event {kind} {data!r}"))
