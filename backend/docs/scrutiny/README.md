# Production scrutiny log

This directory captures the running audit of what's ready for production
and what isn't, plus the trail of remediations as they ship.

The source plan lives at
`~/.claude/plans/zazzy-wibbling-stardust.md` (tiered: T0 blockers → T1
high → T2 medium → T3 nice-to-have). Each remediation ships against one
of those tier items; this directory records what was actually built.

## Start here

→ [**`2026-04-27-production-readiness-overview.md`**](./2026-04-27-production-readiness-overview.md) — full programme rollup. Non-technical summary up top, tier-by-tier technical detail, operator runbook, complete file manifest, what's still open. **Read this first.**

## Index (by date)

| Date       | File                                                                         | What it covers                                          |
|------------|------------------------------------------------------------------------------|---------------------------------------------------------|
| 2026-04-27 | [`2026-04-27-production-readiness-overview.md`](./2026-04-27-production-readiness-overview.md) | **Full programme rollup — every tier, technical + non-technical** |
| 2026-04-25 | [`2026-04-25-tier-0-blockers.md`](./2026-04-25-tier-0-blockers.md)           | Tier 0 pre-launch blockers (T0.1–T0.5) — all five shipped |
| 2026-04-25 | [`2026-04-25-t1-2-global-concurrency.md`](./2026-04-25-t1-2-global-concurrency.md) | T1.2 — Redis-backed cluster-wide concurrency cap        |
| 2026-04-25 | [`2026-04-25-t1-5-callee-timezone.md`](./2026-04-25-t1-5-callee-timezone.md)           | T1.5 — Callee-timezone-aware business-hours check (TCPA) |
| 2026-04-25 | [`2026-04-25-t1-1-tenant-ai-credentials.md`](./2026-04-25-t1-1-tenant-ai-credentials.md) | T1.1 — Per-tenant encrypted AI provider credentials     |
| 2026-04-25 | [`2026-04-25-t1-3-resilient-providers.md`](./2026-04-25-t1-3-resilient-providers.md)     | T1.3 — Resilient STT + TTS wrappers (primary/secondary + circuit breaker) |
| 2026-04-25 | [`2026-04-25-t2-1-t2-5-dnc-and-e164.md`](./2026-04-25-t2-1-t2-5-dnc-and-e164.md)         | T2.1 — DNC list service + CRUD;  T2.5 — Internationalised E.164 normalisation |
| 2026-04-25 | [`2026-04-25-t2-2-t2-3-t2-4-ops-hardening.md`](./2026-04-25-t2-2-t2-3-t2-4-ops-hardening.md) | T2.2 — Streams-based dialer queue; T2.3 — Sentry init; T2.4 — Redis durability probe |
| 2026-04-27 | [`2026-04-27-credential-wiring-and-legacy-audit.md`](./2026-04-27-credential-wiring-and-legacy-audit.md) | T1.1 follow-up — orchestrator wired to CredentialResolver; T2.6 — legacy persona audit |
| 2026-04-27 | [`2026-04-27-t1-3-t2-2-opt-in-integration.md`](./2026-04-27-t1-3-t2-2-opt-in-integration.md) | T1.3 — STT/TTS failover env-flag opt-in;  T2.2 — DIALER_QUEUE_BACKEND factory |

## How to read this log

Each entry documents:
- **What was broken** — the production gap, with file:line citations
- **What shipped** — migrations, modules, endpoints, tests
- **How to verify** — reproducible commands that prove the fix works
- **What's next** — deferred work tracked against later tiers

Every entry is additive — we never edit a historical entry in place.
New facts (follow-up issues, post-deploy findings) go into a new file
with a later date.
