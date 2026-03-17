# Phase 2 Official Reference Baseline

Date verified: February 24, 2026  
Scope: Tenant self-service SIP onboarding, per-tenant policy automation, quotas/abuse controls, and auditable operations.

---

## 1) Phase 2 Outcome

Phase 2 must deliver:
1. Tenant-managed SIP onboarding without manual config edits on servers.
2. Policy-as-data for routing, codec behavior, and trust controls.
3. Abuse and quota controls that are deterministic and enforceable per tenant.
4. Full operational auditability of tenant-driven changes.

---

## 2) Official Sources and Why They Matter

| Area | Official Source | Key Capability Used in Phase 2 |
|---|---|---|
| Kamailio routing | Kamailio `dispatcher` module docs | DB-backed destinations, hash-based selection, runtime reload (`dispatcher.reload`) |
| Kamailio tenant domain mapping | Kamailio `domain` module docs | DB-backed domain validation for multi-tenant routing boundaries |
| Kamailio trust policy | Kamailio `permissions` module docs | Trusted source/table-based ACL controls (`allow_source_address`, trusted table modes) |
| Kamailio flood defense | Kamailio `pike` module docs | Per-source flood detection with configurable density/sampling |
| Kamailio method throttling | Kamailio `ratelimit` module docs | Algorithms for per-key request limiting (`TAILDROP`, `RED`) |
| Kamailio shared counters | Kamailio `htable` module docs | In-memory hash tables with auto-expire for runtime policy state |
| FreeSWITCH dynamic config | FreeSWITCH `mod_xml_curl` docs | Runtime XML retrieval for directory/dialplan/profile data |
| FreeSWITCH runtime reload | FreeSWITCH `mod_commands` docs (`reloadxml`) | Controlled config refresh after validated policy updates |
| Data isolation | PostgreSQL `Row Security Policies` docs (current) | Per-row access controls at database layer for tenant isolation |
| Audit trail | PostgreSQL `CREATE TRIGGER` docs (current) | Deterministic change logging for config mutations |
| Policy documents | PostgreSQL JSON/JSONB docs (current) | Structured policy storage and indexable JSONB operators |
| Counter semantics | Redis `INCR` and `EXPIRE` command docs | Atomic counters and time-window expiry patterns for quotas |
| API error standard | IETF RFC 9457 | Standardized error responses (`application/problem+json`) |
| JWT hardening | IETF RFC 8725 | Current best-current-practice guidance for JWT security |

---

## 3) Extracted Technical Facts (Official)

## 3.1 Kamailio

1. `dispatcher` supports DB-backed sets via `modparam("dispatcher", "db_url", ...)`.
2. Runtime update path exists through RPC commands, including reload operations.
3. Multiple dispatch algorithms are officially supported; consistent hashing (`hash over Call-ID`, `From`, etc.) is available for deterministic routing.
4. `permissions` supports address/trusted tables and source checks, enabling per-tenant trust policy enforcement.
5. `pike` and `ratelimit` are complementary:
   - `pike` detects abusive source patterns.
   - `ratelimit` controls request throughput with tunable algorithms.
6. `htable` supports TTL/auto-expire and is suitable for in-memory policy/counter support.

## 3.2 FreeSWITCH

1. `mod_xml_curl` provides dynamic XML configuration from an external HTTP service.
2. This allows tenant-specific directory/dialplan/profile materialization without static file sprawl.
3. `reloadxml` exists as an operational control command for XML config refresh flow.

## 3.3 PostgreSQL

1. Row-level security can be enabled per table and enforced through policies.
2. Policy expressions are evaluated per-row before user predicates, which is critical for tenant isolation hardening.
3. Trigger mechanisms support deterministic audit capture on insert/update/delete operations.
4. JSONB supports operators and indexing strategies for policy documents.

## 3.4 Redis

1. `INCR` provides atomic increment semantics for numeric keys.
2. `EXPIRE` controls key TTL and is used to establish rolling/fixed windows for counters.
3. Together, these primitives support robust quota and abuse-control counters.

## 3.5 IETF Standards

1. RFC 9457 defines the current standard for HTTP API problem detail responses.
2. RFC 8725 defines JWT security best current practices and should guide token usage/validation strategy.

---

## 4) Production Rules for Phase 2 (Derived from Official Sources)

1. No tenant config applied directly to server files without validation and audit write.
2. All tenant data writes must pass DB-level isolation checks (RLS) and service-level authorization.
3. Runtime route/policy changes must be reload-safe and idempotent.
4. Abuse controls must be implemented at SIP edge and API edge, not only in one tier.
5. Every policy mutation must create an immutable audit event with actor, tenant, before/after state, and request correlation ID.
6. Error surfaces must use standardized problem detail responses for operational clarity.

---

## 5) Phase 2 Evidence Requirements

1. Official-doc traceability:
   - Every implemented Phase 2 feature references a specific official source section.
2. Determinism:
   - Replaying the same onboarding payload is idempotent and produces no duplicate active routes.
3. Isolation:
   - Negative tests prove one tenant cannot read/write another tenant’s policies.
4. Safety:
   - Fault injection during policy reload does not break active call handling.
5. Auditability:
   - Every change is queryable and attributable.

---

## 6) Official Links

1. Kamailio dispatcher module:
   - https://kamailio.org/docs/modules/stable/modules/dispatcher.html
2. Kamailio domain module:
   - https://kamailio.org/docs/modules/stable/modules/domain.html
3. Kamailio permissions module:
   - https://kamailio.org/docs/modules/stable/modules/permissions.html
4. Kamailio pike module:
   - https://www.kamailio.org/docs/modules/stable/modules/pike.html
5. Kamailio ratelimit module:
   - https://www.kamailio.org/docs/modules/stable/modules/ratelimit.html
6. Kamailio htable module:
   - https://www.kamailio.org/docs/modules/stable/modules/htable.html
7. FreeSWITCH mod_xml_curl:
   - https://developer.signalwire.com/freeswitch/FreeSWITCH-Explained/Modules/mod_xml_curl_1049001/
8. FreeSWITCH mod_commands:
   - https://developer.signalwire.com/freeswitch/confluence-to-docs-redirector/display/FREESWITCH/mod_commands
9. PostgreSQL row security (current docs):
   - https://www.postgresql.org/docs/current/ddl-rowsecurity.html
10. PostgreSQL CREATE TRIGGER (current docs):
   - https://www.postgresql.org/docs/current/sql-createtrigger.html
11. PostgreSQL JSON types (current docs):
   - https://www.postgresql.org/docs/current/datatype-json.html
12. Redis INCR:
   - https://redis.io/docs/latest/commands/incr/
13. Redis EXPIRE:
   - https://redis.io/docs/latest/commands/expire/
14. RFC 9457:
   - https://www.rfc-editor.org/rfc/rfc9457.html
15. RFC 8725:
   - https://www.rfc-editor.org/rfc/rfc8725
