# CP08 / OP11 endpoint auth audit cleanup

Date: 2026-10-05. Base: `25c5c779ea871d3820457d434b6ed3e8ea11cbce`.
Test-only source: `995abceb84a883342f2f49d11a7c659c1742ece4`.

The old `_KNOWN_AUTH_GAPS` collection exempted 17 existing protected routes and
one deleted route. Read-only introspection of the resolved FastAPI dependency
tree found `get_current_user` on all 17 existing routes. The sole missing route
was `DELETE /api/v1/rbac/roles/{role_id}/permissions/{permission_id}`. Its actual
replacement accepts DELETE at `/roles/{role_id}/permissions`.

Only `backend/tests/unit/test_endpoint_auth_audit.py` changed: remove that obsolete
collection and make stale public-route entries fail instead of skip. The 25
intentional auth-flow, webhook and public exceptions are unchanged. No application
authorization, permission grants, role policy, router, or audit scanner changed.

## Verified current guards

All paths below begin with `/api/v1`. Permission and role checks resolve through
the current session/principal dependency; they do not rely only on JWT role claims.

| Existing routes | Current guard | Source |
| --- | --- | --- |
| GET `/admin/audit/stats/events-by-type` | `audit:read` | `backend/app/api/v1/endpoints/audit_logs.py:284` |
| GET `/admin/audit/stats/failed-logins` | ANY of `audit:read`, `security:monitor` | `backend/app/api/v1/endpoints/audit_logs.py:323` |
| GET `/admin/security-events/events`, `/events/{event_id}`, `/alerts/open`, `/alerts/overdue` | `security:read` | `backend/app/api/v1/endpoints/security_events.py:101`, `:166`, `:362`, `:395` |
| POST `/admin/security-events/events`; PATCH `/events/{event_id}`; POST `/events/{event_id}/resolve` | `security:write` | `backend/app/api/v1/endpoints/security_events.py:190`, `:252`, `:320` |
| POST `/admin/security-events/events/{event_id}/escalate` | `security:escalate` | `backend/app/api/v1/endpoints/security_events.py:428` |
| POST and DELETE `/rbac/roles/{role_id}/permissions` | platform-admin role | `backend/app/api/v1/endpoints/rbac/roles.py:143`, `:201` |
| GET `/rbac/users/{user_id}/permissions` | tenant-admin minimum; target-tenant/target-user checks | `backend/app/api/v1/endpoints/rbac/users.py:65` |
| GET `/rbac/tenant-users` | current identity plus exact requested-tenant membership; ordinary members see active memberships only | `backend/app/api/v1/endpoints/rbac/tenant_users.py:64`, `:78` |
| POST `/rbac/tenant-users`; PATCH and DELETE `/rbac/tenant-users/{tenant_user_id}` | tenant-admin minimum plus exact target membership and role ceiling | `backend/app/api/v1/endpoints/rbac/tenant_users.py:130`, `:252`, `:398` |

The permission helper has **ANY**, not ALL, semantics and an explicit platform
administrator bypass (`dependencies.py:560`). The current `Permission` enum has
no `audit:*` or `security:*` values; `get_user_permissions` discards unknown enum
values. This review does not establish a tenant-admin entitlement to these audit
or security surfaces, introduce those grants, or approve a different role policy.
It establishes that these are not unauthenticated exceptions.

## Existing negative evidence and limits

- `tests/unit/test_auth_dependency_availability.py`: missing/malformed credentials,
  verification outages, conflicting cookie/Bearer identity, revoked session and
  inactive membership exercise the real shared dependency through synthetic HTTP.
- `tests/integration/test_cp08_current_identity.py:162`, `:172`, `:189`, `:213`,
  `:626`: existing PostgreSQL-backed controls cover session revocation, inactive
  membership, demotion, foreign member-list query, and higher-tier member mutation.
  These integration tests were inspected, not rerun in this cleanup.
- `tests/unit/test_rbac_tenant_users_escalation.py:78` tests role-assignment ceilings.
- `tests/security/test_idor_tenant_scoping.py:298` exercises security-event
  update/resolve/escalate against same and foreign tenants using synthetic methods.
- `tests/security/test_tenant_param_cannot_override.py` is source-level coverage;
  it is not an authenticated HTTP test. No dedicated mounted-HTTP negative suite
  for every audit/security/role-permission route was found in this bounded search.

The existing auth audit remains a dependency/source inventory, not proof of every
endpoint's complete authorization behavior. This change restores its coverage of
the 17 routes without changing that broader test design.

## Validation

Exact overlay order: the RT `exact-requirements-20261005/packages` directory first,
then OP02's `op02-testdeps` Lua test overlay; original backend `.venv` interpreter.
Observed versions: PyJWT 2.15.1, urllib3 2.8.0, FastAPI 0.139.0, pytest 7.4.4.
The runner uses synthetic environment values and denies ordinary socket connects
and DNS; Windows asyncio's internal socketpair construction is explicitly allowed.
No database, provider, email, or external HTTP operation was performed.

Both runs invoked the same complete modules, without deselection:

```text
pytest tests/unit/test_endpoint_auth_audit.py
       tests/unit/test_auth_dependency_availability.py
       tests/unit/test_api_endpoints.py -q -rs
```

- [Valid baseline](artifacts/cp08-auth-audit/baseline.txt): **41 passed, 1 skipped**;
  the skip names the deleted DELETE route.
- [Final run](artifacts/cp08-auth-audit/final.txt): **42 passed, 0 skipped**;
  four existing FastAPI deprecation warnings.
- [CI-selected Ruff](artifacts/cp08-auth-audit/lint.txt): clean (`--select F --ignore F401,F841`).
  `git diff --check` passed.
- [Initial launcher failure](artifacts/cp08-auth-audit/harness-socket-failure.txt)
  is retained: denying Windows' internal socketpair prevented event-loop creation
  (12 failed, 3 passed, 1 skipped, 52 errors). This was a validation-harness issue,
  not a product or scanner regression; the app/test source was still unchanged.
- Independent reviewer `/root/realtime_audit` read the final one-file diff and
  found no material issue. The reviewer did not independently rerun tests.

No deployment or live authorization acceptance is claimed. Existing production
readiness gates and deferred work remain unchanged.
