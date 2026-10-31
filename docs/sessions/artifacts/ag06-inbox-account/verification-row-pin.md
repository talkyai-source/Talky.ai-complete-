# Inbox authorization-row compatibility follow-up

Source: `0f69c0f736ec0080ef68a575ccb13e9a6dbe1384`, following historical source `bc964a8` and evidence `da9f10e6`, on `codex/inbox-original-account-20261005`. All provider and database ports in these tests are synthetic. No provider, network, PostgreSQL, resend, deployment or remote push occurred.

## Rejected assumption and final behavior

Independent source review found that the actual Gmail OAuth callback stores verified `account_email` but leaves `external_account_id` null. The first inbox repair required the latter before even the first read. The earlier 102 passing cases therefore did not establish canonical Gmail compatibility. The original source/probes/logs remain preserved rather than rewritten as final evidence.

The follow-up uses the existing `connector_accounts.id` to bind one invocation to its authorization row. OAuth reconnect inserts a new row; ordinary credential refresh updates the same row. The real resolver now returns credential-free `account_row_id`, and its optional `account_id` filter requires the chosen connector, tenant, active status and exact row. A missing or inactive pin cannot fall back to another account. This row identifies a local authorization, not a provider-stable subject or original delivery receipt.

The inbox helper snapshots connector/provider/row before its first await. A successful first read remains supported even on a legacy object missing row proof; a 401 cannot trigger an unproved retry. Canonical Gmail null-external-ID accounts can refresh the same row once. When an external identity was available initially, the existing `verify_reviewed_connector` comparison remains an additional requirement. Shared reviewed-effect identity rules are unchanged.

After independent review identified a during-refresh revocation race, the existing token write gained `status=active` in its acknowledged exact-row update. A deleted, replaced or revoked row cannot report a successful refresh and initiate a second inbox read. A missing pinned connector/account returns `email_account_changed`; it does not assert that another currently connected mailbox is disconnected. Actual same-row provider rejection and database error classifications remain distinct.

## Actual-method coverage and results

The new compatibility fixture executes the production OAuth callback, real Gmail connector construction, production resolver and inbox read, replacing only provider methods, encryption and the database port. Its database port applies the actual equality predicates and records query/update acknowledgements; it is not PostgreSQL/RLS proof. It verifies that callback persistence really leaves the external ID null before testing successful first read and same-row 401 refresh.

Controls cover replacement authorization (even with unchanged external identity), in-place metadata changes, deletion, inactive status, foreign tenant/connector, no fallback to a newer row, and deletion/revocation during the awaited provider refresh. Existing list/body bounds, one-refresh budget, other-error/cancellation behavior, and foreign-connector no-expiry controls remain.

- `row-pin-initial.txt`: 14 failed / 17 passed on the historical candidate, including both canonical Gmail first-read and refresh cases. The five replacement cases were stopped too early by the invalid initial guard; their failures assert that the original read must occur before the replacement challenge.
- `row-pin-focused-fixture-failure.txt`: 1 failed / 57 passed; an existing successful-refresh stub omitted the newly returned row ID. The stub was corrected without changing its assertions.
- `row-pin-related.txt`: interim 114 passed; `row-pin-callers.txt`: interim 104 passed. These precede the final during-refresh correction and are not additional distinct coverage.
- `row-pin-refresh-race-initial.txt`: 1 failed / 1 passed / 32 deselected. Revocation during refresh wrongly allowed a second read; deletion already failed the acknowledged update.
- `row-pin-final.txt`: final **220 passed, zero failed, zero skipped**, 13 existing datetime warnings across 12 modules. The owned inbox module now contains 34 cases.
- `row-pin-final-lint.txt`: Ruff F clean for both application files and both changed test modules. The owned test module is Black formatted and `git diff --check` passed.

Independent review by the realtime agent was source-only: it confirmed the canonical identity, scoped pin, active-status write acknowledgement, no-fallback behavior and unchanged reviewed-effect helpers. It did not run another test suite, database or provider check. Root reviewed the final diff before authorizing the source commit.

## Reproduction

Use the interpreter, synthetic environment and ordered dependency overlays in the historical `verification.md`. Working directory is this isolated worktree's `backend`. `row-pin-manifest.json` records the exact source commit, normalized SHA256 hashes and final module inventory.

```text
python -m pytest tests/unit/test_inbox_original_account.py tests/unit/test_connector_health.py tests/unit/test_email_service.py tests/unit/test_connector_factory.py tests/unit/assistant/test_tool_arg_coercion.py tests/unit/assistant/test_streaming.py tests/unit/assistant/test_calendar_and_drive_errors.py tests/unit/test_calendar_delivery_contract.py tests/unit/test_communication_delivery_contract.py tests/unit/test_crm_sync_hooks.py tests/unit/test_crm_sync_service.py tests/unit/test_meeting_service.py -q -o addopts=
python -m ruff check app/infrastructure/assistant/tools/inbox.py app/services/connector_resolver.py tests/unit/test_inbox_original_account.py tests/unit/test_connector_health.py --select F
```

## Preserved limits

The independently reproduced connector-wide health-expiry race in `expiry-residual.json` remains open. This patch does not make those status writes account/generation safe. A separate atomic correction is being designed using the existing pool and transaction support; no such repair is claimed here. The earlier probe files are bound to their recorded historical source and fixtures, not rerun or relabeled for this follow-up.

The row pin governs the current read/refresh invocation; it does not bind separately requested message IDs to an original action's provider account or supply operator adjudication. It cannot atomically prevent a future authorization change after its final database acknowledgement and the external request. Held/unknown external action receipts remain held, and designated-account/browser acceptance remains unrun.
