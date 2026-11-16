# Plan limits: what the code enforces (D5-3)

Written 2026-11-17 from the code on branch `fix/backend-day5-20261117`. Plan
rows are stored in `plans` (`features` and `not_included` are jsonb arrays of
strings; `minutes`, `agents`, `concurrent_calls`, `price` are columns). They
reach the website through `GET /plans/` and `GET /billing/plans`
(`list_plan_catalog`, `app/domain/services/billing_catalog.py:177`) and, for
Pay as you go, `GET /plans/payg` (`app/api/v1/endpoints/plans.py`). Values
come from migrations 0071 (Basic, Business), 0073 (Payment test) and 0083
(Pay as you go). Migration 0097 removes the line "Automatic campaign wizard"
from all of them (nothing gated the wizard by plan).

## For the owner

Lines that nothing in the backend enforces. They are shown to customers as
limits or inclusions. Nothing was removed or reworded: each needs your decision
(build the control, or remove the line).

- "Campaigns: 2" (Basic), "Campaigns: 10" (Business, Pay as you go): no campaign count is checked anywhere.
- "Voice recording storage: 2 GB" / "4 GB": no storage total is measured or limited.
- "Recording retention: 30 days" (Basic) / "90 days": retention is the tenant's own recording policy (default 90, settable by the tenant up to 3650), not the plan.
- "Live support: up to 1 hour/month" / "3 hours/month": no support time is tracked.
- "CRM connectors" (not included on Basic) and "HubSpot, Salesforce and supported CRM connectors": connectors are not gated by plan; a Basic tenant can connect them.
- The `agents` column (2 / 10 / 10 / 1): only quoted in the Ask AI text, never limited.
- Concurrent calls apply only while the tenant has no explicit `tenant_call_limits` row; a row overrides the plan number.

## Table

"Stored key" is the column or list the line lives in. A line is ENFORCED only
where code is named; otherwise it says NOT ENFORCED. Searches for the
NOT ENFORCED rows: `max_campaigns`, `campaign_limit`, `campaign_count`,
`storage_limit`, `storage_quota`, `max_storage`, `plan.*campaign`,
`has_feature`, `require_feature`, `plan_features`, `.features`, `live_support`,
and every `plan_id` / `plans` reference under `backend/app` (the only
read-for-limits paths are `call_guard.py` and `minutes_quota.py`).

### Basic (`basic`)

| Line as the customer reads it | Stored key | Enforcement |
|---|---|---|
| Included AI minutes: 300/month | `features`, `plans.minutes` | ENFORCED: `app/domain/services/minutes_quota.py:354` (`compute_minutes_status`) and `call_guard.py:1187` (`minutes_quota_exhausted`); new calls are refused when the month's minutes plus bought minutes are used. |
| Concurrent calls: 3 | `features`, `plans.concurrent_calls` | ENFORCED: `call_guard.py:1411` (`_plan_concurrent_calls`) feeds the limit checked at `call_guard.py:882`; the call is refused at the limit. Only when the tenant has no `tenant_call_limits` row. |
| Campaigns: 2 | `features` | NOT ENFORCED |
| Voice recording storage: 2 GB | `features` | NOT ENFORCED |
| Recording retention: 30 days | `features` | NOT ENFORCED (tenant policy `retention_days`, `recordings.py:558`, is independent of the plan) |
| Live support: up to 1 hour/month | `features` | NOT ENFORCED |
| CRM connectors | `not_included` | NOT ENFORCED (no plan check in connectors) |
| agents: 2 | `plans.agents` | NOT ENFORCED (display only, `ask_ai_constants.py:65`) |

### Business (`business`)

| Line as the customer reads it | Stored key | Enforcement |
|---|---|---|
| Included AI minutes: 1,000/month | `features`, `plans.minutes` | ENFORCED: same as Basic minutes (`minutes_quota.py:354`, `call_guard.py:1187`). |
| Concurrent calls: 10 | `features`, `plans.concurrent_calls` | ENFORCED: `call_guard.py:1411`, `call_guard.py:882` (same condition as Basic). |
| Campaigns: 10 | `features` | NOT ENFORCED |
| Voice recording storage: 4 GB | `features` | NOT ENFORCED |
| Recording retention: 90 days | `features` | NOT ENFORCED (tenant policy, not plan) |
| Live support: up to 3 hours/month | `features` | NOT ENFORCED |
| HubSpot, Salesforce and supported CRM connectors | `features` | NOT ENFORCED |
| agents: 10 | `plans.agents` | NOT ENFORCED |

### Pay as you go (`payg`, `GET /plans/payg`)

| Line as the customer reads it | Stored key | Enforcement |
|---|---|---|
| No monthly fee: buy minutes when you need them | `features`, `plans.billing_model = prepaid` | ENFORCED: `minutes_quota.py:107` (`minutes_unlimited`): a prepaid account may use only minutes it bought; calls stop at a zero balance (`call_guard.py:1187`). |
| Minutes carry over until used | `features` | ENFORCED: `app/domain/services/topup_usage.py:254` (`topup_minutes_left`) and `record_topup_usage` (:199) keep bought minutes in `billing_ledger`; bundles have no expiry (`topup_packages.expires_days` NULL). |
| Concurrent calls: 10 | `features`, `plans.concurrent_calls` | ENFORCED: `call_guard.py:1411`, `call_guard.py:882`. |
| Campaigns: 10 | `features` | NOT ENFORCED |
| Voice recording storage: 4 GB | `features` | NOT ENFORCED |
| Recording retention: 90 days | `features` | NOT ENFORCED (tenant policy, not plan) |
| HubSpot, Salesforce and supported CRM connectors | `features` | NOT ENFORCED |
| agents: 10 | `plans.agents` | NOT ENFORCED |

### Payment test (`payment_test`, team only)

| Line as the customer reads it | Stored key | Enforcement |
|---|---|---|
| Team only: one real $1 charge to check payments | `features` | ENFORCED: `billing_mode.py:45` (`TEAM_ONLY_PLAN_IDS`), applied at `billing_catalog.py:167` and `:235`; other accounts cannot see or buy it. |
| Included AI minutes: 2/month | `features`, `plans.minutes` | ENFORCED: `minutes_quota.py:354`, `call_guard.py:1187`. |
| Cancel it in Manage subscription after the check | `features` | Instruction to the customer; nothing to enforce. |
| agents: 1 | `plans.agents` | NOT ENFORCED |
