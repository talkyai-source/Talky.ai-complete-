# OP11: honest Admin usage evidence and privilege inventory

Status: bounded reporting repair verified locally; OP11 remains incomplete.

Source/essential fixture commit: `95b54dc37f3fc9956c7d632d36e558b6e78ba0a1`.

Worktree: `tmp/production-ready-op11-20261005`, branch `codex/production-ready-op11-20261005`, base `28004d3a`. No production inspection, deployment, supplier request, PostgreSQL start/query, financial mutation, new price source or ledger was performed. Checked-in service units are an inventory, not evidence of current deployed process ownership.

## Reproduced reporting defects

The actual Admin summary endpoint method, with synthetic database rows, returned USD 5.35 for one 600-second Asterisk call carrying legacy `cost=5` and one failed SMS action. It attributed USD 0.33 to Deepgram, USD 0.01 to Groq, USD 5 to Twilio voice and USD 0.01 to Twilio SMS. Its query did not read provider identity, action outcome or provider usage evidence. A second input of two 59-second calls with unknown costs produced one minute/USD 0.05 in the summary and zero minutes/USD 0.00 in the tenant breakdown.

The first new endpoint controls recorded **10 failed, 1 passed**. Separately, **11 date-boundary controls failed**: the report used `created_at <= YYYY-MM-DD`, excluding most of its selected end day, accepted invalid/reversed periods and did not express an exclusive next-day UTC boundary. Original failures are retained in `artifacts/op11/usage-initial.txt` and `date-boundary-initial.txt`.

## Bounded repair

- `backend/app/api/v1/endpoints/admin/usage.py` no longer invents provider rates or attribution. Supplier `total_cost` and type/tenant monetary projections remain null, with `supplier_cost_status=unavailable`; provider lists remain empty with explicit unavailable attribution. This does not mean no calls occurred or that suppliers charged zero.
- A separate `legacy_outbound_estimate` carries only finite, recorded outbound `calls.cost` values: nullable `recorded_total`, `covered_call_count`, `missing_call_count`, legacy USD currency and `unavailable`, `partial` or `recorded_rows_only` coverage. A genuine recorded zero is distinct from missing, malformed or non-finite money. Even all-row coverage is **not supplier-complete**. Inbound monetary evidence remains excluded rather than combining currencies.
- Recorded duration is summed in seconds before display flooring. Each tenant's duration and the total footer use recorded seconds; customer billing, meter rounding and admission policy are unchanged.
- `total_action_records` labels saved assistant-action records. The legacy `total_api_calls` field remains only a compatibility alias for those records; it is not a count of provider HTTP requests or successful effects.
- Dates use explicit UTC start-inclusive/end-exclusive boundaries; the selected end date includes its full day. Invalid, compact-format or reversed dates return a useful 400 before queries. Raised errors, adapter error envelopes and missing result data return a generic 503 rather than zero usage or private connection details.
- The three existing Admin consumers and shared API types handle nullable money: `UsageBreakdownCard` (used by Usage & Cost and Connectors), `QuotaUsage` (Command Center), and `UsageCostPage`. Loading/read errors do not show invented zero values. Actual recorded counts can still be zero. Empty provider attribution has its own unavailable message while actual call/action counts remain visible.
- `provider_cost_ledger.py` documentation now describes optional, lossy operational telemetry. Full `backend/app` inspection found only startup/shutdown flusher imports and no provider calls to `record`/`CostEvent`/quantity parsers. No active pricing/reconciliation job or Admin consumer of its event table was found. Starting a flusher or observing an empty/unpriced event table cannot establish complete usage or zero supplier spend. Recorder implementation was not changed.

Backend and Admin must be promoted compatibly: older Admin clients containing `?? 0` cannot display the new nullable response honestly. No alternate reporting or billing API was added.

## Local verification and limits

`artifacts/op11/validation-manifest.json` records commands, runtime versions and exact attribution. Final backend controls use actual endpoint methods with synthetic fluent database results; they are not real SQL/RLS or supplier reconciliation. The observed synthetic endpoint JSON is retained in `api-example.json` and passed into real React server-rendered Admin presentation components. Those server-render tests prove displayed data semantics, not browser interactions, HTTP authentication or a deployed dashboard. Three additional actual component callback/effect controls use a minimal synthetic hook scheduler to exercise delayed-response ordering; this is not real React browser concurrency acceptance.

Final scoped results: **48 backend tests passed**, zero skips, one unrelated warning; **23 Admin tests passed**, zero skips; full Admin app typecheck, production Vite build, changed-file ESLint and Ruff F checks passed. Root independently read the bounded backend/Admin/telemetry changes and found no material defect.

The existing inbound currency tests were updated to assert the preserved exclusion and separate legacy source instead of the removed fabricated provider totals. They continue to reject mixing the inbound example's 99-unit monetary amount into legacy USD cost.

The first frontend render run exposed a locally authored dash encoded incorrectly; it was corrected to an ASCII separator, and the final run passed. The first changed-file lint run rejected synchronous loading resets in an effect; the component now keys results to the requested tenant/date tuple and ignores replaced requests. Both initial outputs are retained rather than relabeled as successful evidence.

LLM independent review identified a retained tenant-table period race: a late A response could replace B rows or overwrite B's failure. Two actual component-callback controls reproduced it before repair (`period-order-initial.txt`). The tenant table now uses a request generation, cleanup invalidation and resolved query key; it ignores stale successes/errors and does not present old-period rows/counts under new dates. A third stale-error control joins the final Admin batch. LLM final read review confirmed the generation/resolved-query correction and found no additional material defect; no independent test or browser run is claimed.

## Checked-in privilege inventory only

The eleven files in `backend/systemd/*.service` currently specify:

| Units | Checked-in account configuration |
| --- | --- |
| API, voice worker, dialer worker, reminder worker, cleanup, migrate, database backup, healthwatch | No `User` or `Group`; system-unit default would be root unless deployment adds overrides |
| Inbound synthetic and trunk-status updater | Explicit `User=root` |
| Voice gateway | Explicit `User=admins`, `Group=admins` |

These unit files contain no `NoNewPrivileges`, `ProtectSystem`, `PrivateTmp`, `CapabilityBoundingSet` or `ReadWritePaths` directives. That inventory does not prove a present exploit or today's deployed unit configuration. No unit or runtime privilege was changed.

Before changing accounts, the operator must validate the existing permission paths: `/opt/talky/backend/.env` and encryption/config reads; configured local recording directories; application/runtime writes; DB/Redis/network access; `/etc/asterisk/pjsip.d` atomic file creation and group readability; exact Asterisk CLI reload/status commands; backup output/container access; and migration credentials. `pjsip_config_generator.py` already documents the setgid Asterisk group directory and 0640 files, while the trunk-status unit explicitly cites Asterisk CLI/config access. Do not replace these needs with blanket sudo or a speculative `User=` edit.

## Acceptance still open

OP11 requires an approved least-privilege account/permission inventory and exercised call, persistence, restart, maintenance and key-access paths before release. Supplier statements, covered event windows, retries/failures/credits and unexplained differences still require reconciliation. No supplier total, deployment ownership, financial completeness, scanner closure or feature-freeze completion is inferred from this local reporting repair.
