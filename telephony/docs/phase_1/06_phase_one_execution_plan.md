# Phase 1 Execution Plan (Production Telephony Foundation)

Status note:
1. This WS-based execution plan is historical.
2. Active execution authority is `telephony/docs/phase_3/19_talk_lee_frozen_integration_plan.md`.
3. Use `telephony/docs/phase_3/20_status_against_frozen_talk_lee_plan.md` for current tracking.

Date prepared: February 23, 2026
Scope: Phase 1 only (foundation + parallel stack + measurable readiness)

---

## 1) Phase 1 Goal

Stand up a parallel, production-shaped telephony stack (Kamailio + rtpengine + FreeSWITCH) without breaking current live flows, and prove readiness with objective metrics.

This phase does **not** cut over all production traffic. It establishes:
- reliable call setup path
- secure signaling baseline
- RTP/media relay baseline
- transfer primitives validated
- low-latency baseline instrumentation
- rollback-safe canary routing

---

## 2) What Phase 1 Includes

1. Dedicated telephony workspace and docs (already created):
   - `telephony/` with component folders and runbooks
2. Staging deployment of:
   - Kamailio (SIP edge)
   - rtpengine (RTP relay)
   - FreeSWITCH (B2BUA/media app)
3. Python backend integration over existing FreeSWITCH bridge path.
4. Baseline transfer support validation (blind transfer and transfer signaling checks).
5. SLO-oriented monitoring for:
   - setup success
   - first-response latency
   - barge-in stop delay
   - transfer success
6. Canary-ready traffic control and rollback switch.

---

## 3) What Phase 1 Explicitly Excludes

1. Full tenant self-service SIP onboarding UI.
2. 100% traffic cutover.
3. Legacy path hard deletions.
4. Deep custom C/C++ module development (unless critical blocker).

---

## 4) Best-Practice Constraints (Non-Negotiable)

1. No big-bang migration.
2. Only small, self-contained changes per release artifact.
3. Automated deploy/test gates for every environment promotion.
4. One canary at a time.
5. Immediate rollback on SLO breach.

---

## 5) Workstream Plan

## WS-A: Telephony Infrastructure Bootstrap (Days 1-3)

Tasks:
1. Deploy Kamailio with dispatcher-enabled routing config templates.
2. Deploy rtpengine with in-kernel forwarding enabled path.
3. Deploy FreeSWITCH with ESL and SIP profile templates.
4. Keep deployment artifacts in:
   - `telephony/deploy/docker/`
   - later mirrored to helm/terraform as needed.

Deliverables:
1. Services start/health checks green.
2. SIP OPTIONS probes pass through edge.
3. RTP relay receives and forwards test media.

Acceptance gate:
- 100 synthetic INVITE transactions complete in staging with >= 99% setup success.


## WS-B: Security and Signaling Baseline (Days 2-4)

Tasks:
1. Enable TLS transport path in Kamailio configs for protected trunks.
2. Apply IP ACL and trust policy baseline in Kamailio (permissions rules).
3. Add flood-protection checks (pike module baseline thresholds).
4. Add request-type rate limiting (ratelimit module) for INVITE/REGISTER spikes.
5. Restrict FreeSWITCH event socket ingress to allowed sources.

Deliverables:
1. TLS-only route profile for selected staging trunks.
2. ACL policy file with explicit allow/deny controls.
3. Flood test demonstrates offending source is blocked and logged.

Acceptance gate:
- Security smoke checklist passes (ACL, TLS path, ESL source restriction).


## WS-C: Call Control + Transfer Baseline (Days 3-5)

Tasks:
1. Validate FreeSWITCH ESL dual-connection stability under load.
2. Validate blind transfer command path (`uuid_transfer`) end-to-end.
3. Validate hold/talk signaling (`uuid_phone_event`) as control primitive.
4. Add transfer event telemetry to Python logs/call events.

Deliverables:
1. Transfer test scripts (success/failure cases).
2. Transfer outcome metrics in monitoring output.

Acceptance gate:
- Blind transfer success >= 99% across 200 staged attempts.


## WS-D: Media Bridge + Latency Baseline (Days 4-6)

Tasks:
1. Validate FreeSWITCH audio fork websocket path stability.
2. Standardize sample-rate and frame-size policy by route type.
3. Profile queue/backpressure behavior in Python voice pipeline.
4. Define and record baseline P50/P95 latencies:
   - STT first transcript
   - LLM first token
   - TTS first chunk
   - end-to-end response start

Deliverables:
1. Baseline latency report (`phase1_baseline_latency.md`).
2. Tuned defaults for frame sizes/chunking in staging config.

Acceptance gate:
- P95 response start <= 1200ms in staging synthetic scenario.


## WS-E: Canary + Rollback Control (Days 5-7)

Tasks:
1. Add traffic split control (5% canary route).
2. Define SLO-driven rollback triggers.
3. Ensure rollback is one-step route reversal.
4. Run controlled canary for fixed duration with single release.

Deliverables:
1. Canary runbook execution log.
2. Rollback drill report.

