# Telephony Program Overview

## Objective

Build a production-grade voice system that supports:
- Multi-tenant BYOS SIP trunks
- Low-latency AI conversations
- Call transfers (blind and attended)
- DTMF, barge-in, recording, and call analytics
- Safe scaling and predictable operations

## Recommended System (Current Decision)

- SIP Edge/SBC: Kamailio (or OpenSIPS)
- RTP relay: rtpengine
- B2BUA/media app: FreeSWITCH
- AI/control plane: existing Python backend (`backend/`)
- Storage/state: PostgreSQL + Redis

## Why this design

- Telecom packet handling remains in proven C/C++ engines.
- AI and business logic remain in Python for velocity.
- Clear separation of concerns lowers regression risk.
- Supports modular per-customer SIP onboarding.

## Program Constraints

- Do not break existing working call paths during migration.
- No big-bang rewrite.
- Every migration phase must be reversible.

## North Star SLO Targets

- P95 one-way AI response start latency: <= 900 ms
- P95 barge-in stop reaction: <= 250 ms
- Call setup success: >= 99.5%
- Unexpected call drop rate: < 0.3%
- Transfer success (blind/attended): >= 99.0%
