# CP08 Next server authority and logout boundaries

Date: 2026-10-05. Working tree: `tmp/production-ready-20261004`. This slice is uncommitted pending the parent integration commit. It does not certify production deployment, browser cookie delivery, provider authentication, or PostgreSQL behavior.

## Confirmed defects and bounded changes

- The Next local catchall could execute a separate legacy users/session auth implementation. `/auth`, `/auth/*`, and `/me` now return HTTP 503 with `auth_backend_unavailable` before reading credentials, cloning/reading bodies, claiming idempotency, or touching legacy auth storage. After independent review, the parent also authorized a production-only guard for all remaining catchall routes, after the existing specific auth/billing guards: HTTP 503 `api_backend_unavailable`. Historical local tokens can no longer reach that route's separate `sessions JOIN users` authorization or writes in production. This is containment, not completed fallback features or a new proxy. Configure the existing `NEXT_PUBLIC_API_BASE_URL` to use the canonical backend. Development compatibility remains, except already-retired auth/billing routes.
- `getServerMe` required the obsolete mirror cookie and could retry a second `/me` authority. It now forwards only canonical `talky_at`/`talky_sid` to configured `/auth/me`, validates required principal fields, and returns null on missing config, rejection, malformed data, or network failure. Existing server redirect callers remain unchanged.
- The proxy role reader used `next.revalidate: 30` and an alternate `/me` lookup. It now makes a canonical `/auth/me` request with `cache: "no-store"`; no configured backend means no local authority fallback. Existing route/redirect and cookie-migration admission policies are unchanged.
- `logoutAllOtherSessions` swallowed individual failures. It now waits for all attempted revocations and rejects with confirmed/unconfirmed counts if any result is uncertain. The current session is excluded and no automatic retry is added.
- The standalone logout button bypassed AuthContext, and both logout controls could navigate after a newer login. Both now use the root-owned `{serverConfirmed, identityCurrent}` context result. A stale completion does not clear caches or navigate. An unconfirmed current logout goes to `/auth/login?logout=unconfirmed`; the root-owned login banner explains the uncertainty. Unexpected context exceptions lack identity ownership proof, so the controls show an error without navigation. Sidebar retains a full reload on an owned completion.

## Reproduction and verification

All fixtures are synthetic. `fetch`, auth API methods and navigation are intercepted; no external auth, email, provider, or database operation is performed. Server tests use the installed Next request stores and actual `cookies()`/`getServerMe`; proxy and local route exports are invoked directly. Logout tests render the real controls with real AuthProvider and synthetic API acknowledgments. Navigation interception is JSDom proof, not a deployed browser login test.

Installed runtime for these checks: Node 25.8.1, exact-lock Next 16.3.8. Relevant installed Next docs read: `cookies.md`, `fetch.md`, and `15-route-handlers.md` under `node_modules/next/dist/docs`.

Commands run from `Talk-Leee`:

```text
node --test --test-concurrency=1 --import tsx --import ./src/test-utils/setup.ts src/lib/auth-local-route.test.ts src/lib/server-auth.test.ts src/proxy.auth.test.ts
```

Before source changes: **1 passed / 20 failed** (`next-auth-initial.txt`). The failures preserve credential-read admission, missing canonical cookie acceptance, fallback authority, and stale role-fetch options.

```text
node --test --test-concurrency=1 --import tsx --import ./src/test-utils/setup.ts src/lib/auth-local-route.test.ts src/lib/server-auth.test.ts src/proxy.auth.test.ts src/proxy.contact.test.ts src/proxy.trailing-slash.test.ts src/lib/billing-local-route.test.ts
```

After source changes: **34 passed / 0 failed / 0 skipped** (`next-auth-final.txt`). Successive synthetic responses prove current response handling and `no-store` request options, not production Next cache behavior or physical cookie propagation.

```text
node --test --test-concurrency=1 --import tsx --import ./src/test-utils/setup.ts src/lib/session-utils.test.ts src/components/auth/logout-controls.test.tsx
```

Before logout changes with corrected JSDom animation fixture: **3 passed / 6 failed** (`logout-before.txt`). Final: **9 passed / 0 failed / 0 skipped** (`logout-final.txt`). An earlier exploratory log `logout-initial.txt` includes three missing-JSDom-animation failures and is not counted as product reproduction. During verification a zero-retention QueryClient fixture was corrected to retain the unobserved B query until explicit cleanup; the final test checks that a delayed A logout leaves B's query intact.

```text
npx eslint 'src/app/api/v1/[...path]/route.ts' src/lib/server-auth.ts src/proxy.ts src/proxy.contact.test.ts src/proxy.auth.test.ts src/lib/auth-local-route.test.ts src/lib/server-auth.test.ts src/lib/session-utils.ts src/lib/session-utils.test.ts src/components/auth/logout-button.tsx src/components/auth/logout-controls.test.tsx src/components/layout/sidebar.tsx
```

Exit 0, no diagnostics (`next-auth-lint.txt`). Parent owns combined typecheck/build and source binding; this slice makes no separate build claim.

Additional production containment command:

```text
node --test --test-concurrency=1 --import tsx --import ./src/test-utils/setup.ts src/lib/canonical-backend-route.test.ts src/lib/auth-local-route.test.ts src/lib/billing-local-route.test.ts
```

Final: **18 passed / 0 failed / 0 skipped** (`local-authority-final.txt`). Four admission controls failed before the production guard (`local-authority-before.txt`); that exploratory log also contains one incorrect assumption that the development templates route was public. The positive control was corrected to the existing public `/health` route. This is a direct route admission proof before auth/DB access, not a real historical-session exploit or a claim of current production exposure. Post-delta lint on the route, new admission test, and logout component fixture exits 0 without diagnostics (`local-authority-lint.txt`). The logout router fixture includes Next's required `bfcacheId`, rather than weakening its type.

## Source ownership

Application: `Talk-Leee/src/app/api/v1/[...path]/route.ts`, `src/lib/server-auth.ts`, `src/proxy.ts`, `src/lib/session-utils.ts`, `src/components/auth/logout-button.tsx`, `src/components/layout/sidebar.tsx`.

Tests: new `src/lib/auth-local-route.test.ts`, `src/lib/canonical-backend-route.test.ts`, `src/lib/server-auth.test.ts`, `src/proxy.auth.test.ts`, `src/lib/session-utils.test.ts`, `src/components/auth/logout-controls.test.tsx`; existing `src/proxy.contact.test.ts` receives only explicit synthetic backend configuration and restoration. Test counts from overlapping batches are not additive.

Root owns `AuthContext`, HTTP identity/refresh handling, API logout, and the login warning. No changes were made to those files by this slice. No source commit, push, browser launch, PostgreSQL start, or deployment was performed.
