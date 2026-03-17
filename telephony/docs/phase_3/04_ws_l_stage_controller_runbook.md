# WS-L Stage Controller Runbook

Date: February 25, 2026  
Workstream: WS-L (SIP Edge Canary Orchestration)

---

## Purpose

Operate staged SIP-edge canary rollout and rollback using:
1. `telephony/scripts/canary_stage_controller.sh`
2. `telephony/scripts/canary_set_stage.sh`
3. `telephony/scripts/canary_freeze.sh`
4. `telephony/scripts/canary_rollback.sh`

The SIP edge stack is now located under:
1. `telephony/opensips/`
2. `telephony/deploy/docker/docker-compose.telephony.yml` service `opensips`

---

## Stage Model

Allowed rollout stages:
1. `0`
2. `5`
3. `25`
4. `50`
5. `100`

Progression rules:
1. Sequential only (`0 -> 5 -> 25 -> 50 -> 100`) unless `--force`.
2. Frozen state blocks non-zero promotion unless `--force`.
3. Rollback command always targets stage `0`.

---

## Commands

Check status:

```bash
bash telephony/scripts/canary_stage_controller.sh status telephony/deploy/docker/.env.telephony
```

Advance to next stage (with gates):

```bash
bash telephony/scripts/canary_stage_controller.sh advance telephony/deploy/docker/.env.telephony \
  --reason "canary promote after green SLO window"
```

Set explicit stage:

```bash
bash telephony/scripts/canary_stage_controller.sh set 25 telephony/deploy/docker/.env.telephony \
  --reason "manual stage alignment"
```

Emergency rollback:

```bash
bash telephony/scripts/canary_stage_controller.sh rollback telephony/deploy/docker/.env.telephony \
  --reason "SLO breach rollback"
```

Dry-run decision path:

```bash
bash telephony/scripts/canary_stage_controller.sh advance telephony/deploy/docker/.env.telephony \
  --reason "dry-run validation" \
  --dry-run
```

---

## Evidence

Stage decisions are written to:
1. `telephony/docs/phase_3/evidence/ws_l_stage_decisions.jsonl`

Metrics snapshots (when gates are evaluated) are written to:
1. `telephony/docs/phase_3/evidence/ws_l_metrics_*.prom`

Use `verify_ws_l.sh` for end-to-end verification:

```bash
bash telephony/scripts/verify_ws_l.sh telephony/deploy/docker/.env.telephony
```

---

## OpenSIPS Runtime Control

Runtime rollback state transition uses OpenSIPS MI:
1. `opensips-cli -x mi ds_set_state i 2 <destination>`

Configuration health check:
1. `opensips -C -f /etc/opensips/opensips.cfg`

---

## Operational Notes

1. Keep `verify_ws_k.sh` green before stage promotions.
2. Use `--skip-gates` only for controlled emergency/maintenance operations.
3. Every stage action must include a concrete `--reason` value.
4. Preserve decision JSONL artifacts for post-incident RCA and audit.
