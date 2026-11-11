# System Architecture (Internal Hackathon)

## Flow

Two interchangeable audio sources feed one callback. The scorer cannot tell them apart.

```
[A] file_feeder.py  ◄── PRIMARY TODAY
    WAV → ffmpeg G.711 → 320 B chunks at wall-clock speed
[B] SIP phone ──► Asterisk ──► AudioSocket   ◄── LATER, OPTIONAL
                │
                ▼
        same callback interface   8 kHz slin16, 20 ms frames
                │
                ▼
        streaming_scorer.py     ring buffer → 4 s window / 0.5 s hop
                │               ** 8 kHz → 16 kHz upsample here **
                │               ** /ws/live is already 16 kHz **
                ├──► acoustic.py   AASIST (16 kHz raw waveform)
                ├──► prosody.py    F0 contour + pause/rhythm
                └──► speaker.py    ECAPA cosine vs enrolled ref
                │
                ▼
        fusion + context flags → EMA smoothing → thresholds
        (all in streaming_scorer.py)
                │
                ▼
        api.py   Flask + flask-sock · WebSocket push · REST · SQLite
                │
        ┌───────┴────────┐
   dashboard/       approval-ui/
```

## FROZEN API contract — do not modify after hour 1

```json
{
  "call_id": "c1",
  "t": 4.2,
  "score": 0.87,
  "layers": { "acoustic": 0.91, "prosody": 0.72, "speaker": 0.83 },
  "context_flags": ["unknown_origin", "high_value_txn"],
  "state": "OK|ESCALATE|BLOCK"
}
```

REST: `POST /session` opens a scored session · `GET /session/{id}/risk` returns current score · `POST /api/analyze` scores an uploaded WAV · WebSocket `/ws` (fan-out) and `/ws/live` (browser mic). There is no OpenAPI page.

## Critical constants
| Constant | Value | Note |
|---|---|---|
| AudioSocket port | 9092 | TCP, localhost |
| Wire format | 3 B header (type + BE length) + payload | Payload is **little-endian** |
| Audio frame | 0x10, 320 B, 20 ms | 8 kHz, 16-bit, mono |
| Model input | **16 kHz** raw waveform | Upsample required — top integration risk |
| Window / hop | 4.0 s / 0.5 s | 0.25 s hop on the 16 kHz `/ws/live` path |
| Smoothing | EMA, α ≈ 0.3 | Raw scores flap and look broken |
| Fusion weights | acoustic 0.60 · speaker 0.25 · prosody 0.15 | Hand-set, justified, never learned |

## Layer notes forced by the research
- **Prosody:** jitter and shimmer are unreliable at 8 kHz — computed and displayed with **weight 0 inside the prosody risk**; the prosody layer itself is weighted 0.15 from F0 shape and rhythm.
- **Speaker:** reference voiceprint must be enrolled **through the same codec path** as the live call; `/api/enroll` does the G.711 µ-law round trip before embedding. Wideband enrolment against narrowband live audio is the failure mode.
- **Dual output path:** the local WebSocket carries the live score line (demo-critical, must survive a dead venue network). SQLite carries persistence, the call log and review state; an optional legacy Supabase async flush is off by default and must never stall the scorer loop.
- **Ingestion adapters:** PSTN/PBX built; VoIP trunk and collaboration-platform adapters are a boundary on the architecture slide, not code.
