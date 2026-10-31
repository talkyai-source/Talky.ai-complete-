# Inbound lease-loss inventory and local finalizer ownership

This is a bounded OP05 follow-up to the provider-family fence. When durable leg inventory is unavailable, the inbound lease-loss path still requests the known same-provider parent's termination, but neither its absence nor a synchronous terminal callback authorizes logical completion. The existing recovery coordinator retains the retry obligation until durable inventory and all-leg absence are verified.

The source commit is recorded in [manifest.json](manifest.json). Its parent is `5ef4a25a46305613342470089f264b6fb4580a90`, the evidence commit following provider-family source `c9d3f87e6dd7c9011008f886419e17888a1a6825`. Those two commits are unchanged. Only `backend/app/domain/services/telephony/lifecycle.py` changes application behavior in this follow-up.

## Preserved reproductions

- [probe.py](probe.py) and [before.json](before.json) exercise the actual baseline lease-loss helper, force helper and Asterisk DELETE-404 proof, with synthetic SQL, DB-outage, ARI and logical-finalizer ports. With DB available, both saved parent and child receive a proof request. With DB unavailable, only the parent does, yet the old path invokes the logical finalizer and returns `completed=true`. The adapter's local transfer mapping is explicitly empty. This is not a real hidden channel or a database/provider test. Network attempts recorded by this probe: zero.
- [initial.txt](initial.txt): the first seven new controls gave **6 failed, 1 passed** before the repair. These include actual `_on_call_ended` callback admission during the synthetic adapter request, storage recovery and competing ownership.
- [logical-marker-initial.txt](logical-marker-initial.txt): **2 failed, 1 passed, 12 deselected**. After all-leg proof, cancellation or an exception at an awaited dependency of the actual normal finalizer left its local ten-minute marker in place, preventing the same recovery path from retrying. The existing-other-owner control passed.
- [marker-timer-before.py](marker-timer-before.py) extracts the actual baseline expiry function from Git and evaluates its timer against synthetic task/marker state. [marker-timer-before.json](marker-timer-before.json) records an old cancelled timer deleting both the replacement in-flight marker and its completion flag. It is a function-level baseline probe, not full call finalization.
- [generation-initial.txt](generation-initial.txt): **2 failed, 17 deselected** in the intermediate token draft. A missing token could clear a bare marker, and an older held finalizer could mark a replacement owner's generation complete and acknowledge its retry ledger. Both exact controls are retained.

## Repair and ownership boundaries

The lease-loss coordinator installs a marked context in the existing recovery-context map before its first await. Natural callbacks consult the existing all-leg-proof guard. The coordinator refuses to overwrite another hydrated context or an in-flight recovery owner, and rechecks exact context identity before completion. A durable provider-ID change also holds the result; it is not inferred to be an alias.

Storage failure permits only the known parent's same-provider termination request. The provider-family helper continues to reject missing or mismatched ownership. Unverified inventory, unconfirmed child proof and cancellation retain the guard and existing retry obligation. On recovery, the existing orphan coordinator hydrates the durable context and proves the complete selected leg set. On the direct successful path, only this coordinator's guard is removed, and the normal live finalizer runs without fabricated recovery data, preserving its existing admission and duration handling.

One opaque token accompanies each existing local ended-call marker. Expiry, failed-settlement cleanup, orphan-recovery cleanup and lease-loss cleanup compare their captured token. A missing token cannot authorize cleanup. An old finalizer also checks its generation before publishing completion or initiating ledger acknowledgement. This prevents local stale timers and earlier callbacks from removing or completing a later owner's marker. Successful completion and the normal expiry policy remain intact; matched cleanup removes the marker, completion flag and token together.

This is process-local generation ownership. It does not prove cross-process fencing or withdraw a Redis command already dispatched before a later ownership change. The provider-family fence still does not prove original PBX host/account identity.

## Final verification

The final source-bound regression is **304 passed, 0 failed, 0 skipped**, across 16 modules, in 30.39 seconds: [final-regression.txt](final-regression.txt). It includes all **19** new parametrized controls. The 279 warnings include existing datetime deprecations and the existing unawaited `AsyncMock` warning in a voice-pipeline fixture; they are not represented as a warning-free run.

The new controls cover:

- DB outage, repeated failure and eventual complete durable inventory;
- actual synchronous callback during parent termination, and callback during the first Redis await;
- still-present child, later all-leg absence and existing watchdog recovery convergence;
- request cancellation, another context/marker owner and paused concurrent attempts;
- actual normal-finalizer cancellation/error followed by retry;
- stale timer versus a newer callback, with and without completion;
- missing-token refusal and token/set/completion cleanup consistency;
- two actual held finalizers: the old owner returns false without completion or acknowledgement, while the current owner later completes and acknowledges once.

Most callback-admission tests stop at the first mutation after the real callback guard using a test signal. They prove whether logical finalization was admitted, not financial settlement. The final held-finalizer control runs the callback orchestration with synthetic storage/finalization and retry-ledger ports. No actual PostgreSQL, Redis server, provider, telephone call, billing effect or production state was used or verified.

[ruff-final.txt](ruff-final.txt) records clean CI `F` checks over the application file and three affected test files, using the repository's `F401,F841` exclusions. [diff-check.txt](diff-check.txt) has no whitespace errors; Git's line-ending advisories are retained.

Earlier green runs are diagnostic history, not additive acceptance counts: 7 in [first-green.txt](first-green.txt), 12 in [expanded.txt](expanded.txt), 120 in [ownership-followup.txt](ownership-followup.txt), and 302 in [lifecycle-regression.txt](lifecycle-regression.txt). The final 304-case union supersedes these overlapping runs.

The exact interpreter, dependency overlays, test-only environment, command arguments and canonical source hashes are in [manifest.json](manifest.json). Canonical hashes use `read_bytes().replace(b"\r\n", b"\n")`; raw byte hashes are separately labelled. No default text decoding or re-encoding is used for source identity.

RT independently read the final application delta and controls and found no further material defect. That review did not execute tests or use providers/PG; the test counts above belong only to the recorded commands. Root's final review is recorded with the committed manifest.

No new reconciliation workflow, provider mapping, schema or deployment was added. Incomplete external proof continues to hold recovery; this local repair does not close the full OP05 production acceptance gate.
