# PRD — Voice Cloning Detection (Internal Hackathon)

**Scope: internal hackathon round only.** Not a production spec. Anything not demonstrable in a 10-minute presentation is out of scope.

## Problem
Attackers clone an executive's voice from seconds of audio and phone an employee to authorise a transfer. Caller ID and voice familiarity both fail. Detection must arrive *before* the approval, not after the call.

## What we build
A detection service inside the telephony path. Live call audio streams in; a fused risk score streams out; a high-value transaction approval is blocked and escalated to call-back verification when the score crosses threshold.

## Users (demo personas)
- **Fraud analyst** — watches the monitoring dashboard
- **Approver** — sits at the transaction screen, tries to approve a ₹40,00,000 transfer

## In scope
- Single-node Asterisk, two SIP extensions
- Three analysis layers: acoustic (AASIST), prosody (F0 + pause/rhythm), speaker consistency (ECAPA)
- Rule-based contextual enrichment
- Streaming fused risk score over WebSocket
- Monitoring dashboard + transaction approval surface
- Two thresholds: escalate, block
- Feature-only logging, no audio retention
- REST + WebSocket integration surface (Flask; no OpenAPI page)
- Four honest evaluations, including cross-method and codec-degraded

## Explicitly out of scope
Multi-tenancy · horizontal scale · authentication/RBAC · cloud deployment · Android client · model training from scratch · gRPC · real SMS/email gateway (mocked) · anything requiring more than one machine

## Success criteria (internal round)
1. Two live calls — genuine and cloned, same speaker — score separates visibly
2. Approval button locks before the caller finishes speaking; time-to-detection stated aloud
3. Four evaluation numbers on one slide, including the degraded ones
4. Every claim on a slide traceable to a measurement or a cited paper

## Non-goals we will state openly
Not production-ready. Not trained by us — published AASIST weights. Not validated on Indian languages beyond a small evaluation set.
