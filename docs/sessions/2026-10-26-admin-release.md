# Admin release candidate — 26 October 2026

The user has authorized deployment and another QA pass. This record describes release preparation; it does not certify production activation or full paid-use acceptance.

## Candidate

The release workspace is on `codex/admin-release-20261026`, based on main commit `454b8efe330253d9678dfc85e93935b4fe2b3478`. It incorporates the tested Admin remediation from the isolated `codex/admin-premortem-20261021` workspace. The original workspace and unrelated changes in the main checkout are preserved.

The three-way merge retains main's silent-pickup scheduling and post-notice utterance-ID rotation alongside the Admin account-authority, admission and termination changes. A fresh 259-case backend merge gate passes across ten existing modules with no failures or skips and no database/provider connections.

The current Admin frontend passes all 932 tests with no failures, cancellations or skips, full lint and the TypeScript/Vite production build. Its full dependency audit reports zero vulnerabilities after updating the transitive development-only Zod package from 4.3.6 to 4.6.7. Vite remains at main's patched 7.3.7; the existing large-chunk build warning remains. The separate full Admin PostgreSQL and customer Next.js release gates are still running. Promotion requires their final results and resolution of any failed cases.

The initial staged-source secret scan reports no leaks. Browser captures and new database/session evidence bundles are not included in this source publication. Detailed local reports remain available in the remediation and release workspaces; historical test counts are not evidence for changed release inputs.

## Observed production state

Read-only checks on 26 October found `/opt/talky` clean at `2739eeabf7d091ffa63b7dc0695019da77f972f9`. API, gateway and worker services were active, and the backend health probe returned HTTP 200. PostgreSQL 15.17 was at `0070_trunk_soft_delete`; the candidate requires the six additive migrations through `0076_admin_user_operations`.

The configured database login/effective role was `talkyai`, without PostgreSQL SUPERUSER or BYPASSRLS. This observation does not prove the new tables' post-migration privileges. The latest backup service execution reported success at 02:34 UTC; that service result alone does not prove the dump's contents or restorability. The inbound synthetic timer was not installed. Interactive sudo is required in the available SSH account.

## Frontend hosting

The existing Vercel project `talkleeai` serves the customer Next.js site from `Talk-Leee`. The dedicated `talkleeai-admin` project is configured for Vite from `Admin/frontend`, Node 24, `npm ci`, `npm run build` and output directory `dist`.

Admin uses `https://api.talkleeai.com/api/v1`; both development-auth flags are false. Its Vercel configuration provides SPA deep-link rewrites. The intended custom origin is `https://admin.talkleeai.com`. DNS is managed externally at GoDaddy and requires Vercel's recorded CNAME and ownership-verification TXT entries. Project creation, a preview, or DNS verification is not production feature acceptance.

The running backend's exact CORS allowlist does not yet include the Admin origin. Add that explicit origin during the coordinated release while preserving the existing cookie/authentication contract. Do not replace the customer project or publish a new Admin UI against an incompatible backend.

The first Admin-only upload explicitly requested a preview, but Vercel classified it as production and assigned the new project's default aliases. That exact deployment was removed after verifying its project/team identity. The deployment and its three observed URLs now return 404, the Admin project has no active deployment target, and the customer project's production deployment is unchanged. No backend login or business action was attempted during this upload. A further upload must use a verified preview mechanism or wait for the coordinated production activation.

## Activation order and remaining gates

1. Freeze one reviewed commit, complete its security/build/test gates and obtain a compatible fresh backup and recovery proof.
2. Use the existing production procedure to disable new ingress and origination, observe zero gateway/Asterisk/Redis/database activity and obtain the fresh candidate-bound drain manifest required by `deploy_to_server.sh`. Generic deployment authorization is not a substitute for those measured operating facts.
3. Use authenticated operator sudo and the supported pinned Git/systemd deployment. Coordinate dependency parity, schema migration, managed Asterisk/nginx configuration and the matching gateway/backend. Preserve saved receipts, decisions, revisions and revoked credentials.
4. Run `backend/scripts/verify_admin_runtime_privileges.py` under the actual configured API environment after migration and before admitting new Admin writes. This additional gate is documented but is not automatically called by the current deployment script.
5. Require backend readiness and the expected migration head, then promote the matching frontends and verify the custom Admin origin, cookie/CORS behavior, normal login/MFA and designated-role workflows.

Existing E1–E6 acceptance requirements remain in force, including real provider/PBX effects, operating recovery, unfinished supplier-cost capability/reconciliation and policy decisions. The feature freeze remains active. No production backend checkout, migration or restart, or customer-frontend promotion, has occurred. The temporary Admin-only promotion and its verified removal are recorded above.
