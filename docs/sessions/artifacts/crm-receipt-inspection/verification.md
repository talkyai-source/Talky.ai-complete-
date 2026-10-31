# Original-account CRM receipt inspection

Local source commit `24d86b72a2105f76248c9c20e553558ca247eeb1` adds a read-only AG06 support affordance to the existing platform-admin call drawer. It follows the uncertain-action inspection requirement in `docs/production ready.md`; it adds no navigation, customer authority, reconciliation policy, provider, migration, or retry mechanism.

## Behavior

- Call detail shows a sanitized saved CRM receipt projection: provider, status, phase, original connector/account, saved remote IDs, and receipt update time. It does not expose contact arguments, stored exception text, credentials, or provider response bodies. A receipt-query failure is visibly unavailable rather than an invented empty receipt history.
- Only an explicit button invokes `GET /admin/calls/{call_id}/crm-deliveries/{provider}/inspection`. The route requires `require_platform_admin`. Tenant, reference, connector, provider, and external account are derived from the saved call and its scoped receipt; request query values cannot substitute an identity.
- The saved receipt must be an unresolved `creating_call` receipt (`unknown` or `processing`) with valid original identity. Contact creation, historical unowned IDs, other phases, and missing/duplicate receipts remain unavailable.
- The existing resolver has an opt-in `read_only` mode. It requires the saved connector, provider, and external account; exactly one matching active authorization; and a known token expiry beyond the existing 90-second safety window. No newest-account fallback, refresh, token/config/health write, or new identity binding is permitted. Existing callers retain the default resolver behavior.
- The actual HubSpot/Salesforce `find_call_by_reference` methods perform the observation. HubSpot uses its existing POST search endpoint (a read); Salesforce uses its existing GET SOQL query. There is one provider lookup, with no retry. Both providers reject malformed IDs before classifying duplicate reference results.
- The only observations are `observed_reference`, `no_match`, `ambiguous`, and `unavailable`, with a fixed reason, observation timestamp, and an observed activity ID where present. An empty search is inconclusive. A unique matching title/subject does not prove contact, payload, ownership of a past effect, or completion. Responses are `Cache-Control: no-store`.
- Neither observation nor UI changes a saved receipt, business record, health state, or action status. The drawer does not prefetch or poll inspections. Duplicate clicks while a request is pending are coalesced; stale call/detail/inspection responses cannot replace another selection's evidence.

## Verification

All checks below used synthetic data and offline HTTP/DB boundaries. No customer database, credentials, live providers, or actual email/CRM writes were used.

| Check | Result | Artifact |
| --- | --- | --- |
| Affected backend regression, 14 modules | 393 passed, 0 failed, 0 skipped; 8 existing deprecation warnings | `backend-regression.txt` |
| New backend inspection controls, included above | 86 controls covering actual route/resolver/provider methods | `backend/tests/unit/test_admin_crm_inspection.py` |
| Full Admin test command | 52 passed, 0 failed, 0 skipped | `admin-regression.txt` |
| New Admin component/API controls, included above | 18 passed | `admin-focused.txt` |
| Admin TypeScript | exit 0 | `admin-typecheck.txt` |
| Scoped Admin ESLint | exit 0 | `admin-lint.txt` |
| Vite production build | exit 0, 1,768 modules transformed | `admin-build.txt` |
| Scoped backend Ruff F | exit 0 | `backend-ruff.txt` |
| Git whitespace check | exit 0 | recorded in manifest |

Backend controls cover original-account selection for both providers; a newer different account/connector without fallback; missing, revoked, foreign, duplicate, expired, near-expiry, malformed-expiry and undecryptable authorizations; canonical UUID object values; missing/foreign/duplicate and unsupported-phase receipts; provider 401/403/408/429/500; timeout and cancellation; malformed singleton/multirow IDs; duplicate-only typed ambiguity; sanitized detail projection; real platform-admin dependency enforcement; and ignored caller-supplied destination overrides. The synthetic database double rejects unexpected mutation methods and asserts saved rows remain unchanged.

Admin controls execute the actual component and API code through the repository's minimal hook scheduler. They cover explicit-click-only access, truthful observation wording, saved status preservation, no inferred retry/resolution, duplicate clicks, stale call/detail responses, malformed/inherited enum values, missing identity, and exact GET request shape. These are not a browser or React-concurrency qualification.

Independent read-only source review by `account_review` cleared the seven frozen production files after two findings were addressed: malformed IDs had been coerced to strings such as `None`/`True`, and JavaScript `in` checks accepted inherited enum keys. Provider validation now precedes duplicate classification, and UI validation uses own-property checks. The reviewer did not execute tests or change files.

## Preserved intermediate evidence

- `backend-initial-fixture.txt`: 20 Salesforce fixture failures, 62 passes. The isolated route test had omitted the canonical Salesforce factory-registration import, so no Salesforce provider read occurred. Adding the same registration import used by the application corrected the fixture. This is not a deployed application regression.
- `admin-initial-harness.txt`: esbuild could not bundle an imported CSS file without an output path. The test harness now uses an empty CSS loader; no product styling was changed.
- `backend-focused.txt`: 82 passes before the final four malformed multirow identifier controls were added. The final 393-test regression includes those four controls and all final production source changes; do not add the overlapping counts together.

## Remaining boundaries

This affordance is observation only. The pre-existing `CRMSyncService` title-only auto-adoption path was discovered separately and repaired on the integration branch at `e7add564`; it is absent from this owner's base. This branch's inspection change alone must not be described as holding every uncertain background CRM delivery. The 393-test owner run is not a combined integration run of that separate repair. Integrate and verify that repair before claiming the combined behavior.

The persisted account/token association is local admission evidence. The read is not atomic with a later external authorization revocation or a provider-side state change. Inspections do not establish a resolution policy or audit an operator decision. Missing historical proof is not retroactively filled. Provider eventual consistency, correct payload/contact proof, approved operator adjudication, designated account acceptance, real-browser acceptance, and release gates remain open. AG06 is not complete and the production-readiness feature freeze remains active.

Source hashes in `manifest.json` normalize only CRLF to LF using raw bytes. The worktree was created from `5d8ddba3037903b197e6890a3a0bdaf38c59ce56`. No push or deployment is included.
