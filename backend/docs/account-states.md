# Account states: what is refused, what stays allowed

Source of truth for the `account_restrictions` field on `GET /auth/me`
(`app/api/v1/endpoints/auth/profile.py`, `account_restrictions()`, table
`ACCOUNT_RESTRICTIONS_BY_STATE`). Line numbers are for the Day 5 branch; the
test `tests/unit/test_day5_roles_and_account_state.py` probes every code below
against the real enforcing function for every state.

## The three states

`GET /auth/me` derives `account_state` in `profile.py:_derive_account_state`
(a formal hold outranks the subscription):

| `account_state` | Reason code | Source |
|---|---|---|
| `active` | none | anything else. Billing states `past_due`, `cancelled`, `unpaid` are NOT reported here (see "Gap" below) |
| `suspended_subscription` | `subscription_suspended` | `tenants.subscription_status = 'suspended'` (Admin "Suspend") |
| `suspended_hold` | `tenant_hold` / `partner_hold` | `tenants.status = 'suspended'` or the partner is suspended (formal hold) |

`account_restrictions` codes: `sign_in`, `start_campaign`, `place_calls`,
`buy_minutes`, `receive_inbound_calls`.

## suspended_subscription

`account_restrictions = ["start_campaign", "place_calls", "buy_minutes", "receive_inbound_calls"]`

| Code | Refused where | What the caller sees |
|---|---|---|
| `start_campaign` | `campaigns.py:186-211` `_require_account_can_call`, called at `:1192` (POST `/campaigns/{id}/start`) | 403 `{"code":"tenant_suspended","reason":"tenant_suspended","message":...}` |
| `place_calls` | `call_guard.py:528-541` `_check_tenant_active` -> `call_authority.py:183` (`TENANT_BLOCKED_STATUSES`); also `call_guard.py:676` `_check_subscription`. Callers: dialer `workers/dialer_worker.py:1857`, SIP originate `telephony_bridge.py:2116` | dialer: job blocked (reason `tenant_suspended`); originate route: 429 `{"error":"call_blocked","reasons":["tenant_suspended",...]}` (`telephony_bridge.py:2173`) |
| `buy_minutes` | `payg.py:67-88` `tenant_purchase_audience`, used by `billing_topups.py:74-84`; checkout `:275`; package list `:182-186` returns `[]` | checkout: 409 with a plain sentence, no code field (the code on `PurchaseRefused` is not sent); packages: empty list |
| `receive_inbound_calls` | `inbound_admission.py:795` (subscription not active/trialing, reason `subscription_inactive`) and `:845` `read_call_authority` | the call is denied at admission (not an HTTP response) |

Stays allowed (no suspension check exists on these routes; verified by grep
for `read_call_authority`, `_require_account_can_call`, `tenant_purchase_audience`,
`TENANT_BLOCKED_STATUSES` over `app/`; they are only referenced by the files above):

- Sign-in and every authenticated request: the principal loader
  (`core/security/principal.py:37-56`) reads formal authority with purpose
  PRINCIPAL, which ignores billing state (`call_authority.py:183` applies only to ADMISSION).
- **Billing**: `billing.py` has no account-state check (grep `suspended|subscription_status|call_authority`: no match), so plan, invoices, subscription checkout, portal, balance (`billing_topups.py:200`) stay reachable. Only the minute top-up purchase is refused (above).
- **Support**: `api/v1/endpoints/support/` has no account-state check (grep: no match), so reports, tickets and the bot stay reachable.
- Reading and editing campaigns, contacts, settings; pause and stop (`campaigns.py:1363`, `:1450`) have no check.

## suspended_hold (formal hold)

`account_restrictions = ["sign_in", "start_campaign", "place_calls", "buy_minutes", "receive_inbound_calls"]`

| Code | Refused where | Result |
|---|---|---|
| `sign_in` | `auth/login.py:240-252` and `passkeys.py:547` via `principal.py:98` -> `_require_formal_account_access` (`principal.py:37-56`); coded by `principal.py:22-34` | 403 `{"code":"tenant_suspended","reason":...,"message":"This account is on hold, so sign-in is paused. Contact support."}` |
| every signed-in request | `dependencies.py:299`, `:318-322` (principal load on each request) | 401 "Your account or login context is no longer available. Please sign in again." |
| open sessions | `core/security/sessions/lifecycle.py:236-244` revokes the session (`tenant_suspended`, `partner_suspended`) | next request 401 |
| `start_campaign`, `place_calls`, `buy_minutes`, `receive_inbound_calls` | same functions as above; `call_authority.py:185-208` returns `formal_account_hold` / `tenant_<status>` / `formal_partner_inactive` | as above |

Consequence: a held member cannot reach `/auth/me` (it returns 401), so the
`suspended_hold` value of `account_restrictions` is only visible to a caller
that is not subject to the principal hold check (a platform admin).
Billing and Support are NOT reachable during a hold, because every route sits behind sign-in.

## Decision for the owner (D5-28): Billing and Support during a hold

Nothing is built. Today a hold means no access at all: sign-in refused
(`auth/login.py:250-252`), existing sessions revoked (`lifecycle.py:236-244`),
every request refused (`dependencies.py:318-322`). Two options:

1. **No access** (what runs today): the owner confirms that a hold means no
   Billing and no Support through the app; the customer is told on the sign-in
   refusal message ("Contact support").
2. **Limited session** for Billing and Support only: a new session class that
   passes `principal.py` for a short allow-list of routes (`/billing/*`,
   `/support/*`, `/auth/me`) and is refused everywhere else. This needs new
   enforcement in the principal loader and the session lifecycle, a route
   allow-list, and tests that every other route stays refused.

## Gap (not fixed, reported)

Other billing states are refused by the call guard but are reported as
`active` here, so `account_restrictions` is empty for them:
`cancelled`/`canceled` (`TENANT_BLOCKED_STATUSES`), and `past_due`, `unpaid`,
`incomplete_expired` (`SUBSCRIPTION_BLOCKED_STATUSES`,
`subscription_status.py:86-99`, enforced at `call_guard.py:676`). Extending
the field to them needs a decision on whether they are new `account_state` values.
