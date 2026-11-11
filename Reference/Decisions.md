# Decisions.md

Append-only. Each entry: what, why, what it costs. Never delete an entry — a reversed decision gets a new entry.

---

**D1 — Enterprise/PBX deployment, not a phone app.**
The statement describes employees, executives and wire transfers. Android has blocked third-party in-call audio access since Android 10, so a Truecaller-style app cannot see PSTN call audio. The PBX is where the audio legally and technically lives.
*Cost:* no consumer story in the demo. Roadmap slide only.

**D2 — Published AASIST weights, no training.**
Training with codec augmentation is multi-day work. A half-trained detector is worse than none. Our contribution is the multi-layer fusion, the telephony pipeline, and honest evaluation.
*Cost:* must state this plainly and early, or it reads as a gap when caught.

**D3 — Three analysis layers, not one.**
The statement names multi-layer analysis as its first component and names single-layer detection as the existing failure. Three bars resolving into one score is also the most legible thing we can put on a dashboard.
*Cost:* ~5 extra hours. Cut prosody first if behind.

**D4 — Jitter and shimmer weighted 0.**
Research shows both are corrupted at 8 kHz by µ-law quantisation and jitter buffers. Including them would add noise while appearing rigorous.
*Cost:* a weaker prosody layer. Honest.

**D5 — Speaker reference enrolled through the codec path.**
Wideband enrolment against narrowband live audio is the documented failure mode; embeddings lose 3–5× EER on narrowband.
*Cost:* the enrolment step needs the codec pipeline working first.

**D6 — Hand-set fusion weights.**
No time and no validation data to learn them. Weights we can justify beat weights we cannot defend.
*Cost:* suboptimal fusion. Defensible.

**D7 — SQLite, no audio columns.** *(Reversed — see D11.)*

**D11 — Supabase Postgres, replacing SQLite.**
Realtime subscriptions remove the WebSocket fan-out we would otherwise hand-build, and the Supabase MCP lets an agent create the schema directly. Privacy is not a pillar of this demo — it's one honest line on one slide.
*Cost:* a network dependency. Mitigated by keeping the **local WebSocket as the primary path for the live score line**, with Supabase carrying persistence and the approval UI. If venue wifi dies, the score still moves.

**D8 — Light theme only.**
Projectors wash out dark themes; the score line must be readable from the back row. Also matches the banking surface we claim to integrate with.
*Cost:* none.

**D9 — AudioSocket over ChanSpy.**
AudioSocket normalises to fixed 8 kHz slin16 regardless of negotiated codec; ChanSpy inherits the codec and forces CPU-heavy transcoding, and needs an external trigger with race conditions.
*Cost:* if AudioSocket resists, ChanSpy is the documented fallback.

**D10 — Report cross-method and codec-degraded numbers on the main slide.**
Every other team will show one flattering figure. The gap is our credibility.
*Cost:* our headline number looks worse. That is the point.

**D12 — SQLite is the primary store; Supabase is legacy-optional.** *(Reverses D11.)*
The demo must survive a dead venue network. Persistence now uses a local SQLite file (`db/voiceclone.sqlite3`) written by a single buffered flush thread; the dashboard reads it through REST. The old Supabase flush remains available behind `ENABLE_LEGACY_SUPABASE_FLUSH=true` but is off by default and is never on the critical path.
*Cost:* no hosted realtime subscriptions; the local WebSocket + REST polling carry the live view. Acceptable for a single-machine demo.
