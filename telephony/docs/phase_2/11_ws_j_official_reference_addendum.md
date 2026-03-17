# WS-J Official Reference Addendum

Date: February 25, 2026  
Phase: 2 (Tenant Self-Service + Policy Automation)  
Workstream: WS-J (Auditability + Operations)

---

## 1) Purpose

This addendum maps WS-J design and operational decisions directly to official sources.
It exists to keep WS-J production-safe and reviewable.

---

## 2) Decision-to-Source Mapping

| WS-J Decision | Official Source | Why It Matters |
|---|---|---|
| Immutable audit trail via DB trigger path | PostgreSQL `CREATE TRIGGER` + PL/pgSQL trigger docs | Ensures mutation capture logic is in DB transaction path, not best-effort app logs |
| Tenant isolation on policy and audit tables | PostgreSQL RLS docs | Prevents cross-tenant data exposure at database layer |
| Request correlation from API -> DB session | PostgreSQL `set_config` / `current_setting` docs | Creates deterministic request-to-audit linkage |
| Rollback latency metrics (`p50/p95/max`) | PostgreSQL aggregates (`percentile_cont`) docs | Provides measurable rollback SLO tracking |
| Standard API error envelope | RFC 9457 | Consistent, machine-readable failure handling for ops tooling |
| JWT security baseline | RFC 8725 | Avoids weak token-validation patterns in operator-sensitive endpoints |

---

## 3) WS-J Production Invariants

1. No policy mutation is considered complete unless audit row creation succeeds.
2. No tenant-level operation is accepted without tenant-scoped DB context.
3. Every mutate/rollback request must carry:
   - `Idempotency-Key`
   - `X-Request-ID`
4. Runtime event stream must include `started` and terminal status for rollback.
5. Rollback latency must remain observable through runtime metrics endpoint.

---

## 4) Operator Checklist (Human Version)

Before approving a release:
1. Confirm WS-J verifier passes.
2. Confirm rollback drill evidence exists and is recent.
3. Confirm audit row coverage for all tracked policy tables.
4. Confirm alerting/observability wiring still reads runtime metrics endpoint.
5. Confirm no open P1 defects in runtime activation/rollback path.

---

## 5) Official Links

1. PostgreSQL `CREATE TRIGGER`:
   - https://www.postgresql.org/docs/current/sql-createtrigger.html
2. PostgreSQL trigger procedures:
   - https://www.postgresql.org/docs/current/plpgsql-trigger.html
3. PostgreSQL RLS:
   - https://www.postgresql.org/docs/current/ddl-rowsecurity.html
4. PostgreSQL config settings functions:
   - https://www.postgresql.org/docs/current/functions-admin.html
5. PostgreSQL aggregate functions:
   - https://www.postgresql.org/docs/current/functions-aggregate.html
6. RFC 9457:
   - https://www.rfc-editor.org/rfc/rfc9457
7. RFC 8725:
   - https://www.rfc-editor.org/rfc/rfc8725
