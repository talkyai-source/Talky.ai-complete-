# Staging call-capacity rehearsal — F6-01

Prepared procedure only: no load test, provider request, production connection
or admission-setting change was performed. The current default of 50 is not a
measured capacity. The owner supplies the staging server, approved synthetic
accounts, provider budget and test operator. Run neither this ramp nor its
generators on the low-memory development laptop or the production server.

## One capacity setting for the single-host deployment

Use **`MAX_TELEPHONY_SESSIONS=C`**, a positive integer, as the documented
single-host admission setting. Its current default is 50. It is read at process
startup by `app/domain/services/telephony/config.py`; the bridge readiness and
outbound start gate use that same constant. Redeploy/restart the relevant
staging services after changing it; changing a shell variable does not alter
an already-running process.

`global_concurrency.resolve_global_cap()` also honors the existing optional
`MAX_TELEPHONY_SESSIONS_GLOBAL` override. For this single-host runbook, leave
that override **unset** in the owner-managed service configuration so Redis
admission falls back to `MAX_TELEPHONY_SESSIONS`. Verify the effective values,
not only a shell export. Do not introduce a second contradictory cap. Multiple
pods need a separate reviewed cluster-versus-per-pod budget. Per-plan and
tenant limits may be lower; no tenant or advertised plan may exceed the
measured host capacity. This document changes no settings or plan entitlements.

The Redis cap bounds admitted voice sessions, not ringing outbound legs.
Inbound admission acquires before Answer; the current outbound lifecycle
acquires after Answer, before voice setup. Test the actual admission paths
separately from the synthetic pipeline below. A successful synthetic run does
not prove ringing capacity or that a simultaneous burst cannot oversubscribe.

## Preparation and evidence

1. Provision staging with the production CPU/RAM/disk class (about 3.8–3.9 GB
   RAM), OS, code revision, unit/container limits and all normal services.
   Use an anonymized database and synthetic campaign with no real recipients.
   Isolate staging Redis, storage, credentials, callbacks and queues from
   production. Block production dial destinations and external CRM/email/SMS
   writes. Verify monitoring and an operator-controlled Stop path first.
2. Use the production provider/model/voice combination with owner-approved
   test keys and quota. Fake providers cannot establish live-call capacity.
   Record provider limits, region, model, voice, codecs, sample rates, first
   speaker, code commit, service limits and test timestamps. Do not put keys
   in command-line arguments or archived logs. This procedure incurs provider
   usage; the owner sets a spend limit before running it.
