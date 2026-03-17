# WS-L Completion Record

Date: February 25, 2026  
Workstream: WS-L (SIP Edge Canary Orchestration)  
Status: Complete

---

## 1) Scope Closed

WS-L required:
1. Stage controller with controlled progression (`5 -> 25 -> 50 -> 100`).
2. Drain/freeze/rollback command path.
3. Destination probing and runtime state transition validation.
4. Machine-readable stage decision evidence.

Delivered:
1. `telephony/scripts/canary_stage_controller.sh`
2. `telephony/scripts/verify_ws_l.sh`
3. OpenSIPS edge migration path under `telephony/opensips/`
4. Decision evidence JSONL (`ws_l_stage_decisions.jsonl`)

---

## 2) OpenSIPS Shift Implemented

The SIP edge layer was moved from `telephony/kamailio` to:
1. `telephony/opensips/conf/opensips.cfg`
2. `telephony/opensips/conf/dispatcher.list`
3. `telephony/opensips/conf/address.list`
4. `telephony/opensips/conf/tls.cfg`
5. `telephony/opensips/certs/`

Compose service now runs:
1. `opensips` (container `talky-opensips`)

---

## 3) WS-L Gate and Decision Artifacts

Stage controller evidence:
1. `telephony/docs/phase_3/evidence/ws_l_stage_decisions.jsonl`
2. `telephony/docs/phase_3/evidence/ws_l_metrics_*.prom`

`verify_ws_l.sh` validates:
1. non-sequential stage guard
2. metrics gate reject path
3. staged progression path
4. freeze guard
5. dry-run rollback behavior
6. decision artifact structure

---

## 4) Runtime Rollback Path

Runtime rollback command path:
1. `opensips-cli -x mi ds_set_state i 2 <destination>`

Durable rollback path:
1. `KAMAILIO_CANARY_ENABLED=0`
2. `KAMAILIO_CANARY_PERCENT=0`
3. `KAMAILIO_CANARY_FREEZE=0`
4. OpenSIPS config validation (`opensips -C -f /etc/opensips/opensips.cfg`)

---

## 5) Exit Statement

WS-L is complete and ready for WS-M execution.
