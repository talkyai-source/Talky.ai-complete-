# Backend Day 5 list: what the server does now

Answers to the frontend "Backend — Day 5" list (D5-1 to D5-29), written
17 November 2026 from the code on branch `fix/backend-day5-20261117`.
Every change ADDS fields or routes; no existing field was renamed or removed.
All paths are under `/api/v1`. The error envelope is unchanged:
`{"error": {"code", "message", "details", "request_id"}}`.

## Summary

| Item | State | Where to look below |
|---|---|---|
| D5-1 AI option availability | Done | AI options |
| D5-2 "Automatic campaign wizard" plan line | Removed | Plans |
| D5-3 Which plan limits are enforced | Written | Plans |
| D5-4 Error-tracking library | Added to requirements | Operations |
| D5-5, D5-7, D5-23 Analytics totals, short windows, time zone | Done | Analytics |
| D5-6, D5-20, D5-21, D5-22 Call list | Done | Calls |
| D5-8 Inbound switch for a view-only role | Done | Roles |
| D5-9 Telephony reads per role | Answered: admin only | Roles |
| D5-10, D5-11, D5-12 Sign-in answers | Done | Sign-in |
| D5-13 Import row errors | Done | Contacts |
| D5-14 Connector status | Done | Connectors |
| D5-15 Server sentences | Done | Sentences |
| D5-16 ElevenLabs errors | Done | AI options |
| D5-17 Email, Reminders, Meetings, Developer Hub routes | Not built (owner decision) | Not built |
| D5-18 Top-up return reference | Read route added | Billing |
| D5-19 Audit actor name | Done | Audit |
| D5-24 Readable audit reference | Done | Audit |
| D5-25, D5-26, D5-29 Campaign list phase, completed, calling rules | Done | Campaigns |
| D5-27 Suspended account policy | Done | Account state |
| D5-28 Billing and Support during a formal hold | Written up (owner decision) | Account state |

## AI options (D5-1, D5-16)

- `GET /ai-options/providers`: every model, speech engine and voice carries
  `available` (bool) and `unavailable_reason` (string or null). Each section
  carries `provider_status`. A provider is available when its key, or its
  key pool, is set on the server.
- The one sentence for every unavailable case:
  "This option is not switched on for this account yet." with the code
  `ai_option_unavailable`. No answer names a server setting any more.
- Saving an option whose provider is unavailable is refused: 422 for
  Deepgram and Google speech (new), the existing 503 / 400 for the others,
  all with that sentence.
- There is no "approved" flag: the code has no approved list beyond the menu
  it already offers.
- The voice list returns `elevenlabs_error` as a fixed sentence plus
  `elevenlabs_error_code: "voice_catalog_unavailable"` in place of the
  provider's own text.

## Plans (D5-2, D5-3)

- Migration 0097 removes "Automatic campaign wizard" from every plan list.
  `GET /plans/` and `GET /plans/payg` no longer send it. The public pricing
  table row was removed in the same change.
- `backend/docs/plan-limits-enforcement.md` lists every plan line with the
  code that enforces it. Enforced: included minutes, concurrent calls, the
  prepaid balance. NOT enforced (owner to decide): campaign count, recording
  storage, plan recording retention, support hours, connectors by plan.

## Analytics (D5-5, D5-7, D5-23)

- `GET /analytics/calls`: rows and the new `summary` carry `other`, so
  answered + failed + other = total. `other` is a call with no outcome yet,
  in progress, or a dialer-only value.
- `GET /analytics/calls?window_hours=1|4|8|24` returns
  `buckets: [{start, end, total_calls, answered, failed, other}]`,
  `bucket_minutes` (15 for 1 and 4 hours, 30 for 8, 60 for 24) and
  `window_hours`. Any other value, or combining it with `from` / `to`, is 422.
- `GET /analytics/calls/by-campaign` rows carry `other`. The dashboard
  carries `other_calls`.
- `/auth/me` already returned `account_timezone` and `reporting_timezone`
  (null means UTC). No change was needed.

## Calls (D5-6, D5-20, D5-21, D5-22)

- `GET /calls/?lead_filter=leads|issues` filters before paging with the rule
  the calls page uses today. Leads: a captured phone or email, or a lead
  outcome starting `qualified` or `callback`. Issues: status busy / failed /
  no_answer, or outcome busy / failed / no_answer / rejected / timeout /
  unavailable, or a lead outcome starting `no_interest` or `disqualified`.
- `search` also matches the captured phone and email when it is three
  characters or longer.
- `GET /calls/?changed_since=<ISO time>` returns the calls whose row changed
  at or after that time, newest change first, same row shape. Page-number
  paging only: with a cursor it is 422.
- Each list row and the call detail carry `best_time_to_call` (string or null).
- `backend/docs/call-status-values.md` lists every call status, outcome,
  inbound readiness code and dialer block code.
- Known differences: the server uses the stored validation state of a
  captured contact, not the transcript projection the page computes; the
  page's search also matches the outbound caller number, the server's does not.

## Roles (D5-8, D5-9)

- `GET /inbound-campaigns/controls` now needs only the inbound read
  permission. A caller without the controls permission gets the state only:
  `{"inbound_enabled": false, "version": 7, "reason": null, "updated_at": null}`.
  Changing the switch is unchanged.
