# OpenSIPS Migration Plan (Kamailio -> OpenSIPS)

Date: February 25, 2026  
Owner: Telephony Platform  
Scope: SIP edge only (media stays in FreeSWITCH + rtpengine)

---

## Objective

Replace Kamailio edge runtime with OpenSIPS while retaining all implemented platform features:
1. TLS ingress
2. ACL and abuse controls
3. Dispatcher-based stable/canary routing
4. Progressive canary orchestration
5. Runtime rollback and freeze controls
6. Existing WS-A to WS-L verification model

---

## Feature Mapping

Existing capability -> OpenSIPS equivalent:
1. SIP edge process -> `opensips` service
2. Dispatcher selection -> `dispatcher` module (`ds_select_dst`)
3. Probing state -> `ds_ping_interval`, `ds_probing_threshold`, `ds_inactive_threshold`
4. Runtime state change -> `opensips-cli -x mi ds_set_state`
5. Canary probability -> `cfgutils` (`rand_set_prob`, `rand_event`)
6. ACL trust list -> `permissions` (`allow_source_address`)
7. Flood/rate controls -> `pike` + `ratelimit`
8. TLS termination -> `proto_tls` + `tls_mgm`

---

## Execution Steps

1. Filesystem migration:
   - rename `telephony/kamailio` -> `telephony/opensips`
   - rename module path `telephony/modules/kamailio` -> `telephony/modules/opensips`
2. Compose/service migration:
   - service name `opensips`
   - container `talky-opensips`
   - config mount `/etc/opensips/opensips.cfg`
3. Script migration:
   - update WS-A..WS-L scripts to target `opensips`
   - update runtime rollback command to MI `ds_set_state`
4. Verification migration:
   - add `telephony/scripts/verify_ws_l.sh`
   - update static and integration test coverage
5. Evidence migration:
   - write and retain `ws_l_stage_decisions.jsonl`
   - track stage-gate metrics snapshots

---

## Risk Controls

1. Keep canary default at `0%` until verify suite is green.
2. Keep rollback one-command path operational before first non-zero stage.
3. Fail closed on gate check errors (no promotion if metrics unavailable).
4. Preserve dry-run path for safe validation in non-production environments.

---

## Acceptance

Migration is accepted when:
1. `verify_ws_a.sh` to `verify_ws_l.sh` pass for target environment.
2. Stage progression and rollback paths are validated with evidence artifacts.
3. Phase 3 WS-L checklist is closed.
