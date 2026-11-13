# Host memory protection and unresolved budget — F7-05

**Partial remediation. The existing limits still do not fit the 3.8 GB host.**
This change supplies OOM priorities and a read-only allocation checker. It does
not claim that priority settings create capacity or replace measured limits.

PostgreSQL's production Compose service sets `oom_score_adj: -900`; the Docker
daemon drop-in sets `OOMScoreAdjust=-900`. Dialer, voice, reminder, cleanup and
support workers set `OOMScoreAdjust=300`, preferring those workers as victims
under host pressure. A PostgreSQL container can still hit its own `mem_limit`
and be killed inside its cgroup; a negative score is not immunity.

`install-services.sh` installs the Docker drop-in and reloads unit definitions
as usual. It deliberately does not restart Docker or recreate the PostgreSQL
container during an application deploy. The daemon setting applies on its next
approved start; the container setting requires the owner's separately reviewed
Compose rollout. The infra Compose copy is outside the app checkout, so merging
this file does not itself replace `/opt/talky-infra/compose.prod.yml`.

## Read-only budget check

```bash
cd /opt/talky/backend
venv/bin/python scripts/check_memory_budget.py --host-mib 3600 --headroom-mib 768
```

This reads only repository unit files and the tracked Compose definition. It
does not read environment files or query Docker, PostgreSQL or systemd. The
conservative default treats the stated 3.8 GB host as 3600 MiB and reserves
768 MiB for the OS, Asterisk, nginx, Docker and other unenumerated services.
The owner must review that reserve against actual host usage. All service-unit
hard caps plus container caps are summed; missing/percentage/unbounded limits
are reported, never charged as zero. `MemoryHigh` must be finite and no greater
than `MemoryMax`. Exit 0 means the declared arithmetic fits, 1 means overcommit
or incomplete limits, and 2 means invalid inputs/configuration. Even exit 0
does not establish startup, peak-call capacity or OOM-recovery behavior.

The current checked-in allocation intentionally produces exit 1: existing
finite caps alone already exceed the host, and several scheduled jobs have
no hard cap. A failed audit is not a deploy-ready budget. Do not remove jobs
from the audit merely because their usual schedules differ: they may overlap.

## Work still required under the round's time-box rule

Reducing every cap enough to satisfy the sum would be a material runtime
change for both API processes, voice/dialer/reminder workers, database and
scheduled jobs. Their measured startup and concurrent-call peaks were not
provided, and the staging capacity rehearsal has not run. Tiny caps chosen
only to make a test green could make the next deploy crash-loop. No lower
limits or database buffer changes are guessed in this partial commit.

The owner and engineering must select one concrete budget after the item-44
staging rehearsal: measured caps that fit this host with reserve, or a larger
host (the original QA report explicitly allows 8 GB+). Engineering then changes
every applicable `MemoryHigh`/`MemoryMax` and Compose memory allocation, including
currently uncapped jobs, and reruns the checker plus startup/overlap/call soak
tests. The hard-cap portion of F7-05 remains **not done** until that happens.

Owner-only evidence commands on the approved staging/production host:

```bash
systemctl show docker.service -p MainPID -p OOMScoreAdjust
docker inspect talky-postgres-1 --format '{{.HostConfig.OomScoreAdj}} {{.HostConfig.Memory}}'
systemctl show talky-api.service talky-api-web.service talky-voice-worker.service \
  talky-dialer-worker.service talky-reminder-worker.service \
  -p Id -p MemoryCurrent -p MemoryHigh -p MemoryMax -p OOMScoreAdjust
systemd-cgtop --batch --iterations=1 --order=memory
```

Record startup and peak-load samples for every service and overlapping job,
not only this one-point snapshot. Check the live Docker PID's
`/proc/<MainPID>/oom_score_adj` too: a loaded unit property alone does not prove
an already-running daemon received it. The owner plans any daemon/container
restart with backup and recovery readiness; no such action was executed here.
