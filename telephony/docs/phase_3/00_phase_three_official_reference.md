# Phase 3 Official Reference Baseline

Date verified: February 25, 2026  
Scope: Production canary rollout, SIP/media resiliency, transfer reliability, rollback automation, and cutover governance.

---

## 1) Phase 3 Outcome

Phase 3 must deliver:
1. Controlled production traffic migration with explicit go/no-go gates.
2. Stable SIP routing and media behavior during node failures and partial outages.
3. Transfer and long-call behavior that remains deterministic under load.
4. SLO-driven rollback rules that are automated and auditable.

---

## 2) Official Sources and Why They Matter

| Area | Official Source | Key Capability Used in Phase 3 |
|---|---|---|
| SIP edge dispatch | OpenSIPS `dispatcher` module docs | Runtime destination management, probing, and weighted/hashed routing |
| Media relay control | OpenSIPS `rtpengine` module docs | Offer/answer/delete control and media relay policy hooks |
| RTP engine behavior | Sipwise rtpengine docs | Kernel vs userspace forwarding model, control-plane operation |
| FreeSWITCH runtime control | FreeSWITCH `mod_event_socket` docs | Deterministic external call control via ESL |
| FreeSWITCH outbound control | FreeSWITCH Event Socket Outbound docs | Inbound call handoff with parked channel and async command model |
| FreeSWITCH dynamic config | FreeSWITCH `mod_xml_curl` docs | Dynamic profile/dialplan retrieval with strict timeout handling |
| Transfer semantics | FreeSWITCH `mod_dptools: transfer` docs | Correct transfer behavior and dialplan-jump limitations |
| SIP server discovery | IETF RFC 3263 | DNS-based SIP server resolution and failover strategy |
| Core SIP behavior | IETF RFC 3261 | Baseline transaction/dialog behavior and interoperability requirements |
| Call end diagnostics | IETF RFC 3326 | Reason header for structured termination causes |
| Session longevity | IETF RFC 4028 | Session timer behavior for long-running dialogs |
| API errors | IETF RFC 9457 | Standard `application/problem+json` response format |
| JWT hardening | IETF RFC 8725 | BCP rules for secure JWT validation and key handling |
| Metrics naming | Prometheus naming best practices | Stable metric/label model for SLO dashboards and alerts |
| Recording rules | Prometheus recording rules best practices | Efficient rollup for alerting and canary comparisons |
| Alert routing | Alertmanager docs | Grouping, dedup, inhibition, and routing policies |
| Startup dependencies | Docker Compose startup-order docs | Health-gated service dependencies and deterministic boot sequencing |

---

## 3) Extracted Technical Facts (Official)

## 3.1 OpenSIPS and RTPengine

1. `dispatcher` supports probing and runtime state transitions for destinations; this is required for safe canary and drain operations.
2. Dispatcher algorithm behavior is not equivalent across all modes; canary rollout must use a mode with deterministic reload/state-transition semantics.
3. `rtpengine` module provides explicit media lifecycle control (`offer` / `answer` / `delete`) and must be consistently tied to SIP transaction flow.
4. rtpengine can operate with kernel acceleration and fallback to userspace forwarding; rollout checks must validate both paths.

## 3.2 FreeSWITCH Runtime Control

1. `mod_event_socket` is powerful and must be locked down (bind scope, ACLs, credentials/TLS) before production exposure.
2. Event Socket Outbound starts with a parked channel and requires async command handling for robust control during media operations.
3. `mod_xml_curl` enables dynamic runtime config, but poor timeout control can directly impact live call handling.
4. `transfer` is a dialplan transfer primitive; production attended/blind transfer behavior requires explicit bridge/orchestration logic.

## 3.3 Standards and API Semantics

