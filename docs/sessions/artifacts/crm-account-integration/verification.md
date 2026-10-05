# Combined account and CRM integration

On 6 October 2026, the combined candidate `35fe11b0` passed **333 tests across
eight affected modules**, with zero failures, zero skips and one reported warning
in 12.58 seconds. The run covered the final shared resolver, reviewed account
actions, inbox account/health behavior, CRM hold behavior and Admin inspection.
It is not a new whole-backend, browser or live-provider acceptance run.

## Integrated repairs

- `45943862` (`5bdec4f3` owner): accepts genuine native UUID authorization-row
  identities without coercing arbitrary objects. The preceding synthetic 396-test
  run did not catch this PostgreSQL boundary defect. [Actual database proof](../reviewed-selector-pg/verification.md)
  records its reproduction, 168 focused tests and 12 separate SQL controls.
- `e7add564`: stops uncertain CRM call creation before connector resolution,
  reference lookup or writes. The previous worker could adopt a title-only match
  and update it without proving contact/activity ownership. [Repair evidence](../crm-reference-hold/verification.md)
  preserves four valid baseline failures and 79 affected passing tests.
- `5b880433` (`24d86b72` owner): adds explicit, platform-admin-only original-account
  CRM receipt observation to the existing call drawer. It never refreshes tokens,
  writes receipts/config/health, retries an effect or treats a reference as completed
  delivery. [Inspection evidence](../crm-receipt-inspection/verification.md) records
  393 backend and 52 Admin tests, TypeScript, scoped lint and a Vite build.

The owner runs overlap this combined run; their counts must not be added into a
new aggregate acceptance total. The Admin source and tests are unchanged from
the owner-tested commit, so those checks were not repeated during integration.

## Exact integration verification

The eight pytest modules and all sixteen source/test hashes before and after the
run are in [result.json](result.json). All matched the tested commit and remained
unchanged. Network guards recorded zero prohibited socket attempts; only
Windows event-loop socketpair creation was allowed. Actual route/resolver/provider
logic ran against synthetic database/HTTP boundaries in the new inspection
controls. Existing ordinary effect/read paths and malformed/foreign/stale denial
controls were included.

`runner.py` is archived unchanged from `tmp/crm-account-integration.py`. Its root
calculation assumes that original location. Copy it there and execute from the
`backend` directory with the environment and exact command in `command.json`.
Do not execute the archived path directly. The summary suppressed warning detail;
the single reported warning is retained in the result count and is not claimed
to have been investigated by a separate rerun.

The shared resolver merged cleanly. Its difference from the CRM owner's source
is exactly the reviewed UUID normalization. Its difference from the PostgreSQL
owner's source is the separately reviewed opt-in read-only inspection branch.
The PostgreSQL run predates that combined resolver and does not establish actual
SQL coverage of the new CRM read-only mode. The combined 333-test execution covers
their interaction at the recorded synthetic boundaries. The unchanged source and
evidence paths retain their owner hashes; merged-source hashes are recorded here
instead of relabeling an old run as testing new bytes.

## Remaining scope

Observation is now available for unresolved CRM `creating_call` receipts with
original proof. It is not available for missing legacy proof or uncertain contact
creation. Supported original email/calendar receipt inspection, an approved
durable operator-resolution contract, provider payload/contact proof, designated
live accounts, real-browser journeys and release acceptance remain unfinished.
Private PostgreSQL clones did not reproduce the complete production schema or
policies. No deployed/live readiness claim follows from these results.

All package, scenario, final-gate and deferred statuses are unchanged. The
production-readiness feature freeze remains active. All commits are local;
no push, deployment or customer/provider action occurred.
