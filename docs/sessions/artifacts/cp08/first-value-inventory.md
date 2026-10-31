# CP08 bounded first-value and staff setup inventory

Read-only source review, 2026-10-05. No designated customer account, database, email, passkey device, carrier, or call was exercised for this inventory. Proposed setup steps are not measured completion results.

## Existing staff contract

- `backend/app/api/v1/endpoints/rbac/tenant_users.py:add_user_to_tenant` accepts an existing `user_id`, verifies that profile exists, then inserts/updates an immediately active membership. `invited_by`/`invited_at` columns record administration; they do not constitute invitation delivery, an expiring acceptance token, or user acceptance.
- `Admin/frontend/src/pages/UsersPage.tsx:AddUserModal` is a platform-admin user creation form with name, email, temporary password, role and optional tenant. It explicitly asks the operator to share the password securely. It is not an email invitation form.
- `Talk-Leee/src/app/auth/callback/page.tsx` accepts a `type=invite` query value only in its generic no-token branch and redirects to sign-in. It does not prove invitation ownership or attach a tenant membership.
- Bounded searches through current Next/Admin UI and backend endpoints found no staff invitation creation/delivery/acceptance contract. The CP08/F05 requirement remains unavailable/unverified. Admin-assisted setup does not close it; no invitation service is proposed in this slice.
- Concrete integration issue reported to parent/identity owner: current `backend/app/api/v1/endpoints/admin/users.py:create_user` inserts a verified, active profile with the chosen tenant but did not insert a matching `tenant_users` membership at review time. The new canonical principal rejects a nonplatform account without active membership, despite the route's immediate-sign-in claim. Parent coordinates the repair; this inventory does not claim it is fixed or tested.

## Ordered use of existing pages

1. Use canonical verified signup/sign-in, then `/billing/plans` and `/billing` to inspect actual saved entitlement. A default plan label alone is not entitlement proof.
2. In `/settings`, use `TelephonyProvidersSection`/`SipTrunksList` for the intended active provider, selected trunk and its actual direction-specific readiness. Confirm the caller number is owned and verified. A configured number string or seeded platform route is not verification. Where number verification/provisioning has no end-user control, disclose the existing authorized operator/API step; do not invent a UI button or require undocumented SQL.
3. In `/ai-options`, save the approved provider/model/voice profile. Use `/campaigns/new` (`CampaignWizard`: Basics, Knowledge, Review), then the campaign's `KnowledgePanel` to inspect the supplied knowledge and ready version.
4. In `/contacts`, prepare permitted contacts for the intended campaign. For initial validation, use only an explicitly authorized owned destination and a bounded single-contact scope; this review does not authorize any call.
5. `CampaignStartControl` uses existing `/campaigns/{id}/readiness` and displays the backend reason if route/caller-ID checks are blocked. Existing backend admission and entitlement/quota checks remain authoritative. Do not infer complete readiness from one green route field.
6. Campaign `TestAgentButton` opens the browser microphone/WebSocket agent path. It can help inspect configured conversation behavior, but it is not a PSTN/carrier/caller-ID presentation test. A separately authorized real test call and its reviewed receipts are still needed for the first-call requirement.

No unified first-value checklist or completed designated-buyer trace was located or executed. The parent owns the concise setup guide and remaining usability/first-call acceptance; the proposal above does not close F06 or CP08.

## Independent cross-slice review

The current HTTP/Auth/WebAuthn review identified one new concrete integration issue: optimistic nonplatform login results missing `tenant_id` were being represented as a verified empty-tenant identity, so the subsequent `/auth/me` repair request carried `X-Talky-Expected-Tenant: ""` and conflicted with a real tenant. Parent reports keeping that optimistic identity unverified, and the credential owner adds tenant IDs to MFA/passkey issuance responses; their own tests provide final evidence.

Follow-up read review confirmed the parent's missing-tenant change on disk. The parent also independently reproduced and repaired an OAuth token bridge mismatch: `api.setToken` had written persisted storage while AuthProvider's authoritative ref stayed null. The final bridge delegates API token writes to the shared HTTP storage writer, synchronously updates that ref, and uses the canonical storage helper honoring the bearer-persistence preference. Its new callback-sequence test and aggregate validation are parent-owned evidence; no additional test run or source edit was performed by this reviewer.

No other new material defect was found in the bounded reviewed changes. HTTP checks cover request completion and the existing asynchronous body-reader consumers; refresh is scoped to the original origin/token/identity epoch; logout pending timestamps no longer cause an unbound future logout. WebAuthn option conversion preserves server RP/UV/credential lists and decodes their binary IDs. JavaScript cannot undo a `Set-Cookie` header already applied by the browser for an earlier in-flight response; these checks must not be described as eliminating that separate browser commit race.
