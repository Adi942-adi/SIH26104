```json
{"call_id":"c1","t":4.2,"score":0.87,
 "layers":{"acoustic":0.91,"prosody":0.72,"speaker":0.83},
 "context_flags":["unknown_origin"],"state":"OK"}
```

Constants below it: WebSocket ws://localhost:8000/ws · states OK <0.55, ESCALATE 0.55–0.80, BLOCK >0.80 · audio in 8 kHz 16-bit mono 320-byte frames on AudioSocket/file feeders, 16 kHz PCM on `/ws/live` · model in 16 kHz, upsample happens in the scorer · window 4.0 s, hop 0.5 s (0.25 s at 16 kHz), EMA alpha 0.3.
