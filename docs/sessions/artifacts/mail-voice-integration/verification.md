# Gmail inspection and voice receipt handoff

On 6 October 2026, the combined source passed **421 tests across eleven affected
modules**, with zero failures, zero skips and one warning in 14.35 seconds.
Application source was at `7c4baa91`; the four new handoff controls were frozen
but uncommitted during execution, then committed unchanged as `e81f525f`.
The twenty-two before/after source hashes identify the executed files and match
that final commit. This is a scoped synthetic-port run, not live acceptance.

## Changes

- Gmail inspection source `5e586e4e`, integrated as `a195ac72`, adds an explicit
  platform-admin read of a saved message ID through its original authorization.
  One complete, consistent versioned proof and message ID must have been saved
  together. The endpoint does not stitch partial evidence, follow child actions,
  use the currently selected replacement account, refresh tokens or change any
  receipt. Only the existing exact `get_email` method is called. A returned ID
  means presence observed; a typed missing response is inconclusive. Neither
  sending, delivery, payload correctness nor permission to resend is inferred.
- Voice source `1d122149`, integrated as `651fc90c`, preserves the existing safe
  original authorization/message fields and actual inner action ID in the outer
  voice receipt. Accepted and unconfirmed outcomes retain their prior semantics.
  The existing public receipt allowlist excludes message content, recipients,
  provider errors and credentials. Historical missing proof is not reconstructed.
- Handoff test `e81f525f` exercises actual voice confirmation, performance,
  durable save/public receipt and Gmail proof admission together. Accepted,
  unknown and first-outer-save-failure receipts supply the same original proof
  without changing status or repeating a send. A form receipt retains evidence
  but remains outside the Gmail inspector's `send_email` type gate.

## Evidence

The [Gmail owner report](../gmail-receipt-inspection/verification.md) records
513 backend tests across fourteen modules, including 66 new controls, and
68 Admin tests, including 16 new controls. TypeScript, scoped ESLint/Ruff, Vite
build and whitespace checks passed. The [voice report](../voice-email-receipt-proof/verification.md)
records 138 tests across five modules, including 18 new controls, with the
original projection failure preserved. These owner runs overlap the combined
421-test run and are not additive acceptance totals.

Root and the independent reviewer cleared both application changes. The new
handoff test was separately reviewed. All six Gmail and both voice source files
match their owner commits. Admin source was unchanged after its owner checks,
so those passing checks were not repeated here.

[result.json](result.json) preserves exact pytest arguments, the execution base,
before/after hashes and network guard results. The run recorded no prohibited
network attempt and no source changes. The twenty-two inputs include eleven
application files and eleven test modules. Real application logic ran against
synthetic persistence/provider boundaries; no customer data, PostgreSQL operation
or external email request was used. Warning detail was suppressed in this root
summary; the one warning remains explicit.

The archived `runner.py` is the exact script executed as
`tmp/mail-voice-integration.py`, with a root calculation dependent on that original
location. Copy it there and run from `backend` with `command.json`'s environment;
do not execute the archived path directly. The manifest binds the executed bytes
to the later committed handoff test without rewriting the original result.

## Remaining boundaries

Lost sends without a returned message ID, missing/conflicting original proof,
bulk/SMTP receipts and form outer receipts remain unavailable to this inspector.
Their status and evidence stay intact. An older still-active original Gmail
authorization can be inspected; that does not relax current-account admission
for new effects. Expired/revoked authorizations are not refreshed by inspection.

Exact calendar observation is the next bounded implementation item. Filtered
calendar lists do not prove absence. Approved durable operator resolution,
designated provider/browser acceptance, deployment and release gates remain open.
All package, scenario, final-gate and deferred statuses remain unchanged. No push,
deployment or feature-freeze exit is included.
