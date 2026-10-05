# Original-authorization Gmail message inspection

Local source commit `5e586e4ec46de2ea6e68d8f661668446bf7419be` adds an explicit read to the existing Admin action-receipt panel. This bounded AG06 affordance implements the inspection portion of the fixed readiness plan; it does not establish an operator resolution policy or complete AG06.

## Contract

- The platform-admin-only `GET /admin/actions/{action_id}/email-inspection` derives the original tenant, connector, provider, authorization row, optional external identity and message ID from the selected saved `send_email` action. Request parameters cannot replace those values.
- Only a complete `authorization_row_v1` proof and message ID co-persisted in one `output_data` object or its `provider_result` object qualifies. Present direct/nested bundles and action references must agree; partial or contradictory wrappers, recorded intent contradictions, missing legacy proof, bulk/SMTP/calendar/form receipts and missing IDs remain unavailable. No `public_action_receipt` field stitching, child-action lookup, recipient/time matching, or current-account substitution is used.
- The existing resolver's opt-in `read_only` mode now accepts the original authorization row as an alternative to its original external-account pin. It requires an active parent, one active matching row and a known safe expiry. It never refreshes or writes. An older original authorization A can still be inspected while a newer B exists; it never redirects to B. Effect execution retains its separate current-authorization rule.
- After resolution, the existing versioned-proof verifier compares all original identity fields, including absent/null external identity. A newly added, removed, or changed external identity prevents the read. No external ID is fabricated from a local UUID or email address.
- Exactly one existing `GmailConnector.get_email(message_id)` read is made. The returned ID must equal the saved ID. Its actual typed `ConnectorProviderError(provider='gmail', operation='get_email', status_code=404)` produces an inconclusive absence observation. Generic errors mentioning 404 are unavailable; provider text is never parsed for status.
- Results are only `observed_message`, `not_observed`, or `unavailable`, with a fixed reason, observation time, and observed message ID where applicable. No body, subject, headers, recipients, provider error body, token, or credential metadata is returned. Responses are `Cache-Control: no-store`.
- `observed_message` means the exact ID was returned in the saved authorization. The existing parser does not preserve SENT labels or full MIME/recipient correspondence, so the observation does not prove sent, delivered, intended payload, or completion. Absence does not establish non-execution or permit a resend.
- The existing saved-receipt panel now labels the identity version and original authorization row. Server-computed button availability requires platform-admin authority and eligible saved proof; tenant/partner admins retain their scoped local receipt view without new mailbox-read authority. Inspection is explicit-click only, with duplicate-click and stale-selection guards. It neither changes status nor offers resolution/retry, polling, or prefetch.

## Validation on the frozen owner source

| Check | Result | Artifact |
| --- | --- | --- |
| Affected backend, 14 modules | 513 passed, 0 failed, 0 skipped; 28 warnings | `backend-regression.txt` |
| New backend inspection controls included above | 66 | `backend/tests/unit/test_admin_gmail_inspection.py` |
| Full Admin test command | 68 passed, 0 failed, 0 skipped | `admin-regression.txt` |
| Focused action-receipt component/API tests | 27 passed, including 16 new controls | `admin-focused.txt` |
| Admin TypeScript | exit 0 | `admin-typecheck.txt` |
| Scoped Admin ESLint | exit 0 | `admin-lint.txt` |
| Admin Vite production build | exit 0; 1,768 modules transformed | `admin-build.txt` |
| Scoped backend Ruff F | exit 0 | `backend-ruff.txt` |
| Git whitespace check | exit 0 | manifest |

The 65-pass earlier backend focused log predates one final wrapper-action-reference control. All 66 new controls are included in the 513-pass regression. The overlapping focused/full counts must not be added together.

Backend controls exercise the actual route, real resolver and existing Gmail HTTP read using synthetic boundaries. They cover canonical inner, assistant outer and nested receipt shapes; coherent duplicate bundles; partial/conflicting/stitched bundles; proof/reference/input contradictions; original older row selection; native UUID database scalars; missing/revoked/foreign/expired authorizations; optional external identity drift; actual typed HTTP failures; malformed/different returned IDs; timeout/cancellation; platform-only dependency enforcement and detail availability; ignored request overrides; no refresh and no database mutations. The read-only database double rejects unexpected write methods and asserts saved rows remain unchanged.

Admin controls execute the actual component and API source with the repository's minimal hook scheduler. They cover explicit-click access, original-row labels without invented external identity, truthful uncertainty, saved state preservation, stale responses, duplicate clicks, malformed/mismatched/inherited result fields, fixed sanitized errors and exact GET shape. These are synthetic component checks, not real-browser or React-concurrency acceptance.

Root and `account_review` independently read the final four production files and cleared the source before commits. The independent reviewer did not execute tests or change files.

## Scope and remaining limits

No Gmail provider method, provider API, migration, navigation, calendar search, voice linkage, business record, receipt status, connector health state or OAuth write flow was added or modified. No live provider/customer database/real PostgreSQL/browser call was used by this owner.

This owner tree starts at `303268a7a293c5f91a6c8660fa36237d99eef290`. Historical voice outer email/form receipts missing co-persisted proof remain unavailable. The integration branch's separate voice evidence preservation at `651fc90c` is not in this owner's test base; combined qualification must be recorded separately. Future self-contained voice `send_email` receipts can meet the same contract; `submit_form` remains explicitly unsupported by this inspector. No historical child linkage is invented.

Google Calendar has an exact GET embedded inside mutating `update_event`, not a callable read-only event method; Outlook currently has only filtered window-list reads. The window lists cannot prove cancellation/absence, and no calendar search was introduced. No-ID email send outcomes and records missing original proof remain held.

Inspection is not atomic with later provider revocation/state changes. It does not resolve, retry or certify an uncertain action. Operator adjudication policy, designated provider acceptance, browser acceptance and release gates remain open. No feature or release gate is promoted, and the production-readiness freeze remains active. No push or deployment is included.

The final manifest records the source commit, exact commands, limits and SHA-256 hashes of raw source/artifact bytes after CRLF-to-LF normalization only.
