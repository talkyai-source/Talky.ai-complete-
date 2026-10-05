# Calendar, Gmail and voice receipt integration

On 6 October 2026, committed candidate `3de75cd32c923b5bbf7eb65af813eb2154ef32bb` passed **508 tests across
twelve affected modules**, with zero failures, zero skips and one warning.
All twenty-five recorded application/test hashes stayed unchanged during execution
and match that commit. The run recorded no prohibited connection attempts.
This is a bounded synthetic-port regression, not a new whole-backend run or
live provider, database, browser or release acceptance.

## Change and review

Calendar owner source `2010a1bf10b1305280ba6047099e0af44f3b7147` adds exact-ID reads for the existing Google
and Outlook calendar connectors. The existing platform-admin action panel can
explicitly inspect a saved event through its original active authorization.
One complete co-persisted versioned proof and event ID are required; partial,
conflicting, bulk and unproven historical receipts remain unavailable.

The reads encode the event ID as one path component and request only its ID.
They never refresh tokens, search time windows, follow child/meeting records,
substitute the current account, mutate provider state or resolve/retry an action.
An exact returned ID means only that a reference was observed. Google tombstones
may retain that ID; presence does not establish an active booking or successful
creation, update or cancellation. Typed missing/gone errors remain inconclusive.

Root and independent source review cleared the final five application files and
two test deltas. Review fixes prevent mixed message/event responses substituting
the displayed reference, missing/blank IDs being accepted, and malformed timestamps
crashing rendering. The shared proof helper retains the existing Gmail admission
and rejection rules. All seven owner files match the integrated candidate.

## Evidence and limits

The [owner report](../calendar-receipt-inspection/verification.md) records **296
backend tests across seven modules** (including 87 new controls), **99 Admin
tests** (including 31 new controls), TypeScript, scoped ESLint, Vite build,
CI-rule Ruff and whitespace checks. Those results overlap this root run and are
not additive acceptance totals. The stricter Ruff invocation exposed two existing
unused imports; its output and baseline comparison remain preserved. Admin checks
remain bound to unchanged owner source; root did not repeat them or run a browser.

The combined twelve modules cover reviewed account identity/currentness, CRM
unknown holds and inspection, Gmail and calendar inspections, voice receipt
preservation and its actual proof-parser handoff. Persistence/provider ports are
synthetic. The separate earlier PostgreSQL reviewed-selector probe did not execute
these inspection modes; it is not promoted into full-schema or read-mode proof.

`result.json` preserves the exact pytest arguments, source hashes and network
guard counters. The archived runner was executed as
`tmp/calendar-mail-voice-integration.py` from `backend`; its root calculation
depends on that original location. Restore it there with `command.json`'s
environment before reproducing, rather than running the archived path directly.

## Remaining work

Missing returned IDs, missing historical proof and unusable original credentials
remain held. Inspection neither establishes payload correctness nor authorizes
another effect. Approved durable operator resolution, designated original-account
provider/browser acceptance, deployment and release evidence remain outstanding.
No package, scenario, final-gate or deferred status was changed. No push, customer
message/calendar mutation, deployment or feature-freeze exit is included.
