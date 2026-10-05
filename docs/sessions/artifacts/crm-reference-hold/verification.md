# Uncertain CRM call creation stays unresolved

On 6 October 2026, source `e7add564` passed **79 tests across three affected
modules**, with zero failures or skips. Four regression cases reproduced the
unsafe baseline for both existing CRM providers. No provider or PostgreSQL
request was made; all service persistence/resolution/provider ports were synthetic.

## Root cause and repair

`CRMSyncService._deliver` previously searched a call title/subject after an
uncertain create, saved the returned activity ID, updated that record and marked
the delivery successful. The search supplies no proof of the activity's original
contact association or payload. Local destination/contact admission checks did
not verify the remote record. The old positive recovery test expected this unsafe
adoption, so a green suite did not establish the required ownership contract.

Recovery of `creating_call` now raises the existing destination-review outcome
before connector resolution, binding, reference lookup or provider writes. The
existing handler retains an unknown receipt and its original account, contact,
remote IDs, intent and completion evidence. It cannot publish a synced result.
Ordinary creation and updates to already acknowledged activity IDs retain their
existing paths. The separate Admin inspection may observe a reference; it does
not authorize adoption, completion or resend.

This is a safety repair, not completion of the operator-resolution workflow.
The existing bounded claim/backoff attempt bookkeeping is unchanged. An approved
durable resolution policy and designated provider acceptance remain open.

## Verification

- `baseline.txt` / `baseline.json`: four valid failures before the application
  change. Full sync falsely reported success for Salesforce and HubSpot; the
  two expired-lease-shaped direct service calls did not hold the uncertain create.
- `final.txt` / `final.json`: **79 passed**, including all four controls, ordinary
  provider delivery/update/idempotency, independent destinations, changed source
  and account rejection, hooks and existing AG06 CRM evidence checks. The exact
  three-module pytest arguments and before/after LF source hashes are preserved.
- `lint-final.txt`: the repository CI Ruff F selection passed for both changed
  files. A broader E/F/W experiment reported two pre-existing E701 one-line
  statements at test lines 386–387; those unrelated statements were not changed.
  Source whitespace checks passed.
- Independent read-only review cleared the early hold, receipt preservation,
  unchanged acknowledged-ID update path and four regression controls. The
  reviewer did not execute tests.

`initial-harness.txt` / `.json` preserve a failed first harness: the network guard
blocked Windows asyncio's private loopback socketpair, so its four failures are
not product evidence. The corrected guard permits only thread-local socketpair
creation and rejects other socket connections. `before.txt` / `.json` preserve
an intermediate test run: two valid false-success failures and two incomplete
expired-lease fixture failures. The fixtures were seeded through the actual sync
service before the final baseline. Neither intermediate run is counted as a
second independent set of defects.

The final run recorded zero prohibited network attempts, 104 internal socketpair
creations, unchanged source hashes, and no warnings in the pytest summary.
`runner.py` is the exact final harness archived from
`tmp/crm-unknown-verification.py`; its root computation assumes that original
location. Copy it there before reproducing from the repository's `backend`
directory with the environment and arguments in `command.json`. The archived
runner is not directly executable from its evidence location.

The expired-lease cases provide the actual service with the receipt shape the
existing store emits; they do not execute lease SQL, simulate a process crash or
prove production recovery. No live CRM, browser, release gate, deployment or
push is covered. The earlier general statement that all unknown CRM creation was
held must be read with this correction: contact creation was held, while this
call-creation reference-adoption path required the additional repair.