1. RFC 3263 is the baseline for DNS-driven SIP target resolution and failover behavior.
2. RFC 3326 provides interoperable reason signaling for diagnostics and analytics pipelines.
3. RFC 4028 session timers must be aligned with gateway and B2BUA behavior to avoid long-call drops.
4. RFC 9457 keeps error shape stable across API and automation clients.
5. RFC 8725 avoids weak JWT patterns (algorithm confusion and unsafe key handling).

## 3.4 Observability and Rollout Safety

1. Prometheus metric naming and label discipline directly determines query stability and alert quality.
2. Recording rules should precompute expensive canary-vs-baseline comparisons for low-latency alerting.
3. Alertmanager grouping and dedup must prevent alert storms during controlled failover drills.
4. Docker Compose service startup ordering is not readiness by default; health checks are mandatory for deterministic start.

---

## 4) Phase 3 Design Rules (Derived from Official Sources)

1. No production traffic increase without passing SLO gates at each canary stage.
2. SIP edge routing must support both weighted progression and immediate drain/rollback.
3. Media path verification must include packet-loss/jitter checks and transfer reliability checks.
4. Every canary step must emit machine-readable evidence (metrics snapshot + decision record).
5. Rollback must be one command path, tested before production traffic > 25%.
6. Public/tenant APIs continue to use RFC 9457 error shape; auth hardening follows RFC 8725.

---

## 5) Phase 3 Evidence Requirements

1. Official-doc traceability:
   - Each WS-K..WS-O deliverable references an item in this file.
2. Deterministic canary:
   - Stage progression only occurs with explicit SLO pass evidence.
3. Failure handling:
   - Simulated component outage demonstrates auto-recovery or controlled rollback.
4. Transfer quality:
   - Blind and attended transfer success rates meet target under canary load.
5. Observability:
   - Alerts and dashboards are actionable, deduplicated, and drill-tested.

---

## 6) Official Links

1. OpenSIPS dispatcher module:
   - https://opensips.org/html/docs/modules/3.4.x/dispatcher.html
2. OpenSIPS rtpengine module:
   - https://opensips.org/html/docs/modules/3.4.x/rtpengine.html
3. rtpengine overview:
   - https://rtpengine.readthedocs.io/en/mr13.4/overview.html
4. rtpengine usage:
   - https://rtpengine.readthedocs.io/en/mr13.4/usage.html
5. FreeSWITCH mod_event_socket:
   - https://developer.signalwire.com/freeswitch/FreeSWITCH-Explained/Modules/mod_event_socket_1048924/
6. FreeSWITCH Event Socket Outbound:
   - https://developer.signalwire.com/freeswitch/FreeSWITCH-Explained/Client-and-Developer-Interfaces/Event-Socket-Library/Event-Socket-Outbound_3375460/
7. FreeSWITCH mod_xml_curl:
   - https://developer.signalwire.com/freeswitch/FreeSWITCH-Explained/Modules/mod_xml_curl_1049001/
8. FreeSWITCH mod_dptools transfer:
   - https://developer.signalwire.com/freeswitch/FreeSWITCH-Explained/Modules/mod-dptools/6586616/
9. RFC 3261:
   - https://www.rfc-editor.org/rfc/rfc3261
10. RFC 3263:
    - https://www.rfc-editor.org/rfc/rfc3263
11. RFC 3326:
    - https://www.rfc-editor.org/rfc/rfc3326
12. RFC 4028:
    - https://www.rfc-editor.org/rfc/rfc4028
13. RFC 9457:
    - https://www.rfc-editor.org/rfc/rfc9457
14. RFC 8725:
    - https://www.rfc-editor.org/rfc/rfc8725
15. Prometheus metric naming:
    - https://prometheus.io/docs/practices/naming/
16. Prometheus recording rules:
    - https://prometheus.io/docs/practices/rules/
17. Alertmanager docs:
    - https://prometheus.io/docs/alerting/latest/alertmanager/
18. Docker Compose startup order:
    - https://docs.docker.com/compose/how-tos/startup-order/
