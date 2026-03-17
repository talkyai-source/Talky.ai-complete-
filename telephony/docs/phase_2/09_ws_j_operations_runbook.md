# WS-J Operations Runbook

Date: February 25, 2026  
Phase: 2 (Tenant Self-Service + Policy Automation)  
Workstream: WS-J (Auditability + Operations)  
Audience: On-call engineers and release operators

---

## 1) Why This Exists

This runbook tells you what to do when a telephony runtime policy change fails.

It focuses on:
1. Activation failures
2. Partial apply/verify failures
3. Rollback execution and proof
4. Audit evidence validation

Use this runbook only after WS-F through WS-I are already green.

---

## 2) Preconditions

Before you start, confirm:
1. API is reachable.
2. PostgreSQL is reachable.
3. You have a valid tenant admin token.
4. Every mutating call includes:
   - `Idempotency-Key`
   - `X-Request-ID`
5. Migration `20260224_add_tenant_policy_audit_ws_j.sql` is applied.

---

## 3) Fast Triage Signals

Check these first:
1. Activation failures:
   - `tenant_runtime_policy_events.action='activate'`
   - `tenant_runtime_policy_events.status='failed'`
2. Rollback failures:
   - `tenant_runtime_policy_events.action='rollback'`
   - `tenant_runtime_policy_events.status='failed'`
3. Rollback latency and counters:
   - `GET /api/v1/telephony/sip/runtime/metrics/activation`
4. Audit coverage:
   - `tenant_policy_audit_log` has request-linked entries for the same mutation window.

---

## 4) If Activation Fails

Follow this exact order:
1. Capture the failing `X-Request-ID` from API logs.
2. Query `tenant_runtime_policy_events` using that `request_id`.
3. Identify first failed stage:
   - expected failure stage is usually `apply` or `verify`.
4. Confirm runtime active-state integrity:
   - there must be exactly one active version in `tenant_runtime_policy_versions`.
5. If state is unsafe or uncertain, execute rollback:
   - `POST /api/v1/telephony/sip/runtime/rollback`
   - use a new `Idempotency-Key` and a new `X-Request-ID`.
6. Confirm rollback succeeded:
   - terminal event: `action='rollback' AND status='succeeded'`
   - metrics endpoint reflects rollback counters and latency.

---

## 5) If Apply/Verify Partially Fails

Do this:
1. Identify failed stage from runtime events.
2. Freeze further activations for that tenant until recovery is complete.
3. Roll back to last-known-good version.
4. Re-validate:
   - active version is stable
   - event stream is continuous (`started` to terminal status)
   - audit rows exist for affected policy tables
5. File incident ticket with:
   - tenant ID
   - failing request ID
   - failing stage
   - rollback request ID
   - restored policy version

---

## 6) Rollback Drill (Mandatory)

Run this drill before release sign-off:
1. Activate version `N`.
2. Activate version `N+1`.
3. Roll back to version `N`.
4. Verify:
   - active version is `N`
   - rollback has both `started` and `succeeded` events
   - audit entries include `request_id`
   - rollback latency is visible in runtime metrics endpoint.

---

## 7) Audit Evidence Query

```sql
SELECT
  tenant_id,
  table_name,
  action,
  request_id,
  correlation_id,
  actor_user_id,
  created_at
FROM tenant_policy_audit_log
WHERE tenant_id = $1
ORDER BY created_at DESC
LIMIT 100;
```

Audit acceptance:
1. Every policy mutation writes an audit row.
2. Rows contain tenant/action/request metadata.
3. Payload expectations:
   - `INSERT`: `after_payload` populated
   - `UPDATE`: both `before_payload` and `after_payload`
   - `DELETE`: `before_payload` populated

---

## 8) Retention and Housekeeping

Run controlled prune job:

```sql
SELECT prune_tenant_policy_audit_log(5000);
```

Production guidance:
1. Run on a schedule (for example, every 15 minutes).
2. Track prune volume and table growth in capacity review.
3. Change retention only through reviewed migration and change control.

---

## 9) Official Standards and Docs

1. PostgreSQL `CREATE TRIGGER`:
   - https://www.postgresql.org/docs/current/sql-createtrigger.html
2. PostgreSQL trigger function variables (`TG_OP`, `NEW`, `OLD`):
   - https://www.postgresql.org/docs/current/plpgsql-trigger.html
3. PostgreSQL row-level security:
   - https://www.postgresql.org/docs/current/ddl-rowsecurity.html
4. PostgreSQL runtime session settings (`set_config`, `current_setting`):
   - https://www.postgresql.org/docs/current/functions-admin.html
5. PostgreSQL aggregate/percentile functions:
   - https://www.postgresql.org/docs/current/functions-aggregate.html
6. RFC 9457 (`application/problem+json`):
   - https://www.rfc-editor.org/rfc/rfc9457