- D5-9: the three telephony reads (`/telephony/sip/trunks/pool`,
  `/pool-assignment`, `/outbound-route`) are for account admins only and stay
  so (`tenant_admin`, `partner_admin`, `platform_admin`). Other roles get 403.
  The Settings page now shows those roles a plain note in the Telephony tab
  and makes no telephony request for them.

## Sign-in (D5-10, D5-11, D5-12)

- `POST /auth/mfa/verify` 401 codes: `two_factor_session_expired` (the
  sign-in step expired or was already used: start again) and
  `two_factor_failed` (wrong code).
- Sign-in: 401 `sign_in_locked` with `details.retry_after_seconds`. The lock
  is keyed on the address typed, so it never shows whether an account exists.
- Sign-in: 403 `email_not_verified` ("Verify your email before signing in.")
  only after a correct password.
- `POST /auth/verify-email/resend {"email": "..."}` always answers 200
  `{"message": "If that address needs verifying, we have sent a new email."}`.
  429 when it is called too often from one place (5 a minute, 20 a day). A
  single address is also limited to 3 an hour, silently: the answer is still 200.
- The website page `/auth/verify-email?token=...` exists now.

## Contacts (D5-13)

- Each import row error is a plain sentence plus a stable `code`
  (`phone_missing`, `phone_invalid`, `phone_country_required`, `save_failed`,
  `update_failed`, `row_too_many_cells`, `email_invalid`, `timezone_invalid`,
  `contact_method_invalid`, `do_not_call_invalid`, `mobile_number_invalid`,
  `business_number_invalid`, `value_too_long`, `value_invalid`,
  `country_invalid`).
- The import answer adds `errors_total`, `errors_truncated`,
  `field_errors_total`, `field_errors_truncated`. Lists are cut at 100.
- A refused import (422) is a list of `{row, field, code, message}` cut at
  100, ending with `{row: null, field: null, code: "more_errors", ...}` when cut.

## Connectors (D5-14)

- `GET /connectors/status` items add `display_name`, and for an expired or
  error state a fixed `message` and a stable `code` (`check_unavailable`,
  `credential_missing`, `authorization_cannot_refresh`, `permissions_missing`,
  `refresh_unresolved`, `authorization_expired`, `connector_error`).

## Sentences (D5-15)

- Rate limit: "Too many requests. Wait a moment and try again." with
  `details.retry_after_seconds`.
- Password reset, sign-in and two-factor failures no longer name internal
  parts ("Redis", "MFA", setting names). Calendar connector failures are one
  plain sentence each.

## Billing (D5-18)

- The address a customer returns to after a top-up already carried
  `order_id` (our own id) on success and cancel. New:
  `GET /billing/topups/{order_id}` returns that order's stored state:
  `{"id", "package_code", "minutes", "price_cents", "currency",
  "currency_exponent", "status", "created_at", "paid_at"}`. Another account's
  id and an unknown id are both 404.
- `request_id` on a return address still means a plan purchase only.

## Audit (D5-19, D5-24)

- Audit rows carry `actor_name`. Labels for rows with no person: "System",
  "Service", "API key", "Deleted user", "Unknown user", "Unnamed user".
- Who can read the audit log is unchanged: in code only a platform admin.
- The admin account-change receipt adds `audit_reference_short`
  (for example `AUD-1A2B3C4D`). The full id is unchanged.

## Campaigns (D5-25, D5-26, D5-29)

- `GET /campaigns` rows carry `progress`:
  `{"live_calls": 0, "jobs_awaiting_dispatch": 250, "phase": "preparing"}`,
  the same object as the detail. `progress` is null if the count read failed.
- A running outbound campaign with nothing left to do becomes `completed`.
  "Nothing left" means no job waiting or scheduled, no call in progress, no
  contact still waiting and no callback due. A campaign with a contact that is
  still waiting stays `running`. There is no `failed` status. Start accepts a
  completed campaign like a stopped one, and the website offers Start for it.
- `GET /campaigns/calling-rules` returns `effective_schedule` (what a new
  campaign of this account will be held to) and `legal_floor`, before any
  campaign exists.
- Create and update accept `first_speaker` (`agent` or `user`), the same
  value Start accepts.

## Account state (D5-27, D5-28)

- `/auth/me` adds `account_restrictions`: `[]` for an account in good
  standing; `["start_campaign", "place_calls", "buy_minutes",
  "receive_inbound_calls"]` for a suspended subscription; a formal hold adds
  `"sign_in"`. Every code is something the server already refuses.
- `backend/docs/account-states.md` lists, per state, what is refused and
  where, and what stays reachable. Billing and Support stay reachable for a
  suspended subscription. During a formal hold nothing is reachable; the two
  options are written there for the owner.

## Operations (D5-4)

- `sentry-sdk` is in `backend/requirements.txt`. It reports nothing until
  `SENTRY_DSN` is set on the server.

## Not built

- D5-17 (Email, Reminders, Meetings, Developer Hub routes) and D5-28
  (a limited session during a formal hold): owner decisions.
- A `failed` campaign status (D5-26) and an "approved" flag for AI options
  (D5-1): neither exists in the product.