Acceptance gate:
- Canary can be enabled/disabled in <= 2 minutes with no downtime.

---

## 6) Phase 1 Day-by-Day Execution

Day 1:
1. Finalize environment contracts and secrets.
2. Bring up telephony containers in staging.
3. Verify basic health and SIP reachability.

Day 2:
1. Enable dispatcher route sets and target pools.
2. Configure pike thresholds and permissions ACL baseline.
3. Enable TLS route profile for protected trunk tests.

Day 3:
1. Connect FreeSWITCH ESL and validate event ingestion.
2. Run outbound and inbound synthetic call tests.
3. Start call-setup success dashboard.

Day 4:
1. Validate media fork path with live PCM loopback.
2. Measure latency baseline and queue pressure.
3. Fix obvious frame/chunk mismatches.

Day 5:
1. Validate transfer primitives (`uuid_transfer`, hold/talk events).
2. Add transfer telemetry and error buckets.
3. Run 200-transfer reliability test.

Day 6:
1. Run canary at 5% staged traffic equivalent.
2. Compare canary/control metrics.
3. Exercise rollback and verify recovery time.

Day 7:
1. Publish Phase 1 exit report.
2. Sign off on Phase 2 entry criteria.

---

## 7) Phase 1 Exit Criteria

All must be true:

1. Setup success >= 99% in staging synthetic suite.
2. Blind transfer success >= 99% in controlled run.
3. P95 response start <= 1200ms in staging baseline.
4. Barge-in interruption path confirmed stable.
5. Canary and rollback playbooks validated.
6. No unresolved P1/P2 defects open.

---

## 8) Rollback Criteria (Immediate)

Rollback if any occurs during canary:

1. Setup success < 98.5% for 10+ minutes.
2. P95 response start > 1500ms for 15+ minutes.
3. Transfer success < 95%.
4. One-way/no-audio incidents exceed threshold.

Rollback steps:
1. Disable canary route split.
2. Route 100% to legacy telephony path.
3. Freeze release and capture evidence for RCA.

---

## 9) Why This Plan Is Up-To-Date and Best-Practice-Based

This plan is anchored to current official docs and standards behavior, not assumptions.
Validation date for references: February 23, 2026.

1. Kamailio dispatcher supports active probing and destination health state (`ds_ping_interval`, probing thresholds/modes); this directly drives WS-A active gateway management.
2. Kamailio pike + ratelimit provide two complementary protections (per-source flood detection + per-method throttling); this is why WS-B includes both controls up front.
3. Kamailio permissions supports trusted-source ACL evaluation (`allow_trusted`, source-address checks); this is why WS-B establishes explicit trust policy before traffic growth.
4. Kamailio TLS docs define client verification modes (`verify_client`) and certificate handling; this is why TLS profile hardening is mandatory in WS-B.
5. rtpengine documents kernel offload for low CPU and better concurrency, with automatic userspace fallback; this is why WS-A/WS-D target kernel mode without risking hard failures.
6. FreeSWITCH mod_event_socket docs recommend ACLs and default local-only control for safer ESL exposure; this drives WS-B ESL ingress restrictions.
7. FreeSWITCH `uuid_transfer` docs include behavior/caveats for bridged calls; this is why WS-C has explicit transfer reliability gates rather than assuming transfer correctness.
8. Google SRE canary guidance recommends reproducible automation, small deployment units, and gradual exposure; this is exactly why WS-E uses 5% canary + strict rollback triggers.

---

## 10) References (Official / Primary)

1. Kamailio dispatcher module (stable docs):
   - https://kamailio.org/docs/modules/stable/modules/dispatcher.html
2. Kamailio pike module (stable docs):
   - https://www.kamailio.org/docs/modules/stable/modules/pike.html
3. Kamailio ratelimit module (stable docs):
   - https://kamailio.org/docs/modules/stable/modules/ratelimit.html
4. Kamailio permissions module (stable docs):
   - https://www.kamailio.org/docs/modules/stable/modules/permissions.html
5. Kamailio TLS module (stable docs):
   - https://www.kamailio.org/docs/modules/stable/modules/tls.html
6. rtpengine usage (mr12.4 docs, in-kernel forwarding + fallback):
   - https://rtpengine.readthedocs.io/en/mr12.4/usage.html
7. FreeSWITCH mod_event_socket docs:
   - https://developer.signalwire.com/freeswitch/FreeSWITCH-Explained/Modules/mod_event_socket_1048924
8. FreeSWITCH Event Socket Outbound docs:
   - https://developer.signalwire.com/freeswitch/FreeSWITCH-Explained/Client-and-Developer-Interfaces/Event-Socket-Library/Event-Socket-Outbound_3375460/
9. FreeSWITCH `uuid_transfer` command reference (mod_commands):
   - https://developer.signalwire.com/freeswitch/confluence-to-docs-redirector/display/FREESWITCH/mod_commands
10. Google SRE Workbook - Canarying Releases:
   - https://sre.google/workbook/canarying-releases/

---

## 11) Immediate Next Action

Start WS-A and WS-B in staging while preserving current backend runtime paths unchanged.
