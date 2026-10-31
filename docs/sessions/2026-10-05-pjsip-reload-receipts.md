# PJSIP configuration reload acknowledgement

Date: 5 October 2026. Base: `3c2bc1d48bdcf28848a74b275c379d342e71eb82`.
Source: `28833884` in isolated worktree `tmp/pjsip-reload-receipt-20261005`.
This is a bounded OP04/OP11 repair to the existing trunk configuration path.
No host, Asterisk command, provider, database or deployed configuration was used.

## Reproduced problem

A second trunk change arriving during another request's reload delay immediately
received `coalesced` with `accepted=True`. `apply_trunk_config(require_reload=True)`
therefore returned successfully before any reload command had acknowledged the
change. If the first command subsequently failed, the second caller had already
been told success. After the delay, commands could also overlap because the lock
did not cover execution.

Three initial controls failed. Expanded controls reproduced six failures,
including cancellation of a waiting request, an uncollected running child after
cancellation, and no acknowledgement timeout. These were actual service methods
with synthetic subprocess ports and temporary files; they are not evidence of a
customer incident or an exercised carrier configuration.

## Repair

Each enabled request now holds the existing reload lock through its delay,
command and acknowledgement. The early-success optimization is removed; no
shared-task coordinator, queue, new service or host permission is introduced.
Only an `executed` receipt is accepted. The legacy `coalesced` value can still be
represented but is not accepted, and the implementation no longer emits it.

The command has a 10-second acknowledgement deadline after process creation.
Timeout returns an unconfirmed failure. Cancellation or timeout stops, drains
and reaps the owned child before the next command may start. Repeated
cancellation cannot release ownership early, and a pipe error cannot turn caller
cancellation into an ordinary result. If stopping the process is denied or
reaping remains uncertain, ownership can remain held beyond the deadline: this
is not a hard 10-second API completion guarantee. Time spent waiting for the
lock, the existing configured delay and process creation is also separate.

The original production synchronization hook still restores the prior file
projection on a failed activation/edit and raises HTTP 503. The new control
executes that actual hook with two synthetic activations: both receive 503 and
both uncommitted temporary files are removed when all reloads fail. It does not
exercise an actual database transaction or prove the compensating reload worked.

## Verification and independent review

The final four-module run passed **85 checks, zero failures, one explicit POSIX
file-mode skip**, in 4.24 seconds. It covers the new receipt/concurrency/cleanup
controls and existing configuration generation, Asterisk setup and selected
trunk readiness checks. Two new checks use an actual harmless local Python
subprocess to prove cancellation/timeout reaping; no Asterisk binary runs.
Synthetic held-process controls cover repeated cancellation, denied kill and
pipe errors without starting any real PBX command.

The first repair passed 34 checks/one skip, then 80/one skip after adjacent and
real-child coverage. Independent review identified cancellation plus a pipe
error swallowing the original cancellation; a related denied-kill path could
release the lock before child exit. Both were reproduced (**two failures, nine
passes**) and repaired. An intermediate 83/one-skip run preceded the final
repeated-cancellation controls and task-result consumption correction. Counts
overlap; they are not added together.

The previous test explicitly accepted a pending reload; it was replaced by
assertions that only an executed receipt qualifies, backed by the actual
concurrent service-path controls. No acceptance threshold, route policy,
authorization rule or existing health requirement was relaxed.

Ruff passed with the existing CI selection `--select F --extend-ignore F401,F841`;
`git diff --check` passed. The LLM review agent independently reviewed the final
source and tests and found no further material defect. It did not run a separate
suite or host check. The final source hashes match the files in `28833884`.

Evidence: [initial failures](artifacts/pjsip-reload-receipts/initial.txt),
[expanded failures](artifacts/pjsip-reload-receipts/cancel-initial.txt),
[review-found failures](artifacts/pjsip-reload-receipts/edge-initial.txt),
[final output](artifacts/pjsip-reload-receipts/reviewed.txt),
[exact command/source snapshot](artifacts/pjsip-reload-receipts/reviewed-command.json),
and [source/quality verification](artifacts/pjsip-reload-receipts/verification.json).

From `backend` in an isolated test environment using the declared dependencies:

```text
python -m pytest tests/unit/test_pjsip_reload_receipts.py tests/unit/test_pjsip_config_generator.py tests/unit/test_setup_asterisk_contract.py tests/unit/test_sip_trunk_call_readiness.py -q --tb=short -ra
```

The recorded runner used Python 3.12.12, explicit synthetic provider values, an
unavailable unit-test database, no worktree `.env`, and the existing exact-direct-
requirement overlay. No dependency was installed or changed for this repair.

## Limits and remaining acceptance

A zero command exit is an acknowledgement, not evidence that an endpoint/AoR is
loaded, registered, reachable or able to place the selected call. Existing
runtime readiness and selected-route checks remain necessary. File writes occur
before the reload lock; this repair does not establish whole file/database
transactional isolation across tenants or processes. The current API's supported
single-process ownership remains relevant. Timeout cannot prove no remote effect.

Production activation/edit requests may wait longer under concurrent changes
because each now awaits its own command. Automatic reload remains opt-in. No
systemd account, filesystem ownership, sudo policy or recording permission was
changed. Linux file modes, real Asterisk failure/recovery, deployed concurrency,
selected caller-ID and owned-handset acceptance remain unrun. OP04 and OP11 stay
open; no release/scenario gate is closed by this repair.
