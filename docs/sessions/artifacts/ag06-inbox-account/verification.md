# AG06 inbox read identity across authentication refresh

**Historical candidate:** this first implementation was rejected for canonical Gmail compatibility after independent review. Its synthetic external-identity fixtures missed Gmail accounts with a null external ID. The original observations below are preserved; use [the authorization-row follow-up](verification-row-pin.md) for the accepted source and final validation.

Base: `3648fe70`. Source: `bc964a8bccd6eab6826818c922b934b5eb62bfe6` on isolated branch `codex/inbox-original-account-20261005`. No remote push, deployment, provider call, database operation or resend was performed.

The actual `_call_with_one_auth_refresh` helper previously resolved the tenant's newly active connector after an authentication rejection. With original account A and replacement account B, it read the same requested message reference in B and returned success. The synthetic actual-method probe preserves that observation and a matching-A positive control in `probe-before.json`; `probe.py` contains the reproducible program. The provider and database ports are synthetic, not a live Gmail observation.

The repair freezes the initial connector/provider/external-account identity before the first await, restricts the one refresh to that connector/provider, and uses the existing `verify_reviewed_connector` before the second read. Missing or changed proof returns `email_account_changed` without exposing a replacement inbox. A foreign-connector refresh failure cannot mark that foreign connector expired. Same-account refresh still returns the original result contract.

## Source and caller inventory

- `backend/app/infrastructure/assistant/tools/inbox.py`: shared read helper and a bounded, content-free account-error projection.
- `backend/tests/unit/test_inbox_original_account.py`: 20 new actual-helper/list/read controls.
- `backend/tests/unit/test_connector_health.py`: only explicit synthetic provider/account identity added to the three existing refresh fixtures; existing assertions retained.

The helper has exactly two production callers, `read_emails` and `read_email`, both in `inbox.py`. Gmail's list mode performs per-message metadata reads inside the same connector invocation; full-message MIME parsing remains inside `get_email`. There is no separate attachment-fetch caller of this helper. Existing list caps, query/unread coercion, snippets, message-ID trimming, full-body limits and the bounded public projection remain unchanged. Attachment metadata is not newly exposed by the inbox tool.

This pins one invocation through its refresh. It does not turn a later independently requested message ID into an original-action-account inspection, nor bind separate list/read invocations to a durable action receipt. The Admin/Assistant receipt views still show saved evidence only. SMTP remains send-only. Unknown actions stay held; this repair does not provide operator adjudication, an audit transition or a provider delivery guarantee.

## Before and after checks

- `initial.txt`: **10 failed, 8 passed** before application changes. Both list/read switched-account successes and missing/changed identity controls failed; matching-account and existing non-authentication behavior passed.
- `foreign-expiry-initial.txt`: **2 failed, 18 deselected** after adding foreign-connector error controls, before application changes. The old error path attempted expiry of the unrelated connector.
- `focused.txt`: **47 passed**, one existing MIME timestamp deprecation warning, covering the new module and existing connector-health tests.
- `related.txt`: **102 passed, zero skipped**, eight existing datetime warnings across six related modules. This overlaps the 47-case run and is not additional distinct coverage.
- `lint.txt`: Ruff F checks passed for all three changed source/test files. The new test module was Black formatted; `git diff --check` passed.
- `probe-after.json`: replacement B is never read, while matching A refresh succeeds. The original and repaired probes record the imported source SHA256.

New controls also preserve first-read success, permission/timeout/cancellation propagation without refresh, the single-refresh budget, matching-account second-401 reporting, in-place connector identity changes, unchanged list/read output limits, and foreign-error no-expiry behavior. Root reviewed the bounded repair and authorized its commit with the independently reproduced expiry race below kept explicitly open. This is not a second independent test run.

## Exact commands and environment

Working directory: `tmp/inbox-original-account-20261005/backend`. Interpreter: repository `backend/.venv/Scripts/python.exe` (Python 3.12). Set `ENVIRONMENT=test` and `DATABASE_URL=postgresql://test:test@127.0.0.1:1/unavailable_test`. Existing dependency overlays were first on `PYTHONPATH`, in this order:

```text
tmp/postgres-matrix-20261005/tmp/exact-requirements-20261005/packages
tmp/production-ready-ag07-20261005/tmp/op02-testdeps
```

```text
python -m pytest tests/unit/test_inbox_original_account.py -q -o addopts=
python -m pytest tests/unit/test_inbox_original_account.py -k foreign_refresh_failure -q -o addopts=
python -m pytest tests/unit/test_inbox_original_account.py tests/unit/test_connector_health.py -q -o addopts=
python -m pytest tests/unit/test_inbox_original_account.py tests/unit/test_connector_health.py tests/unit/test_email_service.py tests/unit/test_connector_factory.py tests/unit/assistant/test_tool_arg_coercion.py tests/unit/assistant/test_streaming.py -q -o addopts=
python -m ruff check app/infrastructure/assistant/tools/inbox.py tests/unit/test_inbox_original_account.py tests/unit/test_connector_health.py --select F
```

To run either preserved probe from that working directory in PowerShell, pipe the script into the same interpreter so the backend remains the import root:

```powershell
Get-Content -LiteralPath '../docs/sessions/artifacts/ag06-inbox-account/probe.py' -Raw | python -
Get-Content -LiteralPath '../docs/sessions/artifacts/ag06-inbox-account/expiry-probe.py' -Raw | python -
```

The first attempt to redirect the initial pytest output used an absent artifact directory, so pytest did not start. The directory was corrected before the preserved initial run. No failed application observation was overwritten.

## Open, independently reproduced health-expiry boundary

`expiry-probe.py` and `expiry-residual.json` preserve an actual `read_emails` invocation with synthetic storage: A receives a 401; the same connector row now represents B; refresh fails without returning verifiable account identity. The pre-existing connector-wide expiry writes mark B expired because `_mark_email_authorization_expired` filters by connector/tenant/status, not the original external account. This does not read B after this repair, but it can misattribute connector health. The existing confirmed-auth status-reporting behavior was intentionally preserved pending a separate guarded-write correction. No account-safe expiration claim is made.

A separate correction must carry authoritative account-row/account and authorization-generation evidence to an atomic guarded status update. Re-reading identity and then issuing the same broad update is insufficient. Parent connector health must not be downgraded over a newer active account. Missing account proof must stay explicitly unavailable, while a positively identified current rejected authorization must continue to report its real failure. That follow-up requires review of the existing reconnect/refresh writes and their database acknowledgement, not a new retry or reconciliation framework.

AG06 remains open for supported operator resolution and designated-account/browser acceptance. Empty search, missing local receipt, timeout and a model conclusion are not evidence of nonexecution; no held receipt was reset or replayed here.
