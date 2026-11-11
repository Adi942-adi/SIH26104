# CLAUDE.md — SIH26104 Voice Cloning Detection

Auto-loaded by Claude Code. Do not paste `Rules.md` or `Memory.md` on top of this — this file supersedes both for Claude Code sessions.

## Context
Synthetic-voice detection for enterprise telephony. Live call audio → fused risk score → a high-value transaction approval locks before the caller finishes speaking. **Internal hackathon, ~48 hours, one developer.** Single machine, two SIP extensions.

## Environment
**Windows 11 native. No WSL.** PowerShell, not bash. Python venv at `.venv\Scripts\`. Any shell script must be written as cross-platform Python using `subprocess` and `pathlib` instead.

## Pipeline
**Primary input today: `file_feeder.py`** (ffmpeg-transcoded WAV streamed in real time). AudioSocket/Asterisk is a later, optional block — both feed the identical callback interface, so the scorer never knows which source it's reading.

Asterisk (G.711 µ-law) → AudioSocket TCP :9092 → ring buffer (4 s window / 0.5 s hop) → **upsample 8→16 kHz** → three layers → fusion → context rules → EMA smoothing → thresholds → WebSocket → dashboard + approval UI. Browser `/ws/live` ingests 16 kHz directly, so only the 8 kHz sources need the upsample.

| Layer | Model | Fusion weight |
|---|---|---|
| Acoustic | AASIST, published weights, 16 kHz raw waveform | 0.60 |
| Speaker | ECAPA-TDNN cosine vs enrolled reference | 0.25 |
| Prosody | F0 contour + pause/rhythm (parselmouth) | 0.15 |

## FROZEN API contract — never modify
```json
{"call_id":"c1","t":4.2,"score":0.87,
 "layers":{"acoustic":0.91,"prosody":0.72,"speaker":0.83},
 "context_flags":["unknown_origin"],"state":"OK|ESCALATE|BLOCK"}
```

## Facts that constrain the code
- AudioSocket: 3-byte header (1 B type, 2 B **big-endian** length) + payload. Payload PCM is **little-endian**. Audio type `0x10`, 320 B, 20 ms, 8 kHz mono, 323 B total. TCP fragments — always `recvall` exactly N bytes.
- **AudioSocket gives 8 kHz. AASIST needs 16 kHz. Always upsample before inference.** Any path skipping this is a bug. This is the highest-risk defect in the project.
- Published AASIST: 0.83% EER (2019 LA) · 10.51% (2021 LA) · 21.07% (2021 DF). Our numbers should land near these.
- Speaker embeddings lose 3–5× EER on narrowband. Enrol the reference **through the same codec path** as live audio.
- EER by `scipy.optimize.brentq` interpolation, never `min(abs(fpr-fnr))`. Bona fide = positive class (1).
- Jitter and shimmer are unreliable at 8 kHz. Compute and display them; **weight them 0** in fusion.

## Hard rules
1. **Do not persist raw audio.** Not for privacy theatre — because a demo that writes WAVs every call fills the disk and slows the loop. Keep audio in the live ring buffer only.
2. **Light theme only.** No dark mode, no toggle, no `prefers-color-scheme`.
3. Fusion weights are module-level constants. Never learn, fit, or auto-tune them.
4. No auth, multi-tenancy, Docker, cloud, or abstraction layers. Direct code.
4b. **Never run git commands.** No init, no add, no commit, no branch. I handle version control myself.
5. No dependencies outside `Tech-Stack.md` without asking.
6. Ask one question if ambiguous. Do not guess and build.
7. Do not refactor code you weren't asked to touch.

## MCP policy for this project

**Use freely:**
- **Context7** — before writing any SpeechBrain, parselmouth, torchaudio, librosa, Flask or wavesurfer.js code, pull current docs. This is the single highest-value tool here; these libraries are exactly where APIs get hallucinated.
- **Playwright** — verify UI behaviour, not just that it renders. "Does the approve button lock when state becomes ESCALATE" is a Playwright assertion, not an eyeball check.
- **Postman** — exercise the REST endpoints and the WebSocket once `api.py` exists.

- **Supabase** — legacy optional async flush only (`ENABLE_LEGACY_SUPABASE_FLUSH=true`). SQLite (`db/voiceclone.sqlite3`) is the primary store; the live score line uses the local WebSocket.

**Do not use on this project:**
- **Sentry** — no error-tracking service in a 48-hour local build.
- **Firecrawl** — Context7 covers the library docs we need.
- **Figma / Stitch / Magic Patterns** — only for a first-pass dashboard layout if explicitly asked. Never mid-build.

**ralph-loop:** do not run autonomous loops during a time-boxed build. A loop that runs for forty minutes and lands in the wrong place costs more than the task.

## Skills to prefer
- `web-design-engineer`, `ui-styling`, `design-system` — dashboard and approval UI, constrained by `Design-System.md` (light theme only)
- `impeccable` — final polish pass, only after Testing.md Tier 1 passes
- `slides` — deck generation in the presentation phase
- `code-review` — solo build, no reviewer. Run it on `service/` before the demo.

## Out of scope — do not build
Training · multi-tenancy · auth · scale · cloud · Android · gRPC · real SMS/email gateway · dark mode
