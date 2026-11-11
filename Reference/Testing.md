# Testing.md (Internal Hackathon)

No test framework. No coverage target. These are the checks that stop the demo failing on stage — run them, tick them, move on.

## Tier 1 — must pass before you sleep on day 1

| # | Check | How | Pass |
|---|---|---|---|
| T0 | ffmpeg codecs present | `ffmpeg -codecs \| Select-String "gsm\|mulaw\|opus"` | libgsm, pcm_mulaw, libopus listed |
| T1 | File feeder pacing | `py -c "from service.file_feeder import feed; feed('demo/test.wav', print)"` | ~50 chunks/sec of 320 B. **Instant = pacing sleep missing, fix now** |
| T2 | **Sample rate path** | Log the array shape entering AASIST | 16 kHz, correct length. **This is the #1 defect.** |
| T3 | Baseline reproduction | AASIST on 2019 LA eval | EER near 0.83%. Far off = broken pipeline, not a bad model |
| T4 | Codec transcode sanity | Listen to 5 files after G.711 and GSM round-trip | Speech, not noise. No agent can do this for you |
| T5 | End-to-end | Live call → dashboard score moves | Score updates within ~1 s of speech |
| T6 | Approval lock | Cross the escalate threshold | Button locks, call-back prompt appears |
| T7 | No secrets in tracked files | `Select-String -Path *.json,*.js,*.py -Pattern "SUPABASE_ANON|service_role"` | Only `.env` holds keys |

## Tier 2 — before rehearsal

| # | Check | Pass |
|---|---|---|
| T8 | Genuine call stays low | Score flat and below escalate for a full real call |
| T9 | Cloned call rises | Crosses escalate; note the time — this number goes on a slide |
| T10 | Codec toggle | Switch to GSM, rerun T9. Score still separates (expect it weaker — GSM re-vocodes both voices) |
| T11 | Score smoothing | Line is readable, not flapping |
| T12 | Restart resilience | Kill the service mid-call, restart, place a new call. Recovers without reboot |
| T13 | Four evaluations | All four numbers computed, each traced to a saved scores CSV |
| T14 | REST smoke | `POST /api/analyze` with `demo/human_voice.wav` and `demo/bot_8s.wav`; `GET /health`, `/api/calls`, `/api/alerts` | 200s, and bot risk > human risk |

## Tier 3 — stage readiness (all manual)

- [ ] Five cold end-to-end runs, start to finish
- [ ] Full run on battery, lid closed and reopened
- [ ] Full run on the phone hotspot, not home wifi
- [ ] Backup screen recording on the desktop, one click away
- [ ] Every slide number opened at its primary source
- [ ] Q&A drill said out loud, not read

## Known-weak, do not "fix" under time pressure
- GSM separation is expected to be weaker than G.711 — the codec re-vocodes both genuine and cloned audio. Narrate it, don't chase it.
- Speaker layer is noisy on narrowband by design. Supporting evidence, not a gate.
- Prosody has the weakest standalone number of the three. That is fine and it is honest.
