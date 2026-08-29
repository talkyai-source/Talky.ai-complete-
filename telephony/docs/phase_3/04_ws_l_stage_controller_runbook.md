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
2. `100`

Progression rules:
1. The dedicated DID is binary (`0 -> 100`); cohort percentages are not SIP sampling stages.
2. Frozen state blocks every non-zero promotion. `--force` cannot bypass freeze, validation, or metrics.
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
bash telephony/scripts/canary_stage_controller.sh set 100 telephony/deploy/docker/.env.telephony \
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

Supply the metrics credential only through the process environment (normally
from the host secret manager), never as a command-line argument:

```bash
export TELEPHONY_METRICS_TOKEN="$(read-from-approved-secret-manager)"
```

The controller passes it to curl through a mode-0600 temporary header file,
deletes that file immediately after the fetch, and never writes the token to
the decision log, metrics snapshot, or console.

Before a promotion, configure one exact evidence identity in both the backend
metrics process and the controller process. Order is mandatory: build the
immutable candidate artifact and record its digest; generate a new UUIDv4 run
ID; put both into the route metadata and signed release manifest; freeze,
deploy, and verify that exact candidate; only then record the UTC start. The
start must precede every call or runtime drill intended as evidence:

```bash
export TELEPHONY_CANARY_TENANT_ID="<approved-tenant-uuid>"
export TELEPHONY_CANARY_CONFIG_ID="<approved-pinned-inbound-config-uuid>"
export TELEPHONY_CANARY_DID="<approved-7-to-15-digit-did>"
export TELEPHONY_CANARY_CANDIDATE_DIGEST="sha256:<64-lowercase-hex-artifact-digest>"
export TELEPHONY_CANARY_RUN_ID="$(python -c 'import uuid; print(uuid.uuid4())')"
export TELEPHONY_CANARY_GATE_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

`TELEPHONY_CANARY_DID` must be digits only and must exactly match
`OPENSIPS_CANARY_DID` in `.env.telephony`. The backend filters durable call and
transfer evidence by that tenant, normalized called DID, and pinned route
snapshot config. Its scope hash covers tenant, config, DID, frozen candidate
digest, UUIDv4 run ID, and the exact gate-start timestamp. The controller
independently derives that hash and rejects a missing/mismatched identity,
baseline, stale scrape, or stale latest-call timestamp. Runtime-policy evidence
is additionally restricted to a
version containing an inbound route whose regex matches the dedicated DID and
whose durable route metadata links that route to the same DID and pinned
inbound config:

```json
{
  "canary_scope": {
    "did": "<approved-7-to-15-digit-did>",
    "inbound_config_id": "<approved-pinned-inbound-config-uuid>",
    "candidate_digest": "sha256:<64-lowercase-hex-artifact-digest>",
    "run_id": "<same-uuidv4-as-TELEPHONY_CANARY_RUN_ID>"
  }
}
```

Set this metadata through the tenant route-policy API before freezing the
candidate. A missing linkage, a mismatched DID/config/candidate/run, or a route
regex that does not select the DID produces zero eligible runtime evidence.

The canary query is not a rolling-window or absolute-counter gate. Its zero
baseline is `TELEPHONY_CANARY_GATE_STARTED_AT`: calls and call legs must be
created at/after it, call samples are `COUNT(DISTINCT calls.id)`, and runtime
attempts are distinct non-null request IDs created at/after it. A run older than
six hours is invalid. The controller also requires the latest scoped call to be
recent (default 15 minutes) and the metrics refresh to be recent (default 60
seconds). Therefore the initial `0 -> 100` decision cannot consume activation,
rollback, or call evidence from an earlier run.

Current call rows do not carry candidate/run metadata. Candidate binding is
therefore an operational invariant, not a per-row database claim: the signed
digest must already be deployed and held frozen before recording the run start,
and no deploy/config mutation is allowed until the decision completes. Any
mutation invalidates the run; generate a new run ID and later start instead of
reusing its evidence. The scope hash makes a collector/controller mismatch fail
closed, while the exact post-start SQL prevents historical rows from counting.

Leave all six environment values unset while no canary has been approved. An
invalid/future/stale scope or any partial query failure publishes scrape failure
with all evidence gauges at zero and keeps promotion unavailable. Emergency
rollback remains available without an evidence gate.

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
2. Stage increases have no metric-gate bypass. `--dry-run` records a
   simulation and never mutates the env file or runtime.
3. The controller first requires the exact tenant + pinned inbound config +
   dedicated DID + frozen candidate + unique run + start identity. Global,
   pre-start, duplicate-call, or another run's traffic cannot satisfy the
   setup/latency/transfer gates.
4. The controller requires at least
   `TELEPHONY_CANARY_GATE_SETUP_MIN_ATTEMPTS` inbound setup samples (default
   30), three inbound-policy runtime activations, and one rollback drill.
   Insufficient or zero evidence rejects promotion.
5. Keep `TELEPHONY_CANARY_GATE_REQUIRE_TRANSFER_EVIDENCE=0` only for the
   signed transfer-disabled scope. Set it to `1` whenever successful transfer
   is in scope; the configured transfer sample floor then becomes mandatory.
6. Activation, rollback, freeze, and unfreeze share one host state lock. Before
   reading current state, the stage controller acquires a separate atomic
   `.controller.lock.d` adjacent to the resolved canary env and holds it through
   metric snapshot/decision publication and the child action. The lock is
   independent of run ID and `--evidence-dir`, so distinct runs cannot race the
   same env. A stale lock fails closed and requires operator investigation; the
   separate path does not deadlock the child state-change script.
7. Every stage action must include a concrete `--reason` value.
8. Preserve decision JSONL artifacts and the uniquely named run metrics
   snapshot for post-incident RCA and audit.