3. Have an external operator/load-generator host observe staging. On staging,
   create a private evidence directory and collect CPU/RAM/process/disk/Redis
   telemetry every second. Record PostgreSQL connection counts, worker
   heartbeat ages, queue depth, provider failures/rate limits, admission
   refusals, disconnects and audio/turn errors. Use existing metrics where
   available. Example OS capture (requires the owner's installed sysstat):

   ```bash
   umask 077
   EVIDENCE_DIR="$PWD/capacity-$(date -u +%Y%m%dT%H%M%SZ)"
   mkdir -p "$EVIDENCE_DIR"
   git rev-parse HEAD > "$EVIDENCE_DIR/revision.txt"
   pidstat -h -r -u -d -p ALL 1 > "$EVIDENCE_DIR/processes.txt" &
   PIDSTAT_PID=$!
   vmstat -w 1 > "$EVIDENCE_DIR/vmstat.txt" &
   VMSTAT_PID=$!
   # Keep these two PIDs; after the rehearsal stop only these collectors:
   # kill "$PIDSTAT_PID" "$VMSTAT_PID"
   ```

4. Confirm the owner-provisioned staging configuration file and the synthetic
   tenant/campaign UUIDs. The existing caller reads the file named by `--env`;
   it uses read-only database connections and captures contact writes in
   memory. It still makes real STT/LLM/TTS requests. Do not use production
   credentials or assume its read-only DB mode isolates provider side effects.

## Pipeline ramp: 1 → 5 → 10 → 20

From the staging backend directory, set these non-secret references and run
each tier separately. Review its results before raising `N`; do not paste an
unattended loop that advances through all tiers.

```bash
PYTHON=venv/bin/python
TENANT_ID='replace-with-synthetic-staging-tenant-uuid'
CAMPAIGN_ID='replace-with-synthetic-staging-campaign-uuid'
STAGING_CONFIG='/owner-managed/path/staging-capacity.env'
N=1  # Repeat explicitly with 5, then 10, then 20 only after the prior tier passes.
mkdir -p "$EVIDENCE_DIR/tier-$N"
pids=()
for i in $(seq 1 "$N"); do
  "$PYTHON" scripts/synthetic_caller.py \
    --env "$STAGING_CONFIG" --tenant "$TENANT_ID" --campaign "$CAMPAIGN_ID" \
    --first-speaker user --wait 25 \
    --say 'at:1.0:Hello?' \
    --say 'after:0.8:What do you offer?' \
    --say 'during:1.5:Please stop. I have a different question.' \
    --say 'after:0.8:What does it cost?' \
    --say 'after:0.8:Thank you. Goodbye.' \
    --log "$EVIDENCE_DIR/tier-$N/call-$i.log" \
    > "$EVIDENCE_DIR/tier-$N/call-$i.stdout" 2>&1 &
  pids+=("$!")
done
failed=0
for pid in "${pids[@]}"; do
  wait "$pid" || failed=$((failed + 1))
done
printf 'tier=%s failed_processes=%s\n' "$N" "$failed" \
  > "$EVIDENCE_DIR/tier-$N/process-summary.txt"
```

This script creates in-process browser voice pipelines; it does **not** send
requests through the deployed API, dialer, Asterisk or carrier. Parallel child
processes are also a different allocation pattern from the voice worker. Use
the ramp to measure provider/pipeline behavior and identify limits, then repeat
the tiers through the actual staging Test Agent and approved test-trunk paths
from the external generator before selecting the production cap. The existing
script alone cannot certify gateway/dialer capacity. Do not alter protected
voice-agent code merely to imply coverage it does not provide.

For each tier, verify actual simultaneous active voice intervals in the logs;
launching N processes is not proof of N overlapping calls. Repeat fixed-length
sessions for at least ten minutes of sustained overlap after warm-up, collect
at least 100 ordinary turns, and exercise caller-first, agent-first, tool turns,
barge-in and clean hangup separately. After selecting a candidate tier, run a
60-minute soak. Repetition and controlled replacement of finished sessions are
operator tasks; the one-batch command above is not a sustained-load controller.

## Measurements, gates and stop criteria

Measure each turn from caller end-of-speech to first audible agent audio.
Do not substitute whole-process duration or LLM-only time. Preserve raw
timestamps and dropped/failed turns alongside p50/p95/p99; an error is not a
latency sample to discard silently. Compare output audio/transcripts for
cut-off responses, dropped caller turns, barge-in and repeated goodbyes.

QA F6-01 cites GT04 median ≤1.2 s and GT05, but does not define GT05 in that
section. The current repository standard `docs/standards/voice-agent-standards.md`
LAT-1 is stricter: ordinary voice turns p50 ≤800 ms and p95 ≤1,200 ms; tool turns
p95 ≤2,000 ms. Record both the QA criteria and those current targets, and obtain
the QA owner's exact GT05 criterion before declaring a pass. An undefined
acceptance criterion is not satisfied by a green process exit.

Stop the ramp, retain evidence and return to the previous stable tier if there
is OOM/swap thrashing, a provider quota error, a stuck Stop/hangup, corrupted or
missing audio, escalating queue depth, a failing latency/quality gate or a
resource ceiling. Proposed resource ceilings for owner approval: sustained
CPU under 80%, RAM under 85%, no OOM, no sustained swapping and enough reserve
for PostgreSQL, backups and other workers. Item 15's actual service budget must
be applied in staging; unlimited staging units invalidate the comparison.

| Tier / repetition | Observed overlap | Turns / errors | Ordinary p50 / p95 / p99 | Tool p95 | CPU avg / peak | RAM peak / swap | Provider / queue / audio failures | Gate result |
|---|---|---|---|---|---|---|---|---|
| 1 | pending | pending | pending | pending | pending | pending | pending | unverified |
| 5 | pending | pending | pending | pending | pending | pending | pending | unverified |
| 10 | pending | pending | pending | pending | pending | pending | pending | unverified |
| 20 | pending | pending | pending | pending | pending | pending | pending | unverified |

Choose C no greater than the highest tier that passes both sustained end-to-end
and soak gates with reserve. Interpolate upward only after testing that value.
Document reductions for provider quotas and shared services. Then rehearse
exactly C admitted sessions plus one extra concurrent admission, both inbound
and outbound; the extra request must be refused/retried clearly, existing calls
must remain healthy, and release must restore capacity. Verify Stop during
dispatch, process restart/lease expiry and provider failure separately.

The owner applies the measured setting, confirms tenant/plan caps, attaches
metrics and listener evidence, and approves any production rollout. Repeat
after server size, model/provider, voice pipeline, memory limits or worker
topology changes. Until those receipts exist, F6-01 remains operationally open.
