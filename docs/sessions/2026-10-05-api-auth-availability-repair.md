# API smoke failures: credential ordering and catalog fixture

Base: `1b33f6eb87abc73a539e461d8b1efe9fbed77da4`. Date: 5 October 2026.
Source: `4b4629401b893c09d7deee6825007e67f2b61e8e`.
Isolated branch: `codex/api-unit-repro-20261005`.

## Reproduction and classification

The root full unit/security run reported eleven failures in
`tests/unit/test_api_endpoints.py`. Running that module alone against the same
source reproduced **11 failed, 1 passed**; cross-test pollution was not required.
All eleven ended at `RuntimeError: Container not initialized`.

- **One stale fixture:** the plans route now depends on `get_db_pool` and the
  canonical `list_plan_catalog`, while its smoke test still overrode the former
  Supabase-style `get_db_client` dependency. The fixture now supplies a synthetic
  async pool to the actual catalog, with no paid price options/provider requests.
  A storage-failure control requires 503 rather than a fabricated empty catalog.
- **Ten auth failures, with a real error-boundary gap:** `get_current_user`
  acquired a database client before rejecting missing/malformed credentials.
  Although an ordinarily started application initializes its container, credential
  rejection itself does not need that dependency. Session database reads were
  also outside the existing unavailable-verification error boundary. Synthetic
  accepted-token HTTP controls reproduced raw storage exceptions on those paths.

The first 21 new dependency HTTP controls produced **13 failures, 8 passes** on
the unchanged application. Six missing/malformed credential cases, six client or
session outage cases, and a conflicting-access-identity no-storage assertion failed.
The already protected principal-read failures and matching-identity/revoked-session
controls passed. Five further controls were added for access-cookie parsing,
missing session binding, legacy-cookie conflict/absence and current membership
rejection.

## Small repair

Missing credentials, invalid Bearer syntax, invalid tokens, absent session binding
and conflicting access identities are now rejected before acquiring storage.
Client acquisition and session reads join the existing principal-verification
error boundary. Expected authentication exceptions retain their 401/409 statuses;
unavailable live verification returns the generic existing 503 message. No default
user, tenant, role, missing-session acceptance or stale-profile fallback was added.
Current session, cookie agreement, principal/membership and expected-identity
checks remain in place. Metering behavior is unchanged.

Application ownership is only `app/api/v1/dependencies.py`. Test ownership is
`tests/unit/test_api_endpoints.py` plus the new
`tests/unit/test_auth_dependency_availability.py`.

## Final observed verification

Six modules: **62 passed, 1 skipped, 4 warnings** in 16.50 seconds. The skip is the
pre-existing stale-public-route allowlist check in `test_endpoint_auth_audit.py`;
it is not an authentication behavior control. All 13 API smoke tests and all 26
new dependency HTTP controls passed. Existing JWT, CP08 identity-header/CORS,
endpoint-auth inventory and session-middleware RLS-boundary controls were included.
The CI Ruff selection passed; `git diff --check` passed.
Root independently reviewed the application and fixture/test changes and found no
material defect. That review is not another test execution count.

```text
python -m pytest tests/unit/test_api_endpoints.py tests/unit/test_auth_dependency_availability.py tests/security/test_cp08_principal_contract.py tests/unit/test_jwt_security.py tests/unit/test_endpoint_auth_audit.py tests/unit/test_session_middleware_rls_bypass.py -q --tb=short
```

The final run uses the same sanitized dummy-credential environment as the root
full suite: `ENVIRONMENT=test`, unavailable loopback database port 1, Redis host
127.0.0.1/port 6379, no backend `.env` or integration database opt-in. The standalone
initial reproduction additionally used Redis port 1; none of its failures reached
Redis. No database was started or queried, and no provider was called.

The new HTTP tests invoke the actual FastAPI dependency with synthetic accepted
JWT/session/principal seams. They prove ordering, status and authority checks at
those boundaries, not real PostgreSQL RLS, deployed cookies, mailbox recovery or
cross-device acceptance. The full-app smoke tests use an unstarted lifespan and a
synthetic catalog pool. The final result is a focused follow-up, not a replacement
for the root full-suite run.

Evidence: [initial API failures](artifacts/api-endpoint-repair/initial.txt),
[initial dependency failures](artifacts/api-endpoint-repair/auth-initial.txt),
[final run](artifacts/api-endpoint-repair/focused-final.txt),
[exact final command](artifacts/api-endpoint-repair/focused-command.json), and
[CI-rule Ruff](artifacts/api-endpoint-repair/ruff-ci.txt).
Text logs have trailing whitespace normalized; observed outcomes are unchanged.
