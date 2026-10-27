# Local customer onboarding follow-up — 27 October 2026

The user authorized the next local customer-onboarding work after deferring live infrastructure rehearsal. This work stays within CP08 and the existing production-readiness freeze. Stripe and Admin remain their separate workstreams. The checkout contained substantial existing edits; this follow-up preserves them and records current evidence rather than treating historical CP08 checkpoints as proof of the current files.

## Changes

- Signup now reports verification-email failure, preserves a newer resend during cleanup, uses the required duplicate-account wording, and includes credential issuance inside the account transaction. Session cookies are published only after commit. The unused legacy backend `/auth/register` returns 410 with instructions for the verified two-step signup; the customer registration page already uses that flow.
- Password recovery uses the existing pre-tenant database context, gives the same conditional request acknowledgement across delivery/account outcomes, and atomically consumes the matching code before updating credentials. Password, session revocation and refresh-token revocation share a transaction. If the database fails after code consumption, the user must request another code; the endpoint does not report success.
- Credential-entry failures remain local to their form instead of triggering refresh and global session-expiry handling. Callback tokens must pass canonical account validation before changing browser identity. In cookie-only mode, token-only callback links direct the user to normal sign-in because no cookie-exchange endpoint exists. Signup and recovery preserve errors and provide code resend/recovery controls.
- The dashboard links through plan, telephony, agent, campaign, contacts and review steps. This guide remains visible during analytics failures, which have a retry action. It provides navigation, not readiness or completion claims.
- Campaign creation retains the saved campaign after a knowledge-upload failure and retries the upload against that ID. Guidance uses the existing budget contract; voice loading distinguishes errors from empty results. Campaign details expose load retry and the server's start response/error.
- Type checking exposed three pre-existing unresolved campaign references. Restored the existing lead-field UI and adapter against the existing backend GET/PUT routes, preserving custom saved fields during checkbox edits. The rejected-inbound-call panel explicitly reports unavailable history: its `/calls/rejected` API is absent in this checkout. This containment does not complete rejection history.

## Local verification

The focused checks passed: **60 backend auth/recovery**, **30 frontend auth/HTTP**, and **17 campaign** tests (107 total). `npm run typecheck` passed. Scoped auth and campaign ESLint checks passed. Backend checks use synthetic database/Redis/email collaborators and a runner that disables dotenv and external database/SMTP access. This includes a real in-process ASGI check that the eleventh reset attempt is rate-limited; database rollback/Redis behaviour is checked with synthetic collaborators, not a real restricted-role database.

`npm run build` passed: optimized compilation, TypeScript, all 67 static pages and build traces completed with exit 0. The dashboard/setup ESLint check also passed. Build warnings were the existing custom Cache-Control warning and a Node module-registration deprecation. This was a local verification build using loopback API fixtures and disabled monitoring; it is not a deployment artifact and must be rebuilt with the intended environment before release.

**Browser walkthrough incomplete.** The development server served the registration page with HTTP 200, but browser navigation timed out while the memory-constrained machine was compiling. After the successful optimized build, automatic approval review rejected the attempt to launch `npm run start -- --hostname 127.0.0.1 --port 3107`, stating only `blocked by policy`. The action was not retried through another launcher. There is no completed rendered signup/dashboard/mobile walkthrough or screenshot acceptance to claim. The available browser API fixtures are synthetic and do not prove frontend-to-real-backend behaviour.

The [verification manifest](artifacts/local-onboarding-20261027/verification.json) records the distinct test groups and source hashes. No actual email, telephone call or production change was made.

## Remaining acceptance

CP08 remains **in progress**. Real designated-account inbox/device recovery, cross-tab cookie behaviour, released backend/database compatibility, an authorized first telephone call and customer usability acceptance are still required. The absent staff invitation/acceptance journey remains explicit. These local repairs do not establish paid-use readiness or lift the feature freeze. See the [first-call guide](../production-readiness/first-call-setup.md).

The existing `/auth/me` suspension lookup still uses a raw acquired connection. Its restricted-role RLS behaviour was observed during review and is not established by the local callback fixtures. This pass does not claim that the complete identity surface has passed production database acceptance.
