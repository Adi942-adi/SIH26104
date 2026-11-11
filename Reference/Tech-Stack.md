# Tech Stack (Internal Hackathon)

| Layer | Choice | Why this and not the alternative |
|---|---|---|
| Softswitch | **Asterisk** + PJSIP | More documentation than FreeSWITCH; you debug this at 2am |
| Audio tap | **AudioSocket** (`app_audiosocket`) | Normalises to fixed 8 kHz slin16 regardless of negotiated codec; ChanSpy inherits the codec and forces transcoding |
| Acoustic model | **AASIST**, published weights (MIT) | 0.83% on 2019 LA published; no training in 48 h |
| Prosody | praat-parselmouth + librosa | Praat is the reference implementation for F0/jitter/shimmer |
| Speaker | **ECAPA-TDNN** via SpeechBrain (Apache 2.0) | 192-dim, well documented; Resemblyzer as fallback |
| Runtime | PyTorch (CPU) | Weights are PyTorch; ONNX Runtime is a documented edge-deployment option, not built |
| OS | **Windows 11 native, no WSL** | Asterisk is deferred; the file feeder covers the demo path, and everything else runs natively |
| Codec tooling | ffmpeg (Gyan full build) | `libgsm`, `pcm_mulaw`, `libopus`; `libopencore_amrnb` if the build has it |
| Service | **Flask + flask-sock** (REST + WebSocket) | Minimal server with a plain WebSocket route; it does not generate an OpenAPI page |
| Store | **SQLite** (`db/voiceclone.sqlite3`) | Zero network dependency for the demo; Supabase Postgres remains an optional legacy async flush behind `ENABLE_LEGACY_SUPABASE_FLUSH` |
| Dashboard | React + wavesurfer.js | Light theme only — see `Design-System.md` |
| Analysis/plots | MATLAB | Signal-level artifact figures + DET curves; NOT the model |

## Licences — all non-commercial, fine for internal round, state it on a slide
ASVspoof 2019/2021 CC BY 4.0 · ASVspoof 5 ODC-By · **In-the-Wild CC BY-NC 4.0** · AASIST MIT · ECAPA Apache 2.0 · **XTTS-v2 CPML (non-commercial)** · **F5-TTS CC-BY-NC** · Coqui MPL-2.0

## Deliberately not used
FreeSWITCH · gRPC · Docker/K8s · cloud inference · any auth framework · any state manager beyond React state
