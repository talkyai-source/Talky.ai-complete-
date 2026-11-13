# Superseded manual SQL

These files are historical evidence. **Do not run them, add another manual
migration, or replay their data updates.** Use the supported bootstrap floor
and `alembic upgrade head` described in [MIGRATIONS.md](../MIGRATIONS.md).
The `_archive/` directory remains intact for provenance.

The June–July files are accounted for as follows. A later revision can retain a
stronger current contract than the historical SQL; the old file is not the
authoritative desired state.

| Historical file | Current Alembic/bootstrap coverage |
| --- | --- |
| `20260605_add_tenant_assistant_model.sql` | 0087 adds the missing nullable `tenants.assistant_model`. |
| `20260607_add_calls_summary_json.sql` | 0048 CRM deliveries adds `calls.summary_json`. |
| `20260609_call_failure_and_lead_signals.sql` | 0084 folds in the call/lead signals; 0065 includes the stream `call` category. |
| `20260610_leads_active_phone_unique.sql` | 0087 adds the missing historical named index; both bootstraps already have the equivalent older index. No contact deletion/deduplication. |
| `20260610_processed_webhook_events.sql` | 0054 supplies the current receipt/reclaim state, beyond the old claim-only table. |
| `20260612_dialer_job_dedup.sql` | 0060 supplies the corrected uniqueness rule. The historical sibling-cancellation UPDATE must not be replayed. |
| `20260615_cloned_voices.sql` | Both supported bootstrap snapshots include its table/index; 0038 supplies current RLS. |
| `20260629_add_tenant_stt_engine.sql` | 0022 repairs the tenant setting. |
| `20260703_add_contact_lists.sql` | 0041 supplies the table plus current ownership/RLS. |
| `20260703_add_tenant_ai_realtime_config.sql` | 0022 repairs these tenant settings. |
| `20260703_add_trunk_live_status.sql` | 0029 includes the trunk live status. |
| `20260703_add_trunk_live_status_detail.sql` | 0029 includes live status details. |
| `20260706_add_admin_health_tables.sql` | 0044 covers webhooks; 0072/0073 cover incidents; 0087 adds the missing worker status table/index. |
| `20260713_add_calls_dialer_job_id.sql` | 0028/0041 include the call-to-job relationship. |
| `20260717_calls_lead_id_nullable.sql` | 0018/0040 cover the nullable call relationships. |
| `20260729_widen_refresh_tokens_revoked_reason.sql` | 0087 recognizes the old/current CHECK and widens only the old/missing form after a zero-invalid-row preflight. |

0087 also supplies the tables skipped by the conservative 0008 stamp:
`stream_events`, `tenant_ai_credentials`, `tenant_telephony_credentials`, and
`emergency_access_requests`, plus `tenants.active_telephony_provider`. Late
creators use the current `call` event category, active-only AI credential unique
index (0067), and forced tenant RLS rather than reviving weaker old definitions.

The preserved snapshot additionally contains `audit_chain_state`,
`audit_event_types`, `rate_limit_events`, and `suspension_propagation_queue`.
0087 creates these inert missing tables for clean bootstrap metadata parity;
it does not enable a new application path. Existing compatible objects and
their data remain in place. Unknown shapes/security definitions cause refusal.
