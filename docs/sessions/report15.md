# Report 15 — Goals-Driven Frontend Completion and Live Vercel Release

**Prepared:** 2026-09-01 (Asia/Karachi)
**Previous report:** `report14.md`
**Release commit:** `48743d7f8a7e13f9da0592a9b49b9f2b5c03ec56`
**Release branch used:** `codex/frontend-goals-20260901`
**Published ref:** `HEAD -> origin/main`
**Previous main:** `aa0ed789ccf8853536dec7b7338f2b9ad1b3c6d4`
**Production frontend:** `https://talkleeai.vercel.app`
**Vercel project:** `talkleeai`
**Vercel result:** `success — Deployment has completed`
**Direct production result:** `HTTP 200`
**Database migrations in this release:** none
**Backend runtime changes in this release:** none
**Report commit status:** intentionally untracked and excluded from Git
**Report line count:** 4417

## Executive verdict

The requested frontend changes have been committed, pushed directly to GitHub `main`, accepted by the configured Vercel project and served by the production alias with HTTP 200.

This release improves truthfulness and operator safety rather than pretending unfinished backend features exist. Lead interest is derived from the persisted outcome, actionable AI summaries request human review without invented confidence, contact operations expose the full supported field set, campaign creation controls which fields the agent may use, reward totals come from the immutable ledger, and Salesforce is visible but deliberately unavailable until the backend implements it.

The release does not claim that the complete goals.md program is finished. Live inbound greeting/transfer canaries, complete Salesforce OAuth/sync, durable contact/lead audit events, review model/trace snapshots, reward abuse controls, the 200-tenant execution evidence, prompt call matrix and full controlled-release evidence remain open.

## Release decision

| Decision | Result | Evidence |
|---|---|---|
| Push frontend changes to GitHub main | Completed | Remote main equals release commit |
| Trigger configured Vercel production deployment | Completed | Vercel commit status succeeded |
| Confirm production alias responds | Completed | HTTP 200 from talkleeai.vercel.app |
| Include backend work from dirty primary checkout | No | Isolated worktree and commit manifest contain no backend paths |
| Include report15.md in Git | No | Report created after push and remains untracked |
| Claim Salesforce production support | No | UI fails closed until server advertises real capability |
| Claim all goals are complete | No | Remaining items are documented in this report |

## What changed, outcome by outcome

### 1. Truthful interested-lead state

- **Before:** The green Interested badge was derived from whether any structured detail existed. A caller could provide a name and still reject the offer, yet the UI could label the call as interested.

- **After:** The badge now derives only from the persisted post-call lead_outcome verdict. Positive verdicts map to interested, explicit negative verdicts map to not_interested, and telephony-only or missing values stay unknown.

- **Files:** Call detail page, lead-details panel, lead-outcome helper and its unit tests.

- **Safety property:** No inference from field presence; no invented positive lead; missing verdict fails neutral.

- **Proof:** Positive, negative, missing and telephony-only verdict tests all passed in the 315-test suite.

### 2. Honest AI-summary review signal

- **Before:** The summary had provenance information but no visible confidence or needs-review signal. The API does not expose calibrated confidence.

- **After:** Actionable classifications and qualification evidence display Needs review with a plain-language explanation. The UI deliberately does not fabricate a percentage.

- **Files:** CallSummaryCard component plus focused component tests.

- **Safety property:** Human confirmation is requested before follow-up or CRM synchronization.

- **Proof:** Tests prove actionable summaries show the signal and ordinary summaries do not invent numerical confidence.

### 3. Expanded contact creation and editing

- **Before:** The manual contact form omitted job title, preferred contact method and do-not-call controls even though the backend contract carried them.

- **After:** Create/edit now handles job title, mobile/business numbers, company, call window, timezone, notes, preferred method and operational DNC.

- **Files:** Contacts page, extracted contact-form serializer and serializer test.

- **Safety property:** Values are trimmed once in a pure serializer; DNC is sent as an explicit boolean.

- **Proof:** The serializer test asserts operational fields and the full production build type-checks the shared API contract.

### 4. Responsive operational contact display

- **Before:** Desktop and mobile rows emphasized only phone, name and email.

- **After:** Cards and table rows surface company/role, calling window, timezone, contact preference and a priority DNC status.

- **Files:** Contacts page responsive card and table views.

- **Safety property:** DNC takes visual precedence over lead styling so a suppressed person is not presented as ready to call.

- **Proof:** Typecheck, lint and the 67-route production build passed.

### 5. Campaign-level agent field policy

- **Before:** Campaign creation could not explicitly choose which contact fields the voice agent may see or require.

- **After:** Classic creation, knowledge-guided creation and knowledge-driven edit expose the server-owned contact-field registry with explicit access and requiredness.

- **Files:** Campaign lead-fields component, campaign form, campaign wizard, campaign edit page and API client.

- **Safety property:** Save fails closed when the field registry cannot load; no accidental broad field set is sent.

- **Proof:** Component/API tests validate defaults, requiredness changes, campaign-scoped encoded URLs and PUT payloads.

### 6. Duplicate-safe campaign creation retry

- **Before:** Adding a second post-create policy write could create duplicate campaigns if the first campaign write succeeded and the policy write failed.

- **After:** The created campaign ID is retained and reused. Retrying completes the field-policy write instead of issuing a second campaign creation.

- **Files:** Classic campaign form and knowledge-guided campaign wizard.

- **Safety property:** The error message states that campaign details exist and that retry will finish the policy.

- **Proof:** Code review verified both creation flows retain and reuse the returned ID.

### 7. Review reward ledger visibility

- **Before:** The admin review dashboard did not expose the current user's verified reward-ledger balance.

- **After:** A fifth stat reports ledger-backed points, verified entry count, disabled state or ledger-unavailable state.

- **Files:** Admin review page, extended API contract and endpoint test.

- **Safety property:** Configuration alone is never presented as proof of an award; zero entries and disabled rewards have distinct wording.

- **Proof:** The API test asserts GET /calls/reviews/rewards/balance and parses the ledger response.

### 8. Salesforce fail-closed interface

- **Before:** There was no Salesforce card in the connector interface.

- **After:** Salesforce is visible, but when the production backend does not advertise the capability it displays Unavailable and exposes no Connect or Reconnect action.

- **Files:** Connectors page, connector card, connector utility union, dev API status and connector-card tests.

- **Safety property:** The UI cannot start a dead or misleading OAuth flow against a backend that only supports HubSpot CRM.

- **Proof:** A component test proves the Unavailable status, explanation and absence of a Connect button.

### 9. Truthful goals ledger

- **Before:** Review analytics, needs-review behavior and campaign field selection were still unchecked after implementation.

- **After:** Only verified achievements are checked. Salesforce remains explicitly open, and its note states that the frontend card is not the connector implementation.

- **Files:** goals.md.

- **Safety property:** Open live canaries, audit gaps, Salesforce, 200-tenant execution and release evidence remain open.

- **Proof:** The committed goals snapshot is reproduced line-for-line in this report.

## Verification matrix

| Check | Result | Evidence |
|---|---|---|
| Local code review | **PASS** | 25 committed paths reviewed for correctness, security, performance, regressions and missing tests. |
| git diff --check | **PASS** | No whitespace errors before commit. |
| Frontend tests | **PASS** | 315 total; 313 passed; 0 failed; 2 database-dependent tests skipped. |
| TypeScript | **PASS** | npm run typecheck exited 0. |
| ESLint | **PASS** | npm run lint exited 0. |
| Production build | **PASS** | Next.js 16.3.3 webpack build exited 0 and generated 67 static pages/routes in the route manifest. |
| Secret Placeholder Guard | **PASS** | GitHub check completed successfully. |
| Secret Scan (gitleaks) | **PASS** | GitHub check completed successfully. |
| SQL Schema Validation | **PASS** | GitHub check completed successfully. |
| GitHub Frontend job | **PASS** | GitHub Actions frontend job completed successfully. |
| Vercel deployment | **PASS** | Vercel commit status: success — Deployment has completed. |
| Production alias | **PASS** | HEAD https://talkleeai.vercel.app returned HTTP 200. |
| GitHub Backend job | **FAIL (unrelated)** | Alembic 0001 refused an empty CI database because database/complete_schema.sql was not applied first; no backend file changed in this release. |

## Git evidence

- Release commit: `48743d7f8a7e13f9da0592a9b49b9f2b5c03ec56`.
- Commit message contains exactly two authored lines and no co-author trailer.
- Commit totals: 25 files changed, 953 insertions, 125 deletions.
- Remote `refs/heads/main` and local release HEAD were both resolved to the full release SHA after push.
- The release base was fetched immediately before commit and matched `origin/main`.
- The primary checkout's unrelated backend modifications were never staged, committed, reset, moved or overwritten.

## Deployment evidence

- GitHub Vercel status: `success`.
- Vercel description: `Deployment has completed`.
- Vercel dashboard target: `https://vercel.com/allestateestimation-9391s-projects/talkleeai/5WXiTzpKw8GLAAX93aiFHowLjpSV`.
- Production project alias checked: `https://talkleeai.vercel.app`.
- Direct result after Vercel success: HTTP 200.
- GitHub Frontend job: completed successfully.
- GitHub Secret Placeholder Guard: completed successfully.
- GitHub Secret Scan/gitleaks: completed successfully.
- GitHub SQL Schema Validation: completed successfully.

## GitHub backend CI failure — exact interpretation

The monorepo workflow is red because the separate Backend job's Alembic round-trip creates a blank PostgreSQL database and immediately runs `alembic upgrade head`. Migration `0001_baseline` is a baseline marker, not a complete-schema creator. It intentionally checks for the `tenants` table and raises with instructions to apply `database/complete_schema.sql` first. CI did not perform that prerequisite.

This is not caused by the frontend release:

- No file under `backend/` changed in the release commit.
- SQL Schema Validation passed.
- Frontend CI passed.
- Vercel deployment passed.
- The live production alias responds.

The CI workflow should be repaired separately by applying `backend/database/complete_schema.sql` to the temporary database before the Alembic round-trip, or by using the repository's intended baseline-stamp procedure. This report does not silently relabel that red job as green.

## Premortem and risk register

| # | Failure mode | Consequence | Severity | Prevention/mitigation | State |
|---:|---|---|---|---|---|
| 1 | Backend does not advertise Salesforce | User could click a connector that cannot authorize. | High | Card fails closed as Unavailable and removes OAuth actions. | Implemented and component-tested. |
| 2 | Server advertises an incorrect Salesforce capability | The frontend would trust a false server contract. | High | Backend capability advertisement remains the authority; end-to-end Salesforce work remains explicitly incomplete. | Open backend work; not claimed complete. |
| 3 | Lead outcome is absent | UI might guess from captured details. | High | Unknown stays neutral; field presence is ignored. | Implemented and unit-tested. |
| 4 | Negative text contains a positive substring | A simplistic matcher could misclassify a rejection. | Medium | Explicit negative tokens are enumerated and tested; actionable-summary warning is conservative rather than a positive lead badge. | Implemented; vocabulary should grow with backend enums. |
| 5 | Reward endpoint fails | Dashboard could show zero and hide an outage. | Medium | Ledger unavailable is shown distinctly. | Implemented. |
| 6 | Rewards disabled | UI could imply points are earned. | High | The stat says Off; review form makes no reward promise. | Implemented and existing tests retained. |
| 7 | Campaign write succeeds, policy write fails | Retry could duplicate campaign. | High | Returned campaign ID is retained and reused. | Implemented. |
| 8 | Field registry load fails | Agent could receive a default or overly broad data set. | High | Campaign saving is blocked and Retry is exposed. | Implemented. |
| 9 | Saved campaign policy load fails | Edit could overwrite policy with empty array. | High | Edit save remains disabled while query is loading/error. | Implemented. |
| 10 | DNC contact still looks callable | Operator might call a suppressed person. | High | DNC badge has precedence over lead/status badges. | Implemented. |
| 11 | Contact serializer drifts from API type | Fields could silently disappear. | Medium | Pure serializer is typed as ContactMutation and covered by test/typecheck. | Implemented. |
| 12 | Vercel build differs from local build | Main could deploy a compile failure. | High | Local production build passed; Vercel status completed successfully; GitHub Frontend job passed. | Proven. |
| 13 | GitHub main moved during work | Push could overwrite or omit another change. | High | origin/main was fetched immediately before commit and matched the isolated worktree base. | Proven. |
| 14 | Unrelated backend work enters release | Frontend release could mutate voice behavior. | Critical | Only Talk-Leee and goals.md were staged from the isolated worktree. | Proven by commit file manifest. |
| 15 | Report enters production commit | Large internal report would pollute deploy. | Low | report15.md is created after push in the primary root and remains untracked. | Proven by commit manifest and root status. |
| 16 | CI backend migration round-trip fails | Overall workflow is red and could be mistaken for a frontend regression. | Medium | Failure inspected: empty CI DB lacks baseline schema before Alembic 0001. No backend files changed. | Open CI issue; frontend/Vercel green. |
| 17 | Production alias differs from expected project alias | Deployment could be green but users hit another domain. | High | Configured project is talkleeai; https://talkleeai.vercel.app returned HTTP 200 after deployment. | Proven by direct HEAD request. |
| 18 | Popover overlaps critical controls on narrow screen | Users may be unable to save. | Medium | Viewport cap exists, but the real narrow-screen acceptance item remains open. | Not claimed complete. |
| 19 | Manual contact changes lack durable audit events | Actor/action history may be unavailable. | High | No claim was added; goals item remains open. | Backend/full-stack follow-up required. |
| 20 | Salesforce credentials or records cross tenants | Severe tenant isolation incident. | Critical | No functional Salesforce OAuth/sync was enabled. Future work requires encrypted tenant-scoped credentials and isolation tests. | Prevented today by fail-closed UI; future proof pending. |

### Risk 01 — Backend does not advertise Salesforce

- Failure: Backend does not advertise Salesforce
- User/production consequence: User could click a connector that cannot authorize.
- Severity: High
- Control: Card fails closed as Unavailable and removes OAuth actions.
- Current evidence/state: Implemented and component-tested.

### Risk 02 — Server advertises an incorrect Salesforce capability

- Failure: Server advertises an incorrect Salesforce capability
- User/production consequence: The frontend would trust a false server contract.
- Severity: High
- Control: Backend capability advertisement remains the authority; end-to-end Salesforce work remains explicitly incomplete.
- Current evidence/state: Open backend work; not claimed complete.

### Risk 03 — Lead outcome is absent

- Failure: Lead outcome is absent
- User/production consequence: UI might guess from captured details.
- Severity: High
- Control: Unknown stays neutral; field presence is ignored.
- Current evidence/state: Implemented and unit-tested.

### Risk 04 — Negative text contains a positive substring

- Failure: Negative text contains a positive substring
- User/production consequence: A simplistic matcher could misclassify a rejection.
- Severity: Medium
- Control: Explicit negative tokens are enumerated and tested; actionable-summary warning is conservative rather than a positive lead badge.
- Current evidence/state: Implemented; vocabulary should grow with backend enums.

### Risk 05 — Reward endpoint fails

- Failure: Reward endpoint fails
- User/production consequence: Dashboard could show zero and hide an outage.
- Severity: Medium
- Control: Ledger unavailable is shown distinctly.
- Current evidence/state: Implemented.

### Risk 06 — Rewards disabled

- Failure: Rewards disabled
- User/production consequence: UI could imply points are earned.
- Severity: High
- Control: The stat says Off; review form makes no reward promise.
- Current evidence/state: Implemented and existing tests retained.

### Risk 07 — Campaign write succeeds, policy write fails

- Failure: Campaign write succeeds, policy write fails
- User/production consequence: Retry could duplicate campaign.
- Severity: High
- Control: Returned campaign ID is retained and reused.
- Current evidence/state: Implemented.

### Risk 08 — Field registry load fails

- Failure: Field registry load fails
- User/production consequence: Agent could receive a default or overly broad data set.
- Severity: High
- Control: Campaign saving is blocked and Retry is exposed.
- Current evidence/state: Implemented.

### Risk 09 — Saved campaign policy load fails

- Failure: Saved campaign policy load fails
- User/production consequence: Edit could overwrite policy with empty array.
- Severity: High
- Control: Edit save remains disabled while query is loading/error.
- Current evidence/state: Implemented.

### Risk 10 — DNC contact still looks callable

- Failure: DNC contact still looks callable
- User/production consequence: Operator might call a suppressed person.
- Severity: High
- Control: DNC badge has precedence over lead/status badges.
- Current evidence/state: Implemented.

### Risk 11 — Contact serializer drifts from API type

- Failure: Contact serializer drifts from API type
- User/production consequence: Fields could silently disappear.
- Severity: Medium
- Control: Pure serializer is typed as ContactMutation and covered by test/typecheck.
- Current evidence/state: Implemented.

### Risk 12 — Vercel build differs from local build

- Failure: Vercel build differs from local build
- User/production consequence: Main could deploy a compile failure.
- Severity: High
- Control: Local production build passed; Vercel status completed successfully; GitHub Frontend job passed.
- Current evidence/state: Proven.

### Risk 13 — GitHub main moved during work

- Failure: GitHub main moved during work
- User/production consequence: Push could overwrite or omit another change.
- Severity: High
- Control: origin/main was fetched immediately before commit and matched the isolated worktree base.
- Current evidence/state: Proven.

### Risk 14 — Unrelated backend work enters release

- Failure: Unrelated backend work enters release
- User/production consequence: Frontend release could mutate voice behavior.
- Severity: Critical
- Control: Only Talk-Leee and goals.md were staged from the isolated worktree.
- Current evidence/state: Proven by commit file manifest.

### Risk 15 — Report enters production commit

- Failure: Report enters production commit
- User/production consequence: Large internal report would pollute deploy.
- Severity: Low
- Control: report15.md is created after push in the primary root and remains untracked.
- Current evidence/state: Proven by commit manifest and root status.

### Risk 16 — CI backend migration round-trip fails

- Failure: CI backend migration round-trip fails
- User/production consequence: Overall workflow is red and could be mistaken for a frontend regression.
- Severity: Medium
- Control: Failure inspected: empty CI DB lacks baseline schema before Alembic 0001. No backend files changed.
- Current evidence/state: Open CI issue; frontend/Vercel green.

### Risk 17 — Production alias differs from expected project alias

- Failure: Production alias differs from expected project alias
- User/production consequence: Deployment could be green but users hit another domain.
- Severity: High
- Control: Configured project is talkleeai; https://talkleeai.vercel.app returned HTTP 200 after deployment.
- Current evidence/state: Proven by direct HEAD request.

### Risk 18 — Popover overlaps critical controls on narrow screen

- Failure: Popover overlaps critical controls on narrow screen
- User/production consequence: Users may be unable to save.
- Severity: Medium
- Control: Viewport cap exists, but the real narrow-screen acceptance item remains open.
- Current evidence/state: Not claimed complete.

### Risk 19 — Manual contact changes lack durable audit events

- Failure: Manual contact changes lack durable audit events
- User/production consequence: Actor/action history may be unavailable.
- Severity: High
- Control: No claim was added; goals item remains open.
- Current evidence/state: Backend/full-stack follow-up required.

### Risk 20 — Salesforce credentials or records cross tenants

- Failure: Salesforce credentials or records cross tenants
- User/production consequence: Severe tenant isolation incident.
- Severity: Critical
- Control: No functional Salesforce OAuth/sync was enabled. Future work requires encrypted tenant-scoped credentials and isolation tests.
- Current evidence/state: Prevented today by fail-closed UI; future proof pending.

## Rollback procedure

This release contains frontend and goals-document changes only. It introduces no schema migration and no backend runtime mutation.

### Preferred Vercel rollback

1. Open the Vercel project `talkleeai`.
2. Select the last known-good production deployment for commit `aa0ed789ccf8853536dec7b7338f2b9ad1b3c6d4`.
3. Promote that deployment to production.
4. Verify `/`, `/contacts`, `/campaigns/new`, `/calls/<known-id>`, `/admin/reviews` and `/connectors`.
5. Confirm the production alias returns HTTP 200.

### Preferred Git rollback

1. Create a new revert commit with `git revert 48743d7f8a7e13f9da0592a9b49b9f2b5c03ec56`.
2. Do not reset or force-push main.
3. Push the revert commit to `origin/main`.
4. Wait for Vercel to complete.
5. Verify the production alias and the frontend CI job.

### Data rollback

No database or persistent-data rollback is required. Campaign field-policy writes use existing backend endpoints and only occur when users save through the new UI.

## Remaining work from goals.md

| Workstream | Exact remaining outcome |
|---|---|
| P0 inbound live acceptance | Hear the configured production greeting and prove transfer success plus transfer failure with the runtime gates enabled. |
| P0 200-tenant validation | Execute the existing seeder/isolation/load tooling in the approved environment and preserve the complete evidence pack. |
| P0 controlled release | Complete staged/canary acceptance, rollback rehearsal, monitoring and product-owner real-scenario acceptance. |
| Review trace fidelity | Capture the actual call-time LLM model and call trace with each review. |
| Reward controls | Add suspicious-activity monitoring, tenant/admin configuration and API-level authorization/idempotency tests. |
| Auditability | Persist actor/action/old/new audit events for manual lead corrections and contact import/edit/delete operations. |
| Salesforce MVP | Implement tenant-scoped OAuth, encrypted tokens, refresh, disconnect, mapping, one-way sync, object IDs, retries, DLQ/reconciliation, deduplication, errors and isolation proof. |
| CRM availability | Expose captured interested-lead information through an approved, proven CRM synchronization path. |
| Popover acceptance | Prove on a real narrow viewport that open popovers do not cover save buttons or critical fields. |
| Prompt evidence | Run the frozen 30-call matrix with speakers/accents, scoring targets, DNC proof and traceable artifacts. |
| CI baseline repair | Fix the backend Alembic round-trip setup so the temporary database receives complete_schema before baseline migration checks. |
| Deferred P2 | Automatic fine-tuning, cash rewards, bidirectional Salesforce, opportunity/task/campaign sync, advanced IVR and review-only prompt deployment remain deferred. |

### Remaining 01 — P0 inbound live acceptance

Hear the configured production greeting and prove transfer success plus transfer failure with the runtime gates enabled.

This item is not marked complete by this release. Its acceptance evidence must be recorded before the corresponding goals.md checkbox changes.

### Remaining 02 — P0 200-tenant validation

Execute the existing seeder/isolation/load tooling in the approved environment and preserve the complete evidence pack.

This item is not marked complete by this release. Its acceptance evidence must be recorded before the corresponding goals.md checkbox changes.

### Remaining 03 — P0 controlled release

Complete staged/canary acceptance, rollback rehearsal, monitoring and product-owner real-scenario acceptance.

This item is not marked complete by this release. Its acceptance evidence must be recorded before the corresponding goals.md checkbox changes.

### Remaining 04 — Review trace fidelity

Capture the actual call-time LLM model and call trace with each review.

This item is not marked complete by this release. Its acceptance evidence must be recorded before the corresponding goals.md checkbox changes.

### Remaining 05 — Reward controls

Add suspicious-activity monitoring, tenant/admin configuration and API-level authorization/idempotency tests.

This item is not marked complete by this release. Its acceptance evidence must be recorded before the corresponding goals.md checkbox changes.

### Remaining 06 — Auditability

Persist actor/action/old/new audit events for manual lead corrections and contact import/edit/delete operations.

This item is not marked complete by this release. Its acceptance evidence must be recorded before the corresponding goals.md checkbox changes.

### Remaining 07 — Salesforce MVP

Implement tenant-scoped OAuth, encrypted tokens, refresh, disconnect, mapping, one-way sync, object IDs, retries, DLQ/reconciliation, deduplication, errors and isolation proof.

This item is not marked complete by this release. Its acceptance evidence must be recorded before the corresponding goals.md checkbox changes.

### Remaining 08 — CRM availability

Expose captured interested-lead information through an approved, proven CRM synchronization path.

This item is not marked complete by this release. Its acceptance evidence must be recorded before the corresponding goals.md checkbox changes.

### Remaining 09 — Popover acceptance

Prove on a real narrow viewport that open popovers do not cover save buttons or critical fields.

This item is not marked complete by this release. Its acceptance evidence must be recorded before the corresponding goals.md checkbox changes.

### Remaining 10 — Prompt evidence

Run the frozen 30-call matrix with speakers/accents, scoring targets, DNC proof and traceable artifacts.

This item is not marked complete by this release. Its acceptance evidence must be recorded before the corresponding goals.md checkbox changes.

### Remaining 11 — CI baseline repair

Fix the backend Alembic round-trip setup so the temporary database receives complete_schema before baseline migration checks.

This item is not marked complete by this release. Its acceptance evidence must be recorded before the corresponding goals.md checkbox changes.

### Remaining 12 — Deferred P2

Automatic fine-tuning, cash rewards, bidirectional Salesforce, opportunity/task/campaign sync, advanced IVR and review-only prompt deployment remain deferred.

This item is not marked complete by this release. Its acceptance evidence must be recorded before the corresponding goals.md checkbox changes.

## Appendix A — Commit metadata and statistics

````text
commit 48743d7f8a7e13f9da0592a9b49b9f2b5c03ec56
Author:     tooti12 <umar.jwork@gmail.com>
AuthorDate: Tue Sep 1 12:43:49 2026 +0500
Commit:     tooti12 <umar.jwork@gmail.com>
CommitDate: Tue Sep 1 12:43:49 2026 +0500

    feat(frontend): complete goals-driven dashboard updates
    Add lead-field controls, honest statuses, rewards, and expanded contacts.

 Talk-Leee/src/app/admin/reviews/page.tsx           |  22 +-
 Talk-Leee/src/app/api/v1/[...path]/route.ts        |   1 +
 Talk-Leee/src/app/calls/[id]/page.tsx              |   1 +
 Talk-Leee/src/app/campaigns/[id]/edit/page.tsx     |  34 +--
 Talk-Leee/src/app/connectors/page.tsx              |  20 +-
 Talk-Leee/src/app/contacts/page.tsx                | 146 +++++++------
 .../src/components/calls/CallSummaryCard.test.tsx  |  43 ++++
 Talk-Leee/src/components/calls/CallSummaryCard.tsx |  45 +++-
 .../src/components/calls/lead-details-panel.tsx    |  10 +-
 .../src/components/campaigns/campaign-form.tsx     |  45 +++-
 .../campaigns/campaign-lead-fields.test.ts         |  67 ++++++
 .../components/campaigns/campaign-lead-fields.tsx  | 234 +++++++++++++++++++++
 .../src/components/campaigns/campaign-wizard.tsx   |  75 +++++--
 .../components/connectors/connector-card.test.ts   |  19 ++
 .../src/components/connectors/connector-card.tsx   |  23 +-
 Talk-Leee/src/lib/connectors-utils.ts              |   2 +-
 Talk-Leee/src/lib/contact-form.test.ts             |  27 +++
 Talk-Leee/src/lib/contact-form.ts                  |  55 +++++
 Talk-Leee/src/lib/extended-api.rewards.test.ts     |  28 +++
 Talk-Leee/src/lib/extended-api.ts                  |  13 ++
 Talk-Leee/src/lib/lead-details-api.test.ts         |  38 ++++
 Talk-Leee/src/lib/lead-details-api.ts              |  29 +++
 Talk-Leee/src/lib/lead-outcome.test.ts             |  26 +++
 Talk-Leee/src/lib/lead-outcome.ts                  |  45 ++++
 goals.md                                           |  30 ++-
 25 files changed, 953 insertions(+), 125 deletions(-)
 create mode 100644 Talk-Leee/src/components/calls/CallSummaryCard.test.tsx
 create mode 100644 Talk-Leee/src/components/campaigns/campaign-lead-fields.test.ts
 create mode 100644 Talk-Leee/src/components/campaigns/campaign-lead-fields.tsx
 create mode 100644 Talk-Leee/src/lib/contact-form.test.ts
 create mode 100644 Talk-Leee/src/lib/contact-form.ts
 create mode 100644 Talk-Leee/src/lib/extended-api.rewards.test.ts
 create mode 100644 Talk-Leee/src/lib/lead-details-api.test.ts
 create mode 100644 Talk-Leee/src/lib/lead-outcome.test.ts
 create mode 100644 Talk-Leee/src/lib/lead-outcome.ts
````

## Appendix B — Changed-path and Git blob manifest

````text
M	Talk-Leee/src/app/admin/reviews/page.tsx
M	Talk-Leee/src/app/api/v1/[...path]/route.ts
M	Talk-Leee/src/app/calls/[id]/page.tsx
M	Talk-Leee/src/app/campaigns/[id]/edit/page.tsx
M	Talk-Leee/src/app/connectors/page.tsx
M	Talk-Leee/src/app/contacts/page.tsx
A	Talk-Leee/src/components/calls/CallSummaryCard.test.tsx
M	Talk-Leee/src/components/calls/CallSummaryCard.tsx
M	Talk-Leee/src/components/calls/lead-details-panel.tsx
M	Talk-Leee/src/components/campaigns/campaign-form.tsx
A	Talk-Leee/src/components/campaigns/campaign-lead-fields.test.ts
A	Talk-Leee/src/components/campaigns/campaign-lead-fields.tsx
M	Talk-Leee/src/components/campaigns/campaign-wizard.tsx
M	Talk-Leee/src/components/connectors/connector-card.test.ts
M	Talk-Leee/src/components/connectors/connector-card.tsx
M	Talk-Leee/src/lib/connectors-utils.ts
A	Talk-Leee/src/lib/contact-form.test.ts
A	Talk-Leee/src/lib/contact-form.ts
A	Talk-Leee/src/lib/extended-api.rewards.test.ts
M	Talk-Leee/src/lib/extended-api.ts
A	Talk-Leee/src/lib/lead-details-api.test.ts
M	Talk-Leee/src/lib/lead-details-api.ts
A	Talk-Leee/src/lib/lead-outcome.test.ts
A	Talk-Leee/src/lib/lead-outcome.ts
M	goals.md
---BLOBS---
100644 blob f771375050ee1d35d288cfb98c35ae2ba5694ddf	Talk-Leee/.github/workflows/ci.yml
100644 blob 840bf5bd9db8ccae968d64ef82e18da9028b95fc	Talk-Leee/.gitignore
100644 blob fc1ede339cc092b6807c85e1571984b987a22e0c	Talk-Leee/.storybook/main.ts
100644 blob 3d8c20f0e95093ba865f517d577554864a23189d	Talk-Leee/.storybook/mocks/next-image.tsx
100644 blob ed1b3946c9e9899e97fa4ba82025b7ce717b45fb	Talk-Leee/.storybook/mocks/next-link.tsx
100644 blob 5083415226b9e98c3ec799f1b7f40c4190b3876c	Talk-Leee/.storybook/mocks/next-navigation.ts
100644 blob ec621f37643027ea3a809b8a00f00ba0aa20b10a	Talk-Leee/.storybook/preview.ts
100644 blob dd174dc4f022ef49a545bfc2a7be12781b2ecaea	Talk-Leee/.trae_commit_msg.txt
100644 blob f40dc50f70741056958a5f26e9dbcf5a6f1f6929	Talk-Leee/.trae_local_git_files.txt
100644 blob f40dc50f70741056958a5f26e9dbcf5a6f1f6929	Talk-Leee/.trae_remote_git_files.txt
100644 blob a5616d9397235332628b61ed09744b7a644a1933	Talk-Leee/.vscode/settings.json
100644 blob 643577dfaef36ee8032915ef80a0f0638f7beda0	Talk-Leee/AGENTS.md
100644 blob b6f422a09d45e8b8f0d84099dd389eb7d8eed0fc	Talk-Leee/API Mapping.md
100644 blob c674c181da55b98fbc663e58bbc4c3628dc07ebc	Talk-Leee/Billing layer.md
100644 blob 213b16ce7341db48749295aea30908c73cb7e6c9	Talk-Leee/Billing plan 01.md
100644 blob 43c994c2d3617f947bcb5adf1933e21dabe46bb5	Talk-Leee/CLAUDE.md
100644 blob c293b963e2811f8cfa2d8b5d899ab3e563f68fb8	Talk-Leee/Dashboard plan.md
100644 blob de31d5067e28213b59945854ccaa20274a05d50c	Talk-Leee/Dummy data.txt
100644 blob 297654825c603164aa681c288c70b24000612d83	Talk-Leee/Frontend Checklist.md
100644 blob 36e2c453694b75c07b7fc67a6bb872c4a7635026	Talk-Leee/Performance.md
100644 blob 23b9f62ca47cb6d91d48ed2a9cd69ce7cda60519	Talk-Leee/Plan.txt
100644 blob bdfcaeb6eceb9ad95910006214530c3e3190b6ca	Talk-Leee/README.md
100644 blob edcaef267e34d591c9ae0f0b4cd14a146e6c012f	Talk-Leee/components.json
100644 blob 152c1375ff35cb66efb22e45e91df12608b9c994	Talk-Leee/eslint.config.mjs
100644 blob 24bdb32f8f4a3c801dd5a8ef8c5ed95214968f71	Talk-Leee/frontend-roadmap.md
100644 blob ec62c1627bd5fd771c47d9e0a024606c9e46065b	Talk-Leee/next.config.ts
100644 blob 41e31ae8e0958b615c61bbb231d8591989c5890a	Talk-Leee/package-lock.json
100644 blob dac164b6ff4d9a1b16384ac29b0daa918538acf3	Talk-Leee/package.json
100644 blob 11cc818726d7ea299900f15f474b78c416f4b676	Talk-Leee/playwright.config.ts
100644 blob 61e36849cf7cfa9f1f71b4a3964a4953e3e243d3	Talk-Leee/postcss.config.mjs
100644 blob 61db73a5dcafc298a64744bfea884b607709fbb1	Talk-Leee/public/favicon.svg
100644 blob 9db5a15c7076be92c066c504b9c4621ebd1f55fc	Talk-Leee/public/images/ai-voice-agent/see-it-in-action.png
100644 blob d283b4d7da01859a3abf9ec6da1ed947d9529bc8	Talk-Leee/public/images/ai-voice-section..jpg
100644 blob afa04b913bca916c3642a79bd4d828bf7d609c51	Talk-Leee/public/images/ai-voice-section..mp4
100644 blob d72bb53785832ea75aa26ea47cb761d6fc765662	Talk-Leee/public/images/hero-navbar-video.mp4
100644 blob 8058f76df53eb2306e9b3f13ff81548797401199	Talk-Leee/public/images/industries/financial-services/how-it-works.jpg
100644 blob d13a269d54be583756289e38f7d10c3f67661239	Talk-Leee/public/images/industries/healthcare/ai-voice-agents.jpg
100644 blob 195bc3bd76ff6bbf46e60f1a98bbee163889a621	Talk-Leee/public/images/industries/healthcare/live-call-preview.png
100644 blob 5c80989db30920dbbea83faae81281036e389dd3	Talk-Leee/public/images/industries/healthcare/request-a-demo.png
100644 blob 3f02b7a4de0767fae89c75a95fb5668b64d0b2d9	Talk-Leee/public/images/industries/healthcare/smarter-conversations-better-patient-care.png
100644 blob c6299c60d27b6c1807c625ee3f13ca6a885fd624	Talk-Leee/public/images/industries/marketing-automation.jpg
100644 blob 56c42fe4708e7985f3b436da1d99b159560fe1ea	Talk-Leee/public/images/industries/marketing-automation.png
100644 blob 470a2dd25d9dc36e21ef5f8e1d13d4ce56d4b70b	Talk-Leee/public/images/industries/professional-services/10.jpg
100644 blob 0830f86f8013db04d049f54f3577cac1d5d4792d	Talk-Leee/public/images/industries/professional-services/11.jpg
100644 blob 7d909ef7efc7da7088811e62560c16c15cd0a99f	Talk-Leee/public/images/industries/real-estate/real-estate-7.jpg
100644 blob 876518b9bf4eeb85cf30d10cdc1b1991b2da273c	Talk-Leee/public/images/industries/retail-ecommerce/features.png
100644 blob 56c42fe4708e7985f3b436da1d99b159560fe1ea	Talk-Leee/public/images/industries/software-tech-support.png
100644 blob b427e13236cb3857c4ccc1f654569b26e4def989	Talk-Leee/public/images/industries/software-tech-support/12.jpg
100644 blob 0bef4f9b5a1c018c416d7109fc7fdef396fe6565	Talk-Leee/public/images/industries/travel-industry/hero.png
100644 blob 847128434bb9763de2f6c185651d53fa99e34034	Talk-Leee/public/images/use-cases/customer-services-support/1.png
100644 blob c58b39d026ee77cba46aa8c44af0a19061c8b11b	Talk-Leee/public/openapi.json
100644 blob 2aa53e2cbf912d5f1e8baf2ec9318b6a0a948c05	Talk-Leee/public/site.webmanifest
100644 blob 98048288dc550fe37bc8d334a9a5592608dc9dfa	Talk-Leee/public/white-label/acme/favicon.svg
100644 blob 3fa4d62b5de061619b107bbee8c78d250fbc7e89	Talk-Leee/public/white-label/acme/logo.svg
100644 blob 1a2da025a4e8e0a15849fd967772413615993ee5	Talk-Leee/public/white-label/zen/favicon.svg
100644 blob 307e6984449778706047dc9dfbb4816947ce4e99	Talk-Leee/public/white-label/zen/logo.svg
100644 blob dc08eefa494a8ce39dc179fb269dcb037068e814	Talk-Leee/public/worklets/pcm16-capture-processor.js
100644 blob f506db358bd8eec16a2b4b29cda925fc3b5add82	Talk-Leee/push-safe.bat
100644 blob 654efb17f6210d1ab61c3dbd81233b00752fdaee	Talk-Leee/scripts/branding-terms.js
100644 blob 9e8d89ee0e7c21db4ada2e489248770e9a770e5e	Talk-Leee/scripts/check-branding.js
100644 blob 3dacd166d3f975937429e315156a0ee03dfa6ea5	Talk-Leee/scripts/fix-branding.js
100644 blob 847405166a31803c47ec010203b87c804917af0b	Talk-Leee/scripts/generate-openapi.ts
100644 blob bbf4c0904356f7ed96d69f9821a3068eaa1463f3	Talk-Leee/src/Industries/industries.ts
100644 blob 56d828843dfa9c53fed45fbf02320cbb4a934697	Talk-Leee/src/app/403/page.tsx
100644 blob 0534cd19247b9d783eb186764f85794c9bed80f0	Talk-Leee/src/app/admin/abuse-detection/page.tsx
100644 blob 5de2cb2179fc9b54ccbb2e7b10f21a35dd372f37	Talk-Leee/src/app/admin/api-keys/page.tsx
100644 blob c76af761d7c6945210ed948470ddd7fefd4f4400	Talk-Leee/src/app/admin/audit-logs/page.tsx
100644 blob 2ad113f6bc7e5fc109b872daf8fb32fed46c9b21	Talk-Leee/src/app/admin/billing/page.tsx
100644 blob b9277a23cf8ba6d199fc82d29e901586b081b4d8	Talk-Leee/src/app/admin/billing/tenants/page.tsx
100644 blob 1555e34f058740fd885f78c108c3dd075de83e4d	Talk-Leee/src/app/admin/page.tsx
100644 blob 3f8645093c4d2373154f8c2f5d0ec11a43bb5d7f	Talk-Leee/src/app/admin/rate-limiting/page.tsx
100644 blob 80d5ec7dd523950d21dfa0458ac8789d938ef85e	Talk-Leee/src/app/admin/reviews/page.tsx
100644 blob 50cb348f6892c47eeee26a2360c873817d4bf99f	Talk-Leee/src/app/admin/secrets/page.tsx
100644 blob 21752a8c860bce676abadef16708cbc6ee80b13a	Talk-Leee/src/app/admin/voice-security/page.tsx
100644 blob 4028c4a38bdb0380ee14f6c828afc02ccff47d3d	Talk-Leee/src/app/admin/webhooks/page.tsx
100644 blob 22f5ade82f81dd560fb0c3c733458fb9e0dddde6	Talk-Leee/src/app/ai-assist/page.tsx
100644 blob 19c80da2a65d759947a32bcc7fb68a65b54ca124	Talk-Leee/src/app/ai-options/page.tsx
100644 blob 114ad1ad9644830b4491d4fd00e3afc30b9a41cc	Talk-Leee/src/app/ai-voice-agent/page.tsx
100644 blob 6229bc1d3e9b5e0f9a8e8d344d2426b4eb0a170b	Talk-Leee/src/app/ai-voice-dialer/page.tsx
100644 blob 5824f17cff8921b949a4704622ed8b8e21192418	Talk-Leee/src/app/ai-voices/page.test.tsx
100644 blob a27e70b547a52588afca9093978682976bee51fc	Talk-Leee/src/app/ai-voices/page.tsx
100644 blob a6a8bbd32e7d9f163f3df92480dc1ea36fc37de1	Talk-Leee/src/app/analytics/page.tsx
100644 blob 0885d8ff6718ddc88ee823cce988c524c5689c76	Talk-Leee/src/app/api/v1/[...path]/route.ts
100644 blob 87da41b59839c2bc452278488dc9e5bee57d6441	Talk-Leee/src/app/api/voices/route.ts
100644 blob a229100037c299a8437a26dcff04ce91c22add88	Talk-Leee/src/app/api/white-label/branding/[partner]/route.ts
100644 blob 47c2ca1f5483fcfd93b27fc5b1b6bb7d105a745b	Talk-Leee/src/app/assistant/actions/error.tsx
100644 blob cc3b74561aa81ac7bf38b76bfb1866207b606cf5	Talk-Leee/src/app/assistant/actions/loading.tsx
100644 blob c109a119804e121abf842546a7139debf1731afd	Talk-Leee/src/app/assistant/actions/page.tsx
100644 blob 47c2ca1f5483fcfd93b27fc5b1b6bb7d105a745b	Talk-Leee/src/app/assistant/error.tsx
100644 blob b70bbbf0314f59b44998f3ca0271b6d6f6681318	Talk-Leee/src/app/assistant/loading.tsx
100644 blob 83c2edb8084e8fdd37390ad543ce5ab42efe4f53	Talk-Leee/src/app/assistant/meetings/page.tsx
100644 blob 63e2e62b9f575ac7b99b2d682cb2a0e36ce36cdd	Talk-Leee/src/app/assistant/page.tsx
100644 blob 70ef2d12e4b565f2e8bc96613a0cb6b6832e926d	Talk-Leee/src/app/assistant/reminders/page.tsx
100644 blob e2eb8460c522d5f998f429059889dd01222619f4	Talk-Leee/src/app/auth/callback/page.tsx
100644 blob f613366cda927594f307a6a2b8bd20a524af37b0	Talk-Leee/src/app/auth/forgot-password/page.tsx
100644 blob f3a650c92043b7a13f441a6cfa5f51bfa20eae87	Talk-Leee/src/app/auth/login/login-client.tsx
100644 blob 761030b343696f03082b55181650ad49e7994d84	Talk-Leee/src/app/auth/login/page.tsx
100644 blob a915e002be9440daed96b97d1a51d2c9049dad93	Talk-Leee/src/app/auth/register/page.tsx
100644 blob e1f1b685be7374ea8d9b8b90782cf169148ce2fe	Talk-Leee/src/app/auth/register/register-client.tsx
100644 blob cb9dc5764225d728096a448767901af33100ba14	Talk-Leee/src/app/billing/invoices/[id]/page.tsx
100644 blob 78b5eaf7a4d8ad24c7ae21ff82f7aabbc5a4988d	Talk-Leee/src/app/billing/invoices/page.tsx
100644 blob af7fe8f8ee0d0f7acd6ae753e298cb6a719e419a	Talk-Leee/src/app/billing/page.tsx
100644 blob 0b00730406e6e4c9b8742206139a1c8907c60403	Talk-Leee/src/app/billing/plans/page.tsx
100644 blob 144afbdecccf8a55d68da169b1f63c78827be34b	Talk-Leee/src/app/calls/[id]/page.tsx
100644 blob 7b95dbb1c4be9c1f9aa240138575bb764f2e89c8	Talk-Leee/src/app/calls/page.tsx
100644 blob c2cd672faa877b8fb812eccb01c85ca3b4d2a9b3	Talk-Leee/src/app/campaigns/[id]/edit/page.tsx
100644 blob dedbc81a517b71d3582ce65f6a02e97c35ad642d	Talk-Leee/src/app/campaigns/[id]/page.tsx
100644 blob 11c4c60529a00bc131dfcb0089c23771d0d8383c	Talk-Leee/src/app/campaigns/new/page.tsx
100644 blob 9ded23f9e01be68775f4695622f0ae1af64cc5a3	Talk-Leee/src/app/campaigns/page.tsx
100644 blob 8cc5ce74517c66e28d7b8c69415571a63c3fb258	Talk-Leee/src/app/connectors/[type]/callback/page.tsx
100644 blob 18d21312bca086d774fb8bd297d75f2222e8df09	Talk-Leee/src/app/connectors/callback/page.tsx
100644 blob 47c2ca1f5483fcfd93b27fc5b1b6bb7d105a745b	Talk-Leee/src/app/connectors/error.tsx
100644 blob 5a1b42094a03d7658ba56f900eee501d5909f8f6	Talk-Leee/src/app/connectors/loading.tsx
100644 blob d2ab6fd02ba7aea507ad790d29149bf31f4effaf	Talk-Leee/src/app/connectors/page.tsx
100644 blob adc1c50de8b783f85f8fd28485921f484a38f24c	Talk-Leee/src/app/contact/page.tsx
100644 blob d215bffbfac4a6eebc916ef76b04e6ddadaa866f	Talk-Leee/src/app/contacts/page.tsx
100644 blob 34dfa2e0f3437b7c73d897ac393be2c307890e02	Talk-Leee/src/app/dashboard/layout.tsx
100644 blob 9cd5f7de33274169e9e58ffce2a87c2e2232b82c	Talk-Leee/src/app/dashboard/page.tsx
100644 blob 92d150738d6c1102cc21d65c58675f15a6174ded	Talk-Leee/src/app/email/page.tsx
100644 blob 0313b3e06b870195377cf1db16dfc788cc335d4d	Talk-Leee/src/app/favicon-192.png/route.ts
100644 blob 0313b3e06b870195377cf1db16dfc788cc335d4d	Talk-Leee/src/app/favicon-512.png/route.ts
100644 blob 0313b3e06b870195377cf1db16dfc788cc335d4d	Talk-Leee/src/app/favicon.ico/route.ts
100644 blob 0313b3e06b870195377cf1db16dfc788cc335d4d	Talk-Leee/src/app/favicon.png/route.ts
100644 blob d118d519335d8fe8b1fe1b1e4f35fa4699d4e5df	Talk-Leee/src/app/global-error.tsx
100644 blob 934417d7108e79a6b47f15d207c58a32236a625d	Talk-Leee/src/app/globals.css
100644 blob bf59f6b98952688305affdb58c5c8b57610376f5	Talk-Leee/src/app/head.tsx
100644 blob bf0aa7b79ec85d22dafbd2e0a326cdf41e605f43	Talk-Leee/src/app/inbound-campaigns/[id]/edit/page.tsx
100644 blob 155d2021cccf884c18ee02303e791d97378ba6fc	Talk-Leee/src/app/inbound-campaigns/[id]/page.tsx
100644 blob fc7328dab9d6876a723cc07e3d009f0b0a521861	Talk-Leee/src/app/inbound-campaigns/new/page.tsx
100644 blob e60569a2c25e3b253e27f212a990bc6ab5208936	Talk-Leee/src/app/inbound-campaigns/page.tsx
100644 blob db8d09c2f15702796ce685fc9560cf260138bd91	Talk-Leee/src/app/industries/education/page.tsx
100644 blob c853c3a582a033b5d47c7b954fa57061b5e9da93	Talk-Leee/src/app/industries/financial-services/page.tsx
100644 blob e6b2ee6498e663dc590cdeba364ab9f204a0185d	Talk-Leee/src/app/industries/healthcare/page.tsx
100644 blob 42d96478aa77d020c8406e4dff6ec8eee955ba0c	Talk-Leee/src/app/industries/marketing-automation/page.tsx
100644 blob 79aa3c6a1ccd6755f94ebd83330cc24c18de78b0	Talk-Leee/src/app/industries/professional-services/page.tsx
100644 blob b91a6237f727698348ad4326bc631842fbb8aae5	Talk-Leee/src/app/industries/real-estate/page.tsx
100644 blob 3459f975c7b8d6aa5b889099b6861af0be0fe589	Talk-Leee/src/app/industries/recruitment/page.tsx
100644 blob 7f241dbde4dda1ebb96fbd88bafe728b564d9162	Talk-Leee/src/app/industries/retail-ecommerce/page.tsx
100644 blob c62f9596a71c8691d30263e5de9f1834aea8590c	Talk-Leee/src/app/industries/software-tech-support/page.tsx
100644 blob d704c73afd6c83aead18fe1414437f3bab308332	Talk-Leee/src/app/industries/travel-industry/page.tsx
100644 blob 4449931d762130e5d99bb744b4077eafc28f45de	Talk-Leee/src/app/layout.tsx
100644 blob 47c2ca1f5483fcfd93b27fc5b1b6bb7d105a745b	Talk-Leee/src/app/meetings/error.tsx
100644 blob 1258aae763be632c387f1430f57bc1fb4883cd98	Talk-Leee/src/app/meetings/loading.tsx
100644 blob 70b655f9bbf0e92f97044ba103b6c3b393e29f4e	Talk-Leee/src/app/meetings/meeting-row.test.tsx
100644 blob e5ebb80f88a4564b5213fec82ea2e790b5142a1b	Talk-Leee/src/app/meetings/page.tsx
100644 blob db3c8b311d2de9f60c3313dd875734e4dd4d7f0c	Talk-Leee/src/app/not-found.tsx
100644 blob 6337d4591b9bda65d26bae341a1e0ae6bfda3040	Talk-Leee/src/app/notifications/page.tsx
100644 blob f4dfeea69602ac97650f651b32b0b3cd99c7fbeb	Talk-Leee/src/app/page.tsx
100644 blob 608823df6311dce596fd5ff59dd3bec0476d9809	Talk-Leee/src/app/privacy/page.tsx
100644 blob c5b50f939f0d5d0bdc0009b46f15b28e9c1bbf29	Talk-Leee/src/app/privacy/privacy-content.ts
100644 blob b3d5322427b46146ab6aae3f6c2638293370495b	Talk-Leee/src/app/recordings/page.tsx
100644 blob 47c2ca1f5483fcfd93b27fc5b1b6bb7d105a745b	Talk-Leee/src/app/reminders/error.tsx
100644 blob 96af37dba4b7b63eb7552454acd31e3aae40ccc1	Talk-Leee/src/app/reminders/loading.tsx
100644 blob 1cc480673a1bda0d81ab12a5b1e5469171c89572	Talk-Leee/src/app/reminders/page.tsx
100644 blob 9b7e927d7ec38db3d324967c0d736517fe19474d	Talk-Leee/src/app/reviews/page.tsx
100644 blob 931a2e54a90e991ac69445a41b8027f20c9fb491	Talk-Leee/src/app/security/page.tsx
100644 blob 2b41ade258fd7b89c209d8d2bfcab0d838d2a35a	Talk-Leee/src/app/security/session-revoke-copy.test.ts
100644 blob 1ad287933c63e1ec7f1035af6c102dd42767ea95	Talk-Leee/src/app/settings/page.tsx
100644 blob a87d2fb93fefc9cd83e34164ec0883d1710b3680	Talk-Leee/src/app/terms/page.tsx
100644 blob 6f6c08de1f3b06db8598102b348b0b4ec659b07b	Talk-Leee/src/app/terms/terms-content.ts
100644 blob c1f5b26d88abcc4ef490730985accd8cbdfc9385	Talk-Leee/src/app/use-cases/automated-lead-qualification/page.tsx
100644 blob 2ccd3047ec50eafc1920ec6789e66e6823ca1ac9	Talk-Leee/src/app/use-cases/customer-services-support/page.tsx
100644 blob 223bef0d06afc4929da39ca7238251822a8e40a5	Talk-Leee/src/app/white-label/[partner]/analytics/page.tsx
100644 blob 716ca751f54807c388263246ca952dafcfd55ec0	Talk-Leee/src/app/white-label/[partner]/analytics/partner-analytics-client.tsx
100644 blob 06e9b6c9003f150b40e748d4005c52c0743183d6	Talk-Leee/src/app/white-label/[partner]/billing/page.tsx
100644 blob 3341f7bfb16a2b329aeac0b8318d2d16fbe8907c	Talk-Leee/src/app/white-label/[partner]/dashboard/page.tsx
100644 blob 0f219dbdb7a1c86dc4271d152a46cb358e35aabf	Talk-Leee/src/app/white-label/[partner]/layout.tsx
100644 blob 626a39f54e26006f6825c38e50cc3c7981f95bcb	Talk-Leee/src/app/white-label/[partner]/preview/page.tsx
100644 blob 45ce2de03dee346c3137eab88b22e60fd950a96f	Talk-Leee/src/app/white-label/[partner]/tenants/[tenant]/agent-settings/page.tsx
100644 blob d54526c0b47a05508f6fed7ce1a1a4073940062a	Talk-Leee/src/app/white-label/[partner]/tenants/page.tsx
100644 blob 76e3d980ac58a772f8427684a0d57ea4a18eeb07	Talk-Leee/src/app/white-label/[partner]/tenants/tenants-client.tsx
100644 blob f1f15654b4744c478b3b73af7b22edfe8c1bcf12	Talk-Leee/src/app/white-label/dashboard/layout.tsx
100644 blob e0e1f62ce18fedb690494b6f750d6b5075a59506	Talk-Leee/src/app/white-label/dashboard/page.tsx
100644 blob ed2f5b5830fb158af5e7dad6326b4f9c9ed684ce	Talk-Leee/src/app/white-label/layout.tsx
100644 blob 0adb7b72765cbf1e143efc938e960dc58bbd3981	Talk-Leee/src/components/admin/admin-operations-console.tsx
100644 blob d27e750d834a5bff55e037cb0e513f89c802cc60	Talk-Leee/src/components/admin/feature-unavailable.tsx
100644 blob 361eaaacae15b27e31d7b91720a66cd577f21c68	Talk-Leee/src/components/admin/suspension-state-provider.tsx
100644 blob ec2b933915aba07864568f85b9ae1286c06043dd	Talk-Leee/src/components/ai-options/controls.test.tsx
100644 blob dab0647641470a0a47c55fc395d06aa83094288b	Talk-Leee/src/components/ai-options/controls.tsx
100644 blob 4f4fbf84ff53d06094c8165f339e7c40f2e8974f	Talk-Leee/src/components/ai-options/voice-clone-modal.tsx
100644 blob 60c6d4aff92e11ed366a6d0f8ae14defd7881bb4	Talk-Leee/src/components/assistant/assistant-model-picker.tsx
100644 blob 15a14398a5d8517b79b06015692279b913cf4284	Talk-Leee/src/components/assistant/conversation-history.tsx
100644 blob 5fba227627941081b6ed6942c3aa52c73c92a859	Talk-Leee/src/components/assistant/diff-view.tsx
100644 blob c3cf618c5a5e836866060da87fb12138a105e2a4	Talk-Leee/src/components/assistant/edit-proposal-card.tsx
100644 blob ca6d8e43531df1b882b28e8c0a05815bf53b4116	Talk-Leee/src/components/assistant/floating-assistant.tsx
100644 blob 1916a311fbd3e226fbfff4cafe0f6902c87b17b2	Talk-Leee/src/components/assistant/markdown-message.tsx
100644 blob 5a99ab646d9056ab056588c7e13fe2f08dfbf833	Talk-Leee/src/components/assistant/voice-mode.tsx
100644 blob 5144457a986a57243593bda68dfd219cbd305dfb	Talk-Leee/src/components/auth/device-list.tsx
100644 blob 754e3e46a3fdbaf41c6660fc6a4e6cafeaec05d4	Talk-Leee/src/components/auth/logout-button.tsx
100644 blob cf8a17d681feba38e0f82d506e91d3f8fcb89041	Talk-Leee/src/components/auth/mfa-setup.tsx
100644 blob ab7e7690396ca52e75a55be5b83d5228c3c4cade	Talk-Leee/src/components/auth/mfa-verification.tsx
100644 blob 1ffc8880186bebbd24a456097ef6b4408a94a490	Talk-Leee/src/components/auth/passkey-list.tsx
100644 blob a4a42cfe63890b812b100245daa84386beac9ab3	Talk-Leee/src/components/auth/passkey-login.tsx
100644 blob dd24892e20980f008d65c168eca350d03524e84e	Talk-Leee/src/components/auth/passkey-registration.tsx
100644 blob 905d469280b0c0f7fb986562616ff34f198ecf0f	Talk-Leee/src/components/auth/role-based-render.tsx
100644 blob b059797107662f2309d1a13f5f8aaea32a26628d	Talk-Leee/src/components/billing/billing-overview.test.tsx
100644 blob 80d67f79e97f375c1921f5d8064b5493fa415f08	Talk-Leee/src/components/billing/billing-overview.tsx
100644 blob 6ccd06bb622195eb491878bb7316308551b3c0a7	Talk-Leee/src/components/billing/topup-card.tsx
100644 blob 2b7c1619764fbf8bcdefcfe908a177c657b9479a	Talk-Leee/src/components/calls/CallSummaryCard.test.tsx
100644 blob 86a58bb76437b934fca87ad48b446dfaf62703eb	Talk-Leee/src/components/calls/CallSummaryCard.tsx
100644 blob 337e607f02d9b0a8fc618de8913cfa86ab3803a5	Talk-Leee/src/components/calls/call-issues-banner.tsx
100644 blob bd29265d51a890b99e42947f82dfe751b83cd044	Talk-Leee/src/components/calls/conversation-review-panel.test.tsx
100644 blob 8c3d2ed9b3b4857fb6d29b79d8c97176f23994e3	Talk-Leee/src/components/calls/conversation-review-panel.tsx
100644 blob ed84c5c45d292777c1cc4e19f24e47f2cb30b6a0	Talk-Leee/src/components/calls/lead-details-panel.tsx
100644 blob 60d5ebbd6c1c54bc39a885e222cfdfc58cfcfac7	Talk-Leee/src/components/calls/quick-review-buttons.tsx
100644 blob 7b689ceeda05a5a4b06049683ee4a7c0e3bd8d16	Talk-Leee/src/components/calls/recording-feedback-bar.tsx
100644 blob da642e0363bb684de91e3ba396f980d44fefd1d6	Talk-Leee/src/components/calls/use-voice-recorder.ts
100644 blob 865ec7f5839d5d67cf524a3d09b0b96c81e47c08	Talk-Leee/src/components/calls/voice-feedback-recorder.tsx
100644 blob 110431cb3834b8788dd93f93048d628d0def3777	Talk-Leee/src/components/campaigns/agent-name-gender.tsx
100644 blob e4521e6c5d824678200c93aabd07a1bb71682164	Talk-Leee/src/components/campaigns/alert-timeline.stories.tsx
100644 blob 297d80fb9ecd4c1e3b002e6b7d071c956552519d	Talk-Leee/src/components/campaigns/alert-timeline.tsx
100644 blob 1eb182078c8ec4a07d5d9cfc09c15ff89512ed8c	Talk-Leee/src/components/campaigns/apply-to-campaigns-modal.tsx
100644 blob c0660c8a73b00259048cab130f15cd9b5e56b453	Talk-Leee/src/components/campaigns/call-issues-panel.tsx
100644 blob f32e2710b330c015e1141c1f7f0ed6c11f75c861	Talk-Leee/src/components/campaigns/calling-schedule-editor.tsx
100644 blob b74092d18ca8ac42412131e509407e25cb9616f7	Talk-Leee/src/components/campaigns/campaign-basics-editor.tsx
100644 blob 0788ea63165b942a1bfc2afa5d890e08bf1df479	Talk-Leee/src/components/campaigns/campaign-form.tsx
100644 blob 63864c9dd33c680fc4d52d452975c62f81abef7e	Talk-Leee/src/components/campaigns/campaign-lead-fields.test.ts
100644 blob c5f6858ba708591a1faa8a0c465ca36bdfe95fb2	Talk-Leee/src/components/campaigns/campaign-lead-fields.tsx
100644 blob 03f0c14998686c9238b0b7c35c20b84817d7d211	Talk-Leee/src/components/campaigns/campaign-performance-table.stories.tsx
100644 blob 4801ca67bde2e65ab28720d0cf6547d8a1d53739	Talk-Leee/src/components/campaigns/campaign-performance-table.tsx
100644 blob 7f0c86b0f65c89ad224802a60aed641b449ea406	Talk-Leee/src/components/campaigns/campaign-wizard.tsx
100644 blob 7acabe4c6ac078d21d358f2262c0aa5dbc23b3fc	Talk-Leee/src/components/campaigns/command-bar.stories.tsx
100644 blob 3ce23c08d5f1256dbed16371e765b4f9e1292b74	Talk-Leee/src/components/campaigns/command-bar.tsx
100644 blob 3f9e03c1ba8310178345000ca57d05700817f177	Talk-Leee/src/components/campaigns/contact-lists.tsx
100644 blob cac1a89d166ab8518b11d836216f0a1fb79e6a47	Talk-Leee/src/components/campaigns/event-stream.stories.tsx
100644 blob 820f8def668f67f46cdd1fc6c38de590fc4d58e1	Talk-Leee/src/components/campaigns/event-stream.tsx
100644 blob c2b330092c31f15acfb9b2a7ca3ade040a0b159b	Talk-Leee/src/components/campaigns/knowledge-panel.tsx
100644 blob f52111782891444e66f2e086df29c56af5529710	Talk-Leee/src/components/campaigns/live-calls-panel.test.tsx
100644 blob 416019d86e7b7331fa1c8514a3aab64404159335	Talk-Leee/src/components/campaigns/live-calls-panel.tsx
100644 blob 9139b52b2f2a9a805b880a30c56ac6ea9a94e0ef	Talk-Leee/src/components/campaigns/rejected-inbound-calls-panel.test.tsx
100644 blob 0575dac5905a5fcc7fc09bbe4acb70e3ac49713d	Talk-Leee/src/components/campaigns/rejected-inbound-calls-panel.tsx
100644 blob 55e161b42ae89f6c513bb57ac14e5970f646c8f2	Talk-Leee/src/components/campaigns/script-card.tsx
100644 blob dc96018d51bbd5c902d9671a9da5e036e1ec1c79	Talk-Leee/src/components/campaigns/smart-csv-import.tsx
100644 blob 0ad7f247ca2b31f88c77d4de8afdc51b77bdb07e	Talk-Leee/src/components/campaigns/test-agent-button.tsx
100644 blob 636144c38197d31004bb1b8b757947f06e82f8ab	Talk-Leee/src/components/campaigns/voice-provider-picker.tsx
100644 blob bd09b97573e057954e7a1b696a5ba4bdb428b502	Talk-Leee/src/components/connectors/connector-card.test.ts
100644 blob 8510a9000ae22d2a864e7247e230a36905b65ddf	Talk-Leee/src/components/connectors/connector-card.tsx
100644 blob 400b37fc3ace08302c7d3da01ba91079a8488f58	Talk-Leee/src/components/contacts/csv-import-mapper.tsx
100644 blob 93e540b1233cd19d7ba5ff89fa55bcc2f6477ca9	Talk-Leee/src/components/dashboard/PartnerDashboard.tsx
100644 blob ba53846eeaed2b73dd44acda4f1954a47cdd67e4	Talk-Leee/src/components/dashboard/dialer-insights.tsx
100644 blob 8c77703d0724c94a389955126e0d3c5dd703ac1e	Talk-Leee/src/components/email/connector-warning.tsx
100644 blob e217784230f55022c0fb982dbee592b8cdb585e2	Talk-Leee/src/components/email/html-preview.tsx
100644 blob 714f06e018ec78a3c647a848d9ae738db87947a3	Talk-Leee/src/components/email/rich-text-editor.tsx
100644 blob f7135a0c6f64d3679e97150585133fd397818acb	Talk-Leee/src/components/email/send-email-modal.tsx
100644 blob cf13949ec97c12784cffd4384cf7dda6db64de3b	Talk-Leee/src/components/email/send-history.tsx
100644 blob 07eaac9c8b5a29dd40c7289cbff979198c52360d	Talk-Leee/src/components/email/templates-panel.tsx
100644 blob 6e73f397cfa5fd0757fbfe42cfb8bb24eb828b1d	Talk-Leee/src/components/guards/route-guard.tsx
100644 blob 8f3dfda56399836d6fed95d11f4604da4327d40b	Talk-Leee/src/components/home/contact-section.test.tsx
100644 blob 0d6a9b7671c57cd5fedbb25233f91e492c455f85	Talk-Leee/src/components/home/contact-section.tsx
100644 blob 87de7f9da379b8aec14852510db315d6cbf0267d	Talk-Leee/src/components/home/cta-section.tsx
100644 blob 9c1e64accc9d0987a5c2576b4724a5e058160e9f	Talk-Leee/src/components/home/features-section.tsx
100644 blob 576b131be4150c7ea0a3a1a7e1ca41ea30ed1486	Talk-Leee/src/components/home/footer.tsx
100644 blob 42397ffd62f0a78f55940980625ae012ffd00240	Talk-Leee/src/components/home/home-lazy-sections.tsx
100644 blob ed1a79bae8411fe555afa26b966ed3896707443f	Talk-Leee/src/components/home/navbar.test.tsx
100644 blob 14cd60d4b5131a746d7ab3962e345c5705c8987e	Talk-Leee/src/components/home/navbar.tsx
100644 blob cd4f31b1a0b4066fe723078561c4fda11a9df850	Talk-Leee/src/components/home/packages-section.tsx
100644 blob 2ca2d65722dc02f3957d2dad651c0dd0b9b3cef8	Talk-Leee/src/components/home/secondary-hero.tsx
100644 blob 86087621616135230f54b1f3d1f03be648b6fc33	Talk-Leee/src/components/home/stats-section.tsx
100644 blob a829e41b66cb0242c053a8c024f4ddeef204e1e5	Talk-Leee/src/components/home/trusted-by-section.tsx
100644 blob 2915084fe4f0a766c6bb9aa1b198ee4d19d1e2d1	Talk-Leee/src/components/inbound/inbound-campaign-form.test.ts
100644 blob 365382f144cdbca3fd8f7bb302bf2d4d8127cd4e	Talk-Leee/src/components/inbound/inbound-campaign-form.tsx
100644 blob 015538f40ac1a0db14ae96fe1e5683383a580407	Talk-Leee/src/components/inbound/inbound-page-state.tsx
100644 blob e7ccd285a695d0e23e356bfe8c6ec1286f67ad34	Talk-Leee/src/components/inbound/inbound-status.tsx
100644 blob 606ef9095505da105c6d9e1fe9e8444aa59f3c62	Talk-Leee/src/components/layout/breadcrumbs.tsx
100644 blob b8e284dc25de4cf218fbc5b6520a45f1cc87f41b	Talk-Leee/src/components/layout/dashboard-layout.tsx
100644 blob 82bc4f877d9806cbb3783b635c8cdbb6d459e687	Talk-Leee/src/components/layout/global-sidebar-toggle.tsx
100644 blob 496f2348a1321ec5eedf605abb8028af7467e2ee	Talk-Leee/src/components/layout/sidebar.tsx
100644 blob 9174423e57991723746b5e33e96e3e048d797061	Talk-Leee/src/components/legal/legal-document.tsx
100644 blob 6a16c6cb608a13a0b0717e7b741afeb41b8e174e	Talk-Leee/src/components/notifications/notification-bell.tsx
100644 blob ef58f854e6ab921943dc1473340dcce880dc3240	Talk-Leee/src/components/notifications/notification-center-drawer.tsx
100644 blob 37234562615889e120f1350b4e8c2c7d5846dfc9	Talk-Leee/src/components/notifications/notification-center.tsx
100644 blob 42750f6a4c8aa41dcc1e3a4aeb9722a7a27e03b6	Talk-Leee/src/components/notifications/notification-toaster.tsx
100644 blob 5063acbab372936a19f695eb76fe274d8816bc0f	Talk-Leee/src/components/notifications/qualified-lead-alerts.test.ts
100644 blob 1dc50ec6afe2dca5b9ceb738769a8a52e4e75c46	Talk-Leee/src/components/notifications/qualified-lead-alerts.tsx
100644 blob 838fb626ecc0b7b6e9113ed2ece47201d5cdb495	Talk-Leee/src/components/providers/app-providers.tsx
100644 blob abdea116f0114785fe8db4704049b1fcd420ae5d	Talk-Leee/src/components/providers/prefetch-on-auth.tsx
100644 blob aadef9fe59c77654c86cfe6abe0462ef3c607ef3	Talk-Leee/src/components/providers/theme-provider.tsx
100644 blob e1df672a7793b4b86ac2cacb2cf949030e36b715	Talk-Leee/src/components/recordings/recording-media-controls.test.tsx
100644 blob 84625f202118aee94855e7f04bc4b678291ac9a5	Talk-Leee/src/components/recordings/recording-media-controls.tsx
100644 blob e2128cef1fe76f2c707bbcec7711bd44e0cd4138	Talk-Leee/src/components/reminders/reminder-row.stories.tsx
100644 blob 744284a26c101e6d5f3418637fa0e97cc8575d70	Talk-Leee/src/components/settings/sip-trunks-list.tsx
100644 blob 742cb164758a5e81c6030ea495fea30a6a104f39	Talk-Leee/src/components/settings/telephony-providers-section.tsx
100644 blob 671f9e243d3f4c94d6b9f47d9833d82ea1c06e64	Talk-Leee/src/components/states/page-states.test.ts
100644 blob acd14086fa3cc538ae127ef0e62a9b61b0f7c37f	Talk-Leee/src/components/states/page-states.tsx
100644 blob a7f3f0c747de65e549122d8ea14bd78388966e3e	Talk-Leee/src/components/ui/button.stories.tsx
100644 blob 4a2d1c4a9fd26f00d47169c7ba4e8be9f6ce362c	Talk-Leee/src/components/ui/button.tsx
100644 blob 7bbfbf5a5f9aad34178bff497f0cbcb020374be4	Talk-Leee/src/components/ui/card.stories.tsx
100644 blob e67b3c6a5850b699e8fa38198ad8a1674e2ccb34	Talk-Leee/src/components/ui/card.tsx
100644 blob 6b45ec8ba4df430f26676ef983dc530147e79632	Talk-Leee/src/components/ui/confirm-dialog.test.ts
100644 blob 0c39895beda192c8dcc41b4ccbfb28ed3990bb31	Talk-Leee/src/components/ui/confirm-dialog.tsx
100644 blob 8ce86a5439a4c0518df9f230b5e154a7dd48e672	Talk-Leee/src/components/ui/dashboard-charts.stories.tsx
100644 blob 4e80960638567109094688589f7dc5cf05121383	Talk-Leee/src/components/ui/dashboard-charts.tsx
100644 blob e687b77bc97678a4a6744b6e2027d28e55709ddd	Talk-Leee/src/components/ui/health-indicator.stories.tsx
100644 blob d773c92ac0814a3c21bb91ba16fec9d8db645741	Talk-Leee/src/components/ui/health-indicator.tsx
100644 blob 9716902e577c11c6c0cb9b864bcf9594dd55178c	Talk-Leee/src/components/ui/health-stat-card.stories.tsx
100644 blob 12d2cdef58daf5605b734f081c2fc69c95ae95d3	Talk-Leee/src/components/ui/health-stat-card.tsx
100644 blob 963d8f584a20dc0253510c95f8371abc3c4f52c9	Talk-Leee/src/components/ui/helix-hero.stories.tsx
100644 blob 817e0fde62d918cc918918cde483e9337fefe740	Talk-Leee/src/components/ui/helix-hero.tsx
100644 blob 673045aef000befd9234062407cd1f636ec09474	Talk-Leee/src/components/ui/hover-tooltip.stories.tsx
100644 blob 97fd8ac7af248a7d8b131c688377c3e35e714f12	Talk-Leee/src/components/ui/hover-tooltip.tsx
100644 blob c007945265ac470d74bdcbecab8fa0534590483e	Talk-Leee/src/components/ui/info-tip.test.tsx
100644 blob 90b57a58a4c5700923e29da1f6e0969a3e30fb6d	Talk-Leee/src/components/ui/info-tip.tsx
100644 blob b0969a0ed59c6a743367564242d76a153abfcf7d	Talk-Leee/src/components/ui/input.stories.tsx
100644 blob 0c698e567b19ada00fe67b069dec8b7e4a2d01c0	Talk-Leee/src/components/ui/input.test.tsx
100644 blob dfb9f4f4dd8fd3875f69604ca574990446fb1ff1	Talk-Leee/src/components/ui/input.tsx
100644 blob af990cd7a8a546bc9b186be3ae070040c211399d	Talk-Leee/src/components/ui/label.stories.tsx
100644 blob ddd6c33ed5d15dbbd395a88ba6d4a078cc1c0ee5	Talk-Leee/src/components/ui/label.tsx
100644 blob d02f8fa8b8b604ab825344ad429b73184678e461	Talk-Leee/src/components/ui/modal.stories.tsx
100644 blob 38e64dce3e686ef3b86123b98c7b5010c75f58ca	Talk-Leee/src/components/ui/modal.tsx
100644 blob 81fb911c89727b50f5bfe4d26ec21446142aa5bf	Talk-Leee/src/components/ui/morphing-cursor.stories.tsx
100644 blob 19a0fe8a31fdb3c3575ce7d22898627ccd1c1645	Talk-Leee/src/components/ui/morphing-cursor.tsx
100644 blob 6210802b23f3cc85a29f49326e581fce5e8e8aab	Talk-Leee/src/components/ui/select.stories.tsx
100644 blob 30bb054eb1b871a521e3a233fe8f989cc21d9752	Talk-Leee/src/components/ui/select.tsx
100644 blob 4c66a9795286e335b4e3ebcd29b56ae5309e7f9e	Talk-Leee/src/components/ui/status-pill.test.ts
100644 blob 627322982550f8ebb8f785719d22aee42722a067	Talk-Leee/src/components/ui/status-pill.tsx
100644 blob 027f64923074bb5ca9def43831f308b989efd713	Talk-Leee/src/components/ui/switch.stories.tsx
100644 blob fcb8753b0ad65b8024c1433dba57553efd784080	Talk-Leee/src/components/ui/switch.tsx
100644 blob 26eb109120e2ee43feddb68900f353a119976c41	Talk-Leee/src/components/ui/tabs.tsx
100644 blob 21bd6d22a80933c11566031225c3f598c3bf97ce	Talk-Leee/src/components/ui/tooltip.stories.tsx
100644 blob 64594f754e9173938f33ace1da248cbec1e668b5	Talk-Leee/src/components/ui/tooltip.tsx
100644 blob e9f40f30a624f6ea8c542038e9a19c192701b239	Talk-Leee/src/components/ui/viewport-drawer.stories.tsx
100644 blob a2676a827c895a358eccc51b4b85135d879727a4	Talk-Leee/src/components/ui/viewport-drawer.tsx
100644 blob f5a82a97c8d54248ab1cf850c8f89ad239ab3e96	Talk-Leee/src/components/ui/voice-agent-popup.tsx
100644 blob 3a17048604ae2ab33a518f5432e3f8f2e355cb87	Talk-Leee/src/components/white-label/white-label-branding-provider.tsx
100644 blob 81c40ab08a87d975b0e61e0d8497fe4dd3c11da4	Talk-Leee/src/fonts/satoshi/Satoshi-400.woff2
100644 blob ffd0ac96c7b87a1411465f450322b41f5ed875b6	Talk-Leee/src/fonts/satoshi/Satoshi-500.woff2
100644 blob 0a8db7a468b8c16027691be3f5929e7223542438	Talk-Leee/src/fonts/satoshi/Satoshi-700.woff2
100644 blob aebe782768d1c9fda68d5d462cf88ed988808ce4	Talk-Leee/src/hooks/useAuth.ts
100644 blob b0533be1372054bddd0f02a86f0036a7e17631ab	Talk-Leee/src/instrumentation-client.ts
100644 blob 78925d7cd5a6b409fd970f25f34a01e9e2db13a2	Talk-Leee/src/instrumentation.ts
100644 blob 68c844c2a72d887737c5788f4957e569fc7f569d	Talk-Leee/src/lib/admin-access.test.ts
100644 blob 7330c825c798c76c58ffe5f67f58d2e058808999	Talk-Leee/src/lib/admin-access.ts
100644 blob a25ac3176d4172a00ad3f43641acb0e23968b309	Talk-Leee/src/lib/ai-options-api.ts
100644 blob 973adb3b3ae1eeee7b0e7becb41d6a7d8e76045f	Talk-Leee/src/lib/alerts-api.ts
100644 blob cf57a237d743bb9c30500ac5e507206bc26ace23	Talk-Leee/src/lib/api-hooks.ts
100644 blob 094722948ae30a320c548b4bec99aed47b843a8e	Talk-Leee/src/lib/api.ts
100644 blob 71544c0d4003b191ece06946d916d84928e66ab2	Talk-Leee/src/lib/assistant-model-api.ts
100644 blob b5e74fcda9355d291043ff7560341407fd85aabe	Talk-Leee/src/lib/audio-recording.test.ts
100644 blob 964188227a9804a156176e7cf8e7d42dc90c1496	Talk-Leee/src/lib/audio-recording.ts
100644 blob 692340d4d4fb120771bd443b19ca694df3279c8e	Talk-Leee/src/lib/auth-context.tsx
100644 blob 45686e41029e96db842fc77b57104ae8c4cf7997	Talk-Leee/src/lib/auth-hooks.ts
100644 blob 80edd4cbe2837bf471eb67db10293194017beda3	Talk-Leee/src/lib/auth-roles.ts
100644 blob e4918443015e75e097bd79e49152c0a584557d9e	Talk-Leee/src/lib/auth-token.ts
100644 blob b7298dd515b0137b5a86f970cb9d150943cc2fba	Talk-Leee/src/lib/backend-api.admin.test.ts
100644 blob 587df9ea1e4478f935b7449cf2db25c193f07497	Talk-Leee/src/lib/backend-api.assistant.test.ts
100644 blob cd293b3cf7c2068ec37a096f2f07f25f59b8d985	Talk-Leee/src/lib/backend-api.calendar-events.test.ts
100644 blob c0e214712fd9784290c7689fc723ffa1f78d5a9f	Talk-Leee/src/lib/backend-api.connectors.test.ts
100644 blob a35efb559489d1ce84ce3138df1e6c8a672a3429	Talk-Leee/src/lib/backend-api.email.test.ts
100644 blob 99125ba663d75e94ba14ebe55b8d02563e9cadae	Talk-Leee/src/lib/backend-api.ts
100644 blob 50134f8f39b60d4221c7f0b48f17697f5ed2266a	Talk-Leee/src/lib/backend-api.voice-calls.test.ts
100644 blob df2312801f410baf1ab46835a57619e9cc6054ee	Talk-Leee/src/lib/backend-endpoints.ts
100644 blob d64b729f3564ed6f2d5c6e44b0f16945d26ee983	Talk-Leee/src/lib/billing-api.test.ts
100644 blob 1b4fd9d64909d11e935237b974100e66786ccdaa	Talk-Leee/src/lib/billing-api.ts
100644 blob c7dd954e2b97b7eb33ac17635b894e870865ffaf	Talk-Leee/src/lib/billing-types.ts
100644 blob ec34f0b27e579661dc19e7d35cacbf1bc53d609a	Talk-Leee/src/lib/calling-window.ts
100644 blob 9ccaf4217a7d2f085f5aa877d70a6f79b4a2a22c	Talk-Leee/src/lib/campaign-performance.test.ts
100644 blob f6653310232aaec9e3b1f994c5d8eda1f7006623	Talk-Leee/src/lib/campaign-performance.ts
100644 blob 80b9ff42196a80a6d8d6abac899d9091478340fd	Talk-Leee/src/lib/campaign-personas.ts
100644 blob 30a190241e59e7597a9e9d5550e00a491bbd58e8	Talk-Leee/src/lib/connectors-utils.test.ts
100644 blob e2112f1f245155cb0b6f8ed220cf8a654d6b54cd	Talk-Leee/src/lib/connectors-utils.ts
100644 blob d641462a86bee4572c45022e873e52cd0dedea14	Talk-Leee/src/lib/contact-csv.ts
100644 blob a96ef560f372d1f48a9a925dd28cd96c100b5f8a	Talk-Leee/src/lib/contact-form.test.ts
100644 blob ee65a2aacd72178731e1649ef97a8d199894670d	Talk-Leee/src/lib/contact-form.ts
100644 blob 012c92bfeb6fd57f197d0e3536a6178ca0dffd0c	Talk-Leee/src/lib/dashboard-api.inbound.test.ts
100644 blob 0a7b6758856c5c1475a1742c82faedf9b4c42946	Talk-Leee/src/lib/dashboard-api.ts
100644 blob a9486ec8279ed5fa8ca4986e792ff7db761c2069	Talk-Leee/src/lib/dashboard-layout-removal.test.ts
100644 blob 17fd72b77ae4cd5bf77e2ffb15827e3e7925533b	Talk-Leee/src/lib/donut-label-layout.mjs
100644 blob bacacaa9cea9de7d0972dbfe0438021d9870065f	Talk-Leee/src/lib/email-audit-client.tsx
100644 blob c383380db3f69d407db312a5545c0b5e0cbf9267	Talk-Leee/src/lib/email-audit.test.ts
100644 blob a8e11052f3711880fe261d0862ab8119bdde2e03	Talk-Leee/src/lib/email-audit.ts
100644 blob 553f38c07f67b2d0942cd0dbf443da77501a0553	Talk-Leee/src/lib/email-utils.test.ts
100644 blob 5170769b221f5bcb8bf6daccaac8e5aab91e87c0	Talk-Leee/src/lib/email-utils.ts
100644 blob 4e72453ff29b80295631466a341695432ca86c28	Talk-Leee/src/lib/env.test.ts
100644 blob 0026ad08c40d8061ffe65064263bed11e6d97a95	Talk-Leee/src/lib/env.ts
100644 blob 1d8e5f3f23d276cd50c3105c160b683e05b4f8a8	Talk-Leee/src/lib/event-stream-api.ts
100644 blob b2f9eee33bc61f2b5ce09f7256c576fc56d3c5f4	Talk-Leee/src/lib/extended-api.recordings.test.ts
100644 blob 58dc5b09edba509103b0ddbeae4705254587aeec	Talk-Leee/src/lib/extended-api.rewards.test.ts
100644 blob 3ab5c252977c5f78f6e3d9886a03e6c6ba59c660	Talk-Leee/src/lib/extended-api.ts
100644 blob 0c7918d196a9a6e506cf4eb912429f0592b7608a	Talk-Leee/src/lib/http-client.session-expired.test.ts
100644 blob 016e5e42b195cec44043ee11dcdcbb9812f9823c	Talk-Leee/src/lib/http-client.test.ts
100644 blob b4c0c56e5c7ac29e5f74a033c49ff750beebacc9	Talk-Leee/src/lib/http-client.ts
100644 blob 08d8fa45e915713e46d17ebe86287868ec8fb58b	Talk-Leee/src/lib/inbound-api.test.ts
100644 blob 2344459dced2aaf6a5bc2a34cc0e155d45777c98	Talk-Leee/src/lib/inbound-api.ts
100644 blob ea2f7b082c6e256e480b0170d791189e64f068d4	Talk-Leee/src/lib/inbound-permissions.test.ts
100644 blob db0a9f8d4aeb8883c92db661c28088577796279e	Talk-Leee/src/lib/inbound-permissions.ts
100644 blob 1ce4c5862a791cebb294ecaa2bb89f5e4d14a410	Talk-Leee/src/lib/inbound-validation.ts
100644 blob 3821803724c0b852a070513aaf090cf41b568405	Talk-Leee/src/lib/lead-details-api.test.ts
100644 blob 3d5f3f20839be46a50a0107ff1da72620e89d2ed	Talk-Leee/src/lib/lead-details-api.ts
100644 blob 7e2bfa5ef401c913c5db150b1a1e82887b55418a	Talk-Leee/src/lib/lead-outcome.test.ts
100644 blob 8ca3b35a34e276c957c7465aa663eaccf4a64439	Talk-Leee/src/lib/lead-outcome.ts
100644 blob 3effbf7dd54b5d3f657ec80d2808c856fefd37c6	Talk-Leee/src/lib/media-permissions.test.ts
100644 blob 4cc457c58cc6c765ca65712b1b8a36bbe0f99aca	Talk-Leee/src/lib/media-permissions.ts
100644 blob 24c3384cf8f5293be6ef894d6585cfd1a59fb547	Talk-Leee/src/lib/meetings-utils.test.ts
100644 blob ef8ca34e8e2cb59010141801993e8d07268b9770	Talk-Leee/src/lib/meetings-utils.ts
100644 blob 4dbe3a6685a0f07aa6302410560b327c5e4220a0	Talk-Leee/src/lib/mfa-utils.ts
100644 blob dbc96983c0552e134bc9d8cbfb8aabe11aec35bc	Talk-Leee/src/lib/minutes-usage-layout.mjs
100644 blob 4f1c327864802a77227e98e6a65efda2b80cab08	Talk-Leee/src/lib/models.ts
100644 blob 85f58266ea667b080caec754fa461ab39b04736f	Talk-Leee/src/lib/monitoring.ts
100644 blob 69cdfdb0ce450ebe21a828279f204d31aeb6d763	Talk-Leee/src/lib/notifications-client.tsx
100644 blob 203d87cf69e278e43fc4e27b64a9ed22d69d12fc	Talk-Leee/src/lib/notifications.test.ts
100644 blob e0e196f2d127ee8f614819222c1258086f947756	Talk-Leee/src/lib/notifications.ts
100644 blob 017a585e67fcf3764dcc2a8b4ad39a007de8d748	Talk-Leee/src/lib/queries/ai-options-queries.ts
100644 blob 49765f96ae1346e4f34f57fe4010fb8840aa0904	Talk-Leee/src/lib/queries/inbound-queries.test.ts
100644 blob 2df1960f7f94785f26f6f88626f96e3bbedb063f	Talk-Leee/src/lib/queries/inbound-queries.ts
100644 blob d1ef59453351721077e5a95191168acd878f1ee9	Talk-Leee/src/lib/reminders-utils.test.ts
100644 blob fff3ffa783d1f0199d1e8238d7dd01773b7d39c1	Talk-Leee/src/lib/reminders-utils.ts
100644 blob bc6b5a7901a0d33916534841c57445e674322dab	Talk-Leee/src/lib/review-permissions.test.ts
100644 blob c92593b0b8debfa3372d448569687f656039c84e	Talk-Leee/src/lib/review-permissions.ts
100644 blob 388caa7a5a720566145e8b89a4ca08f9f5addad9	Talk-Leee/src/lib/routes.test.ts
100644 blob 5b34c5c11aac440fb6249861a4bf3db108156580	Talk-Leee/src/lib/server-auth.ts
100644 blob 2d1d1b95fa59f0bd39cf5f64c5f0f29ecec4c487	Talk-Leee/src/lib/session-utils.ts
100644 blob 04ff19e812c69d3b1c78cca29ff38146a041cb13	Talk-Leee/src/lib/sidebar-client.tsx
100644 blob fec46bb55b6e03448bcbc82511126c368bfd1d7c	Talk-Leee/src/lib/sidebar.test.ts
100644 blob 48791a36a14d082f903c3681d5cefd689f867ef3	Talk-Leee/src/lib/sidebar.ts
100644 blob cc869c8536b72168652a40ca868ba5a33485cb30	Talk-Leee/src/lib/status-colors.ts
100644 blob c584292e6925c87a71befdea32a9ca6b86cd94f8	Talk-Leee/src/lib/structural-auth-isolation.test.ts
100644 blob 9af7861697efc642bc5203a391adfb30a6298a13	Talk-Leee/src/lib/structural-no-bare-fetch.test.ts
100644 blob 392d3e1935c588ce1b00e6976d3e6a1e944d3145	Talk-Leee/src/lib/structural-single-httpclient.test.ts
100644 blob d766218ff81b602ef4b487bbc6f2995be3830ef5	Talk-Leee/src/lib/structural-single-me-call.test.ts
100644 blob f7950c723d99bf06939dc21fb17632d975fbd05f	Talk-Leee/src/lib/telephony-api.ts
100644 blob 03c67626f8c6cecb3093de9d08c65021983606ad	Talk-Leee/src/lib/temperature-advice.ts
100644 blob 539d1fcb2452acfe9faa4ef878f711a11c0be78e	Talk-Leee/src/lib/theme-implementation.test.ts
100644 blob dbab851e32504026c5f808314b9579f24e0b5c28	Talk-Leee/src/lib/topup-api.test.ts
100644 blob 351d33ba52f2aac6409d87ce0b8bc64722e8546e	Talk-Leee/src/lib/topup-api.ts
100644 blob bd0c391ddd1088e9067844c48835bf4abcd61783	Talk-Leee/src/lib/utils.ts
100644 blob 77cc99d10d9f02b51be55ae0528b219aa6cbf5a7	Talk-Leee/src/lib/webauthn-utils.ts
100644 blob 7ca490dd3ab9e78766e086f5d8e3ed7afe1d5d21	Talk-Leee/src/lib/white-label/branding.ts
100644 blob 7a784e240a817b2193832729bbefb6c72b1b2a54	Talk-Leee/src/proxy.trailing-slash.test.ts
100644 blob f04e6090ea94c2a14f2a6f8a836472e5ab0a4af0	Talk-Leee/src/proxy.ts
100644 blob 5ba93854f2488426b94be12fc39175b0b7599c9f	Talk-Leee/src/server/api-security.test.ts
100644 blob e6d41d495a8129bdf2091ecd48b64480d85af53e	Talk-Leee/src/server/api-security.ts
100644 blob 843c5961abd79ab908b9ede3d0b3c229746af6ff	Talk-Leee/src/server/auth-core.test.ts
100644 blob 92c590d2968fe8e374b628d7dc80dd87368d8222	Talk-Leee/src/server/auth-core.ts
100644 blob 537e7cf8e8850807d85657d12d424ba1d64b0254	Talk-Leee/src/server/db.ts
100644 blob 6e83d9c788890956669fd6cc5d0f763899b30bd9	Talk-Leee/src/server/mfa.test.ts
100644 blob 6b016fb8b5ce910c8c0d45917ba06558de256a80	Talk-Leee/src/server/mfa.ts
100644 blob 042881271e46d77086a858d7c381bb5c9638af42	Talk-Leee/src/server/passkeys.test.ts
100644 blob 08005ddbbd861b923eef42498e64cc2c550d7160	Talk-Leee/src/server/passkeys.ts
100644 blob 9e01bba45cb713f630f302e048119982a0187892	Talk-Leee/src/server/rbac.test.ts
100644 blob 609137e8fa141a05bd3e70974f4eb0206c521069	Talk-Leee/src/server/rbac.ts
100644 blob 221b151da25ec567778d59a414aecd06f86d8e5d	Talk-Leee/src/server/session-security.ts
100644 blob 02ee14bae1ba3873f2624d664168f153720be09e	Talk-Leee/src/server/voice-security.test.ts
100644 blob 3551a47e53aa52f19a842ab366821fbd3abe962f	Talk-Leee/src/server/voice-security.ts
100644 blob c8a1dd4eca87a53ff88c15bbdf164fcc180adfdd	Talk-Leee/src/test-utils/dom.ts
100644 blob 76ce83017c19d4932b6f8387d01b5a5e52d8c6d8	Talk-Leee/src/test-utils/render.tsx
100644 blob 6862e8279d9a9e154772a9a5bccbf750bd5173ea	Talk-Leee/src/test-utils/setup.ts
100644 blob 6ba3d04e8ab0dd3f6c4cee5401cc953c1bccaf81	Talk-Leee/test-artifacts/multi-tenant-concurrency/chromium/01-white-label-dashboard-attempt.png
100644 blob befeb7610536c042a4260e41328057a1e75c9f4f	Talk-Leee/test-artifacts/multi-tenant-concurrency/chromium/02-partner-created.png
100644 blob 683f61970fbe5d57023c96eb16f74110d676b629	Talk-Leee/test-artifacts/multi-tenant-concurrency/chromium/02-tenants-page.png
100644 blob 79dd05321481f9afe7b36bd659468ca047062f2d	Talk-Leee/test-artifacts/multi-tenant-concurrency/chromium/03-sub-tenant-created.png
100644 blob 5c48e60515d4fc68082fd32ca400272486fac3bc	Talk-Leee/test-artifacts/multi-tenant-concurrency/chromium/03-tenants-page.png
100644 blob 739a3e6d5502656d3335246d868368257035836e	Talk-Leee/test-artifacts/multi-tenant-concurrency/chromium/04-concurrency-ramp-results.json
100644 blob cb2eaafb8e35dcb147cf9bc9b7fe7191883844ee	Talk-Leee/test-artifacts/multi-tenant-concurrency/chromium/04-sub-tenant-created.png
100644 blob cf22e7cd2dd08343ed84c60861b97f4e496ad866	Talk-Leee/test-artifacts/multi-tenant-concurrency/chromium/05-agent-settings-loaded.png
100644 blob 517db690982cb53972272a8395b37b1155dd073b	Talk-Leee/test-artifacts/multi-tenant-concurrency/chromium/05-concurrency-ui-blocking.png
100644 blob 6c65e8f8d6085f50f003c07b994b796e30f6e7a6	Talk-Leee/test-artifacts/multi-tenant-concurrency/chromium/05-tenants-page-before-blocking.png
100644 blob 7c2925578abf45406ac53e6d6fbc1bf95eaad1ef	Talk-Leee/test-artifacts/multi-tenant-concurrency/chromium/06-tenants-page-before-blocking.png
100644 blob 7b87cea36bfc0a3ce0bae74ac5164812e60fd8de	Talk-Leee/test-artifacts/multi-tenant-concurrency/chromium/06-ui-blocking.png
100644 blob cbfac1b1ac183d28c1c8e7502c0e5839e85e6888	Talk-Leee/test-artifacts/multi-tenant-concurrency/chromium/07-ui-blocking-allocations.png
100644 blob 723e063b543d6fc790cdcd49e353f13f23a8ab39	Talk-Leee/test-artifacts/multi-tenant-concurrency/chromium/page-errors.json
100644 blob 292a0b9833c3c410c3108526d9fbd1b819bd45ff	Talk-Leee/test-artifacts/multi-tenant-concurrency/firefox/01-white-label-dashboard-attempt.png
100644 blob ac1d16da810734c6d98f253b95106e7657973d2f	Talk-Leee/test-artifacts/multi-tenant-concurrency/firefox/02-partner-created.png
100644 blob c76537c2982f880710785efab2c231f96149e30d	Talk-Leee/test-artifacts/multi-tenant-concurrency/firefox/03-tenants-page.png
100644 blob b0cf8b7a6961d9a09c9caca602413b8488c27857	Talk-Leee/test-artifacts/multi-tenant-concurrency/firefox/04-concurrency-ramp-results.json
100644 blob da99fafdde367b78b36181d5d6cea5a6156efbe5	Talk-Leee/test-artifacts/multi-tenant-concurrency/firefox/04-sub-tenant-created.png
100644 blob cbc633c97a43cad7462d3679173c6fbfb64425ce	Talk-Leee/test-artifacts/multi-tenant-concurrency/firefox/05-agent-settings-loaded.png
100644 blob c335558ace21b8616cbc340f63422e24bb02baa5	Talk-Leee/test-artifacts/multi-tenant-concurrency/firefox/05-concurrency-ui-blocking.png
100644 blob 93b83a5859a0d1bf415ef4f701fd4b2582c24db1	Talk-Leee/test-artifacts/multi-tenant-concurrency/firefox/06-tenants-page-before-blocking.png
100644 blob 9a473b96d07965739794fa97f58b8fff69561f29	Talk-Leee/test-artifacts/multi-tenant-concurrency/firefox/07-ui-blocking-allocations.png
100644 blob 0637a088a01e8ddab3bf3fa98dbe804cbde1a0dc	Talk-Leee/test-artifacts/multi-tenant-concurrency/firefox/page-errors.json
100644 blob f785ad0a2a149014b95af6b800010615001bce0e	Talk-Leee/test-artifacts/multi-tenant-concurrency/msedge/01-white-label-dashboard-attempt.png
100644 blob 3accdca4b61dc99852fc074b621f2d41e69561d6	Talk-Leee/test-artifacts/multi-tenant-concurrency/msedge/02-partner-created.png
100644 blob fc32a9d3fc5809bcd04b152673c78b543abf4d51	Talk-Leee/test-artifacts/multi-tenant-concurrency/msedge/03-tenants-page.png
100644 blob 7255ab1b656b1727fc3ff8f9ba7e855997177c4c	Talk-Leee/test-artifacts/multi-tenant-concurrency/msedge/04-concurrency-ramp-results.json
100644 blob 987916b823325e37120404babe1f56778ca7c1e9	Talk-Leee/test-artifacts/multi-tenant-concurrency/msedge/04-sub-tenant-created.png
100644 blob a4e8aa099112cdc1c9a60a878613527de8b5c4bb	Talk-Leee/test-artifacts/multi-tenant-concurrency/msedge/05-agent-settings-loaded.png
100644 blob 69d326cf4c82fcaa5e4bfa164d477843ab03c978	Talk-Leee/test-artifacts/multi-tenant-concurrency/msedge/05-concurrency-ui-blocking.png
100644 blob 4a10bd9f70ea6f1b4ffef1882aac5a1794b297b5	Talk-Leee/test-artifacts/multi-tenant-concurrency/msedge/06-tenants-page-before-blocking.png
100644 blob b12b504187e8f879b30b78c6f849945e45d807e7	Talk-Leee/test-artifacts/multi-tenant-concurrency/msedge/07-ui-blocking-allocations.png
100644 blob 723e063b543d6fc790cdcd49e353f13f23a8ab39	Talk-Leee/test-artifacts/multi-tenant-concurrency/msedge/page-errors.json
100644 blob bae819550bad2e58af52a630d58a6664753ccc1f	Talk-Leee/test-artifacts/multi-tenant-concurrency/webkit/01-white-label-dashboard-attempt.png
100644 blob c1f0271bdf69bb6079aba80bab16bffcd17a9e27	Talk-Leee/test-artifacts/multi-tenant-concurrency/webkit/02-partner-created.png
100644 blob 9df8e3aba0d24e9fb8d673477cb12a7e3d8bd78f	Talk-Leee/test-artifacts/multi-tenant-concurrency/webkit/03-tenants-page.png
100644 blob 739a3e6d5502656d3335246d868368257035836e	Talk-Leee/test-artifacts/multi-tenant-concurrency/webkit/04-concurrency-ramp-results.json
100644 blob fec5cd7bbd62dd4bb7aa91be86c24eb3875b4fd5	Talk-Leee/test-artifacts/multi-tenant-concurrency/webkit/04-sub-tenant-created.png
100644 blob a0a5deb8fcbdf7eb3c2050bfa7b05a7fba80db7f	Talk-Leee/test-artifacts/multi-tenant-concurrency/webkit/05-agent-settings-loaded.png
100644 blob 6948979efd8fcfbeedb036a6c86e5b71102febd7	Talk-Leee/test-artifacts/multi-tenant-concurrency/webkit/05-concurrency-ui-blocking.png
100644 blob 21f79d1308447df4e8514d95bdb74545dbea6f88	Talk-Leee/test-artifacts/multi-tenant-concurrency/webkit/06-tenants-page-before-blocking.png
100644 blob 2b02804f7e748f06e9a3c02a17d048a5c32c1614	Talk-Leee/test-artifacts/multi-tenant-concurrency/webkit/07-ui-blocking-allocations.png
100644 blob ee2d51b4e179b4ed0551185f6a844124d55419ce	Talk-Leee/test-artifacts/multi-tenant-concurrency/webkit/page-errors.json
100644 blob 05ef89123940ab15876da4137a9c1be530980e04	Talk-Leee/test-report.multi-tenant-concurrency.md
100644 blob 6dd5846f201c33f4c182b88c70225d6f6a49b875	Talk-Leee/test/minutes-usage-layout.test.mjs
100644 blob 8ec8a724c2803e62bd60600dec6380569b5162eb	Talk-Leee/tests/accessibility-audit.spec.ts
100644 blob a16a49f0420dd62cc7789e8eab806851b84c335d	Talk-Leee/tests/agent-settings.transfer-restrictions.spec.ts
100644 blob d3b22d179380100fcf68e58ba1d71e7dcad3a32d	Talk-Leee/tests/auth-audit.spec.ts
100644 blob 6a2a509b144667b05f2c40bdacc9780045c01000	Talk-Leee/tests/connectors.oauth.spec.ts
100644 blob fd0679c20d319073169a5bfbd2d59d10803966ee	Talk-Leee/tests/dashboard-audit.spec.ts
100644 blob 713e6fe8f1f6f5a8c9a1f899501e81448a8a12bc	Talk-Leee/tests/dashboard-first-row.visual.spec.ts
100644 blob 1aa7830663550072f3887bcda702aec54e28f775	Talk-Leee/tests/dashboard-first-row.visual.spec.ts-snapshots/dashboard-kpi-row-desktop-1024-chromium-win32.png
100644 blob 4fb00a920b12031b9375c0fbcf49ff65cc5c7a01	Talk-Leee/tests/dashboard-first-row.visual.spec.ts-snapshots/dashboard-kpi-row-desktop-1024-msedge-win32.png
100644 blob cfe2125715927e9abc55deb1530b80f9893611e7	Talk-Leee/tests/dashboard-first-row.visual.spec.ts-snapshots/dashboard-kpi-row-desktop-1024-win32.png
100644 blob 713a3bb7d722e0787ff4d346e6dc88234a837ee9	Talk-Leee/tests/dashboard-first-row.visual.spec.ts-snapshots/dashboard-kpi-row-mobile-360-chromium-win32.png
100644 blob 46e968d8226168b8d62af96515b368283a139dec	Talk-Leee/tests/dashboard-first-row.visual.spec.ts-snapshots/dashboard-kpi-row-mobile-360-msedge-win32.png
100644 blob 5903d7133b4f5292b96ab5221e1b0f5951457e94	Talk-Leee/tests/dashboard-first-row.visual.spec.ts-snapshots/dashboard-kpi-row-mobile-360-win32.png
100644 blob d6eef26b5ff5940baa81b7779ae383e5628b3f2a	Talk-Leee/tests/dashboard-first-row.visual.spec.ts-snapshots/dashboard-kpi-row-tablet-768-chromium-win32.png
100644 blob d1ff2d185536c466b9c033e70248e9ee39a20884	Talk-Leee/tests/dashboard-first-row.visual.spec.ts-snapshots/dashboard-kpi-row-tablet-768-win32.png
100644 blob e4277ff849fcd5f6372d58377185a20b25754e81	Talk-Leee/tests/dashboard-first-row.visual.spec.ts-snapshots/dashboard-kpi-row-wide-1440-chromium-win32.png
100644 blob 2d72310553be7bb8e41f2f67f8daae6cd5cf3b64	Talk-Leee/tests/dashboard-first-row.visual.spec.ts-snapshots/dashboard-kpi-row-wide-1440-msedge-win32.png
100644 blob c8e2be1b2aa88a428d458fa62027f76d265e1aa6	Talk-Leee/tests/dashboard-first-row.visual.spec.ts-snapshots/dashboard-kpi-row-wide-1440-win32.png
100644 blob 29a6372d38a3c685c2bfe2edba6902fc924eaf87	Talk-Leee/tests/demo.smoke.spec.ts
100644 blob 4c5819c6487ad03cb0b7b7128d24b211a4b5971c	Talk-Leee/tests/donut-label-layout.test.mjs
100644 blob 33a37d0c53b470843c122ba020974d6dd42415f8	Talk-Leee/tests/home-audit.spec.ts
100644 blob 49743a8e648eb55218755f8ca58e8844a4f348c5	Talk-Leee/tests/home-secondary-hero.video.visual.spec.ts
100644 blob 3face2aca2774b0af82267d12cb1c73d5d91261f	Talk-Leee/tests/home-secondary-hero.video.visual.spec.ts-snapshots/home-secondary-hero-player-desktop-1920x1080-win32.png
100644 blob 14c68828914a536704d24db237125d7c2b8a5830	Talk-Leee/tests/home-secondary-hero.video.visual.spec.ts-snapshots/home-secondary-hero-player-mobile-375x667-win32.png
100644 blob 937d23b77ddfeb647baa08f4f1bd29e804f5172e	Talk-Leee/tests/home-secondary-hero.video.visual.spec.ts-snapshots/home-secondary-hero-player-tablet-768x1024-win32.png
100644 blob 5f274e1f5216181dffaaa8d8d93280112e123365	Talk-Leee/tests/inner-pages-audit.spec.ts
100644 blob f37aac495cb0183eb4888744c030a6bd753104eb	Talk-Leee/tests/multi-tenant-concurrency.e2e.spec.ts
100644 blob f52a48879bb66d7ab4f2f4e118233cf6f67cd84b	Talk-Leee/tests/reminders.lifecycle.spec.ts
100644 blob 4955ab8a9589ce0a96b77ed66bf4cb2e130a5ea1	Talk-Leee/tests/responsive.overlap.spec.ts
100644 blob 2691f0f8ef5407223aa2530a7a00d716cbedb4e1	Talk-Leee/tests/tenant-isolation.security.spec.ts
100644 blob e6d69ea98a0f73eb5a26e85d1cf2040dc0ae54ee	Talk-Leee/tests/white-label-branding.spec.ts
100644 blob 608655b59d01b69c4aa0554c27f2a1ced95f5739	Talk-Leee/tsconfig.json
100644 blob 235c2ffd11b9ec1f149f10bf5b2e06484fa69686	Talk-Leee/types/jsdom.d.ts
100644 blob 835a0fd88695a99bc6691acd18ddd94479bfdd3e	goals.md
````

## Appendix C — Production route manifest from the successful build

````text
/_global-error/page
/_not-found/page
/403/page
/admin/abuse-detection/page
/admin/api-keys/page
/admin/audit-logs/page
/admin/billing/page
/admin/billing/tenants/page
/admin/page
/admin/rate-limiting/page
/admin/reviews/page
/admin/secrets/page
/admin/voice-security/page
/admin/webhooks/page
/ai-assist/page
/ai-options/page
/ai-voice-agent/page
/ai-voice-dialer/page
/ai-voices/page
/analytics/page
/api/v1/[...path]/route
/api/voices/route
/api/white-label/branding/[partner]/route
/assistant/actions/page
/assistant/meetings/page
/assistant/page
/assistant/reminders/page
/auth/callback/page
/auth/forgot-password/page
/auth/login/page
/auth/register/page
/billing/invoices/[id]/page
/billing/invoices/page
/billing/page
/billing/plans/page
/calls/[id]/page
/calls/page
/campaigns/[id]/edit/page
/campaigns/[id]/page
/campaigns/new/page
/campaigns/page
/connectors/[type]/callback/page
/connectors/callback/page
/connectors/page
/contact/page
/contacts/page
/dashboard/page
/email/page
/favicon-192.png/route
/favicon-512.png/route
/favicon.ico/route
/favicon.png/route
/inbound-campaigns/[id]/edit/page
/inbound-campaigns/[id]/page
/inbound-campaigns/new/page
/inbound-campaigns/page
/industries/education/page
/industries/financial-services/page
/industries/healthcare/page
/industries/marketing-automation/page
/industries/professional-services/page
/industries/real-estate/page
/industries/recruitment/page
/industries/retail-ecommerce/page
/industries/software-tech-support/page
/industries/travel-industry/page
/meetings/page
/notifications/page
/page
/privacy/page
/recordings/page
/reminders/page
/reviews/page
/security/page
/settings/page
/terms/page
/use-cases/automated-lead-qualification/page
/use-cases/customer-services-support/page
/white-label/[partner]/analytics/page
/white-label/[partner]/billing/page
/white-label/[partner]/dashboard/page
/white-label/[partner]/preview/page
/white-label/[partner]/tenants/[tenant]/agent-settings/page
/white-label/[partner]/tenants/page
/white-label/dashboard/page
````

## Appendix D — Frontend test-source inventory

The following source locations are the executable test declarations present during the successful 315-test run. This is an inventory, not a claim that every line is newly added.

````text
Talk-Leee/src\app\ai-voices\page.test.tsx:106:  test("handles fetch error", async () => {
Talk-Leee/src\app\ai-voices\page.test.tsx:120:  test("toggles play state on button click", async () => {
Talk-Leee/src\app\ai-voices\page.test.tsx:67:  test("renders voices after fetching", async () => {
Talk-Leee/src\app\meetings\meeting-row.test.tsx:12:test("MeetingRow shows participant summary when participants exist", () => {
Talk-Leee/src\app\meetings\meeting-row.test.tsx:27:test("MeetingRow omits participant summary when empty", () => {
Talk-Leee/src\app\security\session-revoke-copy.test.ts:40:test("the sessions section no longer claims revoking ends the session immediately", () => {
Talk-Leee/src\app\security\session-revoke-copy.test.ts:49:test("the sessions section states, in the always-visible text, that the device is not signed out", () => {
Talk-Leee/src\app\security\session-revoke-copy.test.ts:58:test("the tooltip states the rolling window and the remedy in plain language", () => {
Talk-Leee/src\app\security\session-revoke-copy.test.ts:76:test("the password form states the window rather than implying an instant sign-out", () => {
Talk-Leee/src\components\ai-options\controls.test.tsx:11:    test("exposes its formatted value and purpose to assistive technology", () => {
Talk-Leee/src\components\ai-options\controls.test.tsx:30:    test("supports stepped keyboard changes", () => {
Talk-Leee/src\components\ai-options\controls.test.tsx:48:    test("can shrink below its desktop size and retains a visible focus treatment", () => {
Talk-Leee/src\components\billing\billing-overview.test.tsx:114:test("loading is distinct from both the error and the empty state", async () => {
Talk-Leee/src\components\billing\billing-overview.test.tsx:129:test("a failed invoices request never renders 'No invoices yet.'", async () => {
Talk-Leee/src\components\billing\billing-overview.test.tsx:144:test("a failed daily-usage request never renders zeroed call stats", async () => {
Talk-Leee/src\components\billing\billing-overview.test.tsx:161:test("a failed overage-alerts request is surfaced instead of silently showing no alerts", async () => {
Talk-Leee/src\components\billing\billing-overview.test.tsx:81:test("a failed billing request renders the error state, never '0 of 0 minutes used'", async () => {
Talk-Leee/src\components\billing\billing-overview.test.tsx:99:test("a genuinely empty successful response renders the empty state, not an error", async () => {
Talk-Leee/src\components\calls\CallSummaryCard.test.tsx:27:test("actionable AI classifications receive a visible needs-review signal", () => {
Talk-Leee/src\components\calls\CallSummaryCard.test.tsx:36:test("a summary with no actionable classification does not invent confidence", () => {
Talk-Leee/src\components\calls\conversation-review-panel.test.tsx:110:test("teammates' reviews stay visible to an account that cannot write one", async () => {
Talk-Leee/src\components\calls\conversation-review-panel.test.tsx:138:test("a 403 on submit does not offer a Try again that would be refused identically", async () => {
Talk-Leee/src\components\calls\conversation-review-panel.test.tsx:157:test("a server fault still offers Try again, and it re-sends", async () => {
Talk-Leee/src\components\calls\conversation-review-panel.test.tsx:201:            word.test(text),
Talk-Leee/src\components\calls\conversation-review-panel.test.tsx:208:test("the review form promises no points, rewards or credits — even with rewards reported ON", async () => {
Talk-Leee/src\components\calls\conversation-review-panel.test.tsx:235:test("a saved review confirms the save without announcing an award", async () => {
Talk-Leee/src\components\calls\conversation-review-panel.test.tsx:271:test("an existing review can be edited without any wording about awards", async () => {
Talk-Leee/src\components\calls\conversation-review-panel.test.tsx:57:test("a read-only account is told it cannot review instead of being shown the form", async () => {
Talk-Leee/src\components\calls\conversation-review-panel.test.tsx:75:test("calls:create renders the review form", async () => {
Talk-Leee/src\components\calls\conversation-review-panel.test.tsx:83:test("a failed permission lookup is reported as unchecked, not as a refusal", async () => {
Talk-Leee/src\components\campaigns\campaign-lead-fields.test.ts:24:test("new campaign defaults include useful conversational fields only", () => {
Talk-Leee/src\components\campaigns\campaign-lead-fields.test.ts:46:test("the picker makes field access and requiredness explicit", async () => {
Talk-Leee/src\components\campaigns\live-calls-panel.test.tsx:109:test("inbound live rows show direction, ANI, DID, admission, and consent", async () => {
Talk-Leee/src\components\campaigns\live-calls-panel.test.tsx:130:test("a hangup request shows Ending without optimistically ending the row or allowing a duplicate", async () => {
Talk-Leee/src\components\campaigns\live-calls-panel.test.tsx:161:test("a failed or unconfirmed hangup exposes the provider error and allows retry", async () => {
Talk-Leee/src\components\campaigns\live-calls-panel.test.tsx:192:test("an HTTP unconfirmed response surfaces its structured provider detail", async () => {
Talk-Leee/src\components\campaigns\live-calls-panel.test.tsx:278:test("without download permission the recording object URL is revoked when playback stops", async () => {
Talk-Leee/src\components\campaigns\live-calls-panel.test.tsx:298:test("with download permission playback pausing keeps the loaded recording", async () => {
Talk-Leee/src\components\campaigns\live-calls-panel.test.tsx:313:test("polling a terminal call clears Ending and uses the server's final status", async () => {
Talk-Leee/src\components\campaigns\live-calls-panel.test.tsx:89:test("hangup responses require the confirmation-aware contract", () => {
Talk-Leee/src\components\campaigns\live-calls-panel.test.tsx:97:test("termination presentation remains pending until the call itself is terminal", () => {
Talk-Leee/src\components\campaigns\rejected-inbound-calls-panel.test.tsx:16:test("shows durable denials and after-hours calls without exposing private ANI", async () => {
Talk-Leee/src\components\campaigns\rejected-inbound-calls-panel.test.tsx:57:test("shows a healthy empty state", async () => {
Talk-Leee/src\components\connectors\connector-card.test.ts:124:test("ConnectorCard confirms and calls disconnect", async () => {
Talk-Leee/src\components\connectors\connector-card.test.ts:14:test("ConnectorCard enables only Connect when disconnected", () => {
Talk-Leee/src\components\connectors\connector-card.test.ts:30:test("ConnectorCard enables Disconnect when connected", () => {
Talk-Leee/src\components\connectors\connector-card.test.ts:46:test("ConnectorCard shows Expired status and allows reconnect", () => {
Talk-Leee/src\components\connectors\connector-card.test.ts:63:test("ConnectorCard exposes an unavailable server capability without a dead OAuth button", () => {
Talk-Leee/src\components\connectors\connector-card.test.ts:82:test("ConnectorCard calls authorize and shows loading state", async () => {
Talk-Leee/src\components\home\contact-section.test.tsx:13:  it("renders the contact form and info section", () => {
Talk-Leee/src\components\inbound\inbound-campaign-form.test.ts:108:test("only server-visible eligible campaigns and runtime-ready inbound trunks can be selected", () => {
Talk-Leee/src\components\inbound\inbound-campaign-form.test.ts:122:test("inbound-specific overrides start neutral and inherit the base campaign", () => {
Talk-Leee/src\components\inbound\inbound-campaign-form.test.ts:14:test("inbound form requires a verified DID, AI campaign and inbound trunk", () => {
Talk-Leee/src\components\inbound\inbound-campaign-form.test.ts:22:test("inbound form enforces recording disclosure and E.164 transfer destinations", () => {
Talk-Leee/src\components\inbound\inbound-campaign-form.test.ts:38:test("after-hours UI exposes only runtime-backed actions", () => {
Talk-Leee/src\components\inbound\inbound-campaign-form.test.ts:49:test("AI message intake fails closed without its pinned opening message", () => {
Talk-Leee/src\components\inbound\inbound-campaign-form.test.ts:58:test("saved transfer policies can only be disabled while runtime proof is incomplete", () => {
Talk-Leee/src\components\inbound\inbound-campaign-form.test.ts:69:test("after-hours transfer is accepted only in the server-approved proof window", () => {
Talk-Leee/src\components\inbound\inbound-campaign-form.test.ts:82:test("cached open transfer capability fails closed after a refresh error", () => {
Talk-Leee/src\components\inbound\inbound-campaign-form.test.ts:95:test("E.164 and duration validation match the server boundary contract", () => {
Talk-Leee/src\components\notifications\qualified-lead-alerts.test.ts:60:test("leads already recorded in localStorage do not toast again", async () => {
Talk-Leee/src\components\notifications\qualified-lead-alerts.test.ts:72:test("the first load with no history seeds silently instead of toasting the backlog", async () => {
Talk-Leee/src\components\notifications\qualified-lead-alerts.test.ts:84:test("stale unseen leads are absorbed silently, fresh ones toast", async () => {
Talk-Leee/src\components\recordings\recording-media-controls.test.tsx:133:test("legal hold keeps the row and disables further deletion", async () => {
Talk-Leee/src\components\recordings\recording-media-controls.test.tsx:62:test("playback is lazy and download uses a separate request", async () => {
Talk-Leee/src\components\recordings\recording-media-controls.test.tsx:88:test("recording controls fail closed for each missing permission", () => {
Talk-Leee/src\components\recordings\recording-media-controls.test.tsx:96:test("delete retries retain one key and lock the audited reason", async () => {
Talk-Leee/src\components\states\page-states.test.ts:13:test("ErrorState renders message and supports retry", async () => {
Talk-Leee/src\components\states\page-states.test.ts:35:test("ErrorState renders support action when provided", () => {
Talk-Leee/src\components\states\page-states.test.ts:49:test("EmptyState renders CTA when provided", async () => {
Talk-Leee/src\components\states\page-states.test.ts:68:test("EmptyState supports primary and secondary actions", async () => {
Talk-Leee/src\components\states\page-states.test.ts:92:test("LoadingSkeleton renders requested number of lines", () => {
Talk-Leee/src\components\states\page-states.test.ts:98:test("LoadingSkeleton list variant renders rows", () => {
Talk-Leee/src\components\ui\confirm-dialog.test.ts:139:test("ConfirmDialog clears the inline error when the parent closes it directly", async () => {
Talk-Leee/src\components\ui\confirm-dialog.test.ts:159:test("ConfirmDialog clears the pending spinner when the parent closes it directly", async () => {
Talk-Leee/src\components\ui\confirm-dialog.test.ts:179:test("ConfirmDialog intent=cancel uses default copy", () => {
Talk-Leee/src\components\ui\confirm-dialog.test.ts:186:test("ConfirmDialog intent=delete uses default copy", () => {
Talk-Leee/src\components\ui\confirm-dialog.test.ts:35:test("ConfirmDialog focuses Cancel and traps tab navigation", async () => {
Talk-Leee/src\components\ui\confirm-dialog.test.ts:53:test("ConfirmDialog calls onConfirm and closes on success", async () => {
Talk-Leee/src\components\ui\confirm-dialog.test.ts:75:test("ConfirmDialog closes on Escape", async () => {
Talk-Leee/src\components\ui\confirm-dialog.test.ts:85:test("ConfirmDialog shows error when confirm fails and stays open", async () => {
Talk-Leee/src\components\ui\info-tip.test.tsx:104:test("tapping the trigger again closes the tip", async () => {
Talk-Leee/src\components\ui\info-tip.test.tsx:115:test("a tap outside dismisses a pinned tip", async () => {
Talk-Leee/src\components\ui\info-tip.test.tsx:136:test("the tip is reachable by keyboard — Tab focuses the trigger, Enter opens it", async () => {
Talk-Leee/src\components\ui\info-tip.test.tsx:147:test("Space opens the tip too, because the trigger is a button", async () => {
Talk-Leee/src\components\ui\info-tip.test.tsx:157:test("Escape dismisses a tip opened from the keyboard", async () => {
Talk-Leee/src\components\ui\info-tip.test.tsx:174:test("an open tip is announced: the trigger describes itself with the tip content", async () => {
Talk-Leee/src\components\ui\info-tip.test.tsx:195:test("the trigger reflects its open state for styling and for assistive tech", async () => {
Talk-Leee/src\components\ui\info-tip.test.tsx:206:test("optional Learn more renders as a real link to the given href", async () => {
Talk-Leee/src\components\ui\info-tip.test.tsx:226:test("no Learn more link when no href is given", async () => {
Talk-Leee/src\components\ui\info-tip.test.tsx:236:test("rich content is rendered, not stringified", async () => {
Talk-Leee/src\components\ui\info-tip.test.tsx:251:test("the panel carries the viewport width cap", async () => {
Talk-Leee/src\components\ui\info-tip.test.tsx:270:test("LabelWithInfo shows the label and derives an accessible name from it", () => {
Talk-Leee/src\components\ui\info-tip.test.tsx:278:test("LabelWithInfo opens the same tip its label describes", async () => {
Talk-Leee/src\components\ui\info-tip.test.tsx:67:test("the trigger is a real button carrying the label as its accessible name", () => {
Talk-Leee/src\components\ui\info-tip.test.tsx:81:test("each trigger gets its own accessible name rather than a shared 'more info'", () => {
Talk-Leee/src\components\ui\info-tip.test.tsx:95:test("a tap opens the tip and renders its content", async () => {
Talk-Leee/src\components\ui\input.test.tsx:11:test("Input updates value", async () => {
Talk-Leee/src\components\ui\status-pill.test.ts:21:    test(`StatusPill renders ${c.state} with tooltip`, async () => {
Talk-Leee/src\lib\admin-access.test.ts:108:test("getAdminUiCapabilities handles null user", () => {
Talk-Leee/src\lib\admin-access.test.ts:119:test("getAdminUiCapabilities ignores whitespace-only IDs", () => {
Talk-Leee/src\lib\admin-access.test.ts:130:test("roleLabel returns correct labels for all roles", () => {
Talk-Leee/src\lib\admin-access.test.ts:14:test("isPartnerAdminRole recognizes partner_admin role", () => {
Talk-Leee/src\lib\admin-access.test.ts:23:test("isTenantScopedRole recognizes tenant-scoped roles", () => {
Talk-Leee/src\lib\admin-access.test.ts:33:test("getAdminUiCapabilities grants full access to platform_admin", () => {
Talk-Leee/src\lib\admin-access.test.ts:48:test("getAdminUiCapabilities grants full access to admin role", () => {
Talk-Leee/src\lib\admin-access.test.ts:5:test("isPlatformAdminRole recognizes platform_admin and admin roles", () => {
Talk-Leee/src\lib\admin-access.test.ts:63:test("getAdminUiCapabilities grants scoped access to partner_admin", () => {
Talk-Leee/src\lib\admin-access.test.ts:78:test("getAdminUiCapabilities restricts access for tenant_admin", () => {
Talk-Leee/src\lib\admin-access.test.ts:93:test("getAdminUiCapabilities restricts access for user role", () => {
Talk-Leee/src\lib\audio-recording.test.ts:100:test("filenames carry an extension that agrees with the container", () => {
Talk-Leee/src\lib\audio-recording.test.ts:109:test("each microphone failure gets its own remedy", () => {
Talk-Leee/src\lib\audio-recording.test.ts:118:test("a named error always beats environment sniffing", () => {
Talk-Leee/src\lib\audio-recording.test.ts:130:test("an unrecognised failure still yields something actionable", () => {
Talk-Leee/src\lib\audio-recording.test.ts:142:test("every microphone message tells the user what to do next", () => {
Talk-Leee/src\lib\audio-recording.test.ts:152:test("the duration cap is 30s and matches the API's Form(le=30)", () => {
Talk-Leee/src\lib\audio-recording.test.ts:159:test("the minimum length rejects a mis-click but not a short sentence", () => {
Talk-Leee/src\lib\audio-recording.test.ts:163:test("the mirrored accept-list matches the backend's _MIME_EXTENSIONS", () => {
Talk-Leee/src\lib\audio-recording.test.ts:178:test("durations render as m:ss", () => {
Talk-Leee/src\lib\audio-recording.test.ts:33:test("Chrome and Firefox get Opus in WebM", () => {
Talk-Leee/src\lib\audio-recording.test.ts:38:test("Safari below 18.4 gets MP4, never WebM", () => {
Talk-Leee/src\lib\audio-recording.test.ts:44:test("Safari 18.4+ can do WebM and is given the better container", () => {
Talk-Leee/src\lib\audio-recording.test.ts:49:test("a browser that supports nothing we asked about yields no preference", () => {
Talk-Leee/src\lib\audio-recording.test.ts:58:test("a throwing isTypeSupported does not abort the search", () => {
Talk-Leee/src\lib\audio-recording.test.ts:66:test("every candidate we would request is one the backend accepts", () => {
Talk-Leee/src\lib\audio-recording.test.ts:79:test("the recorder's own mimeType wins over what we requested", () => {
Talk-Leee/src\lib\audio-recording.test.ts:88:test("codec parameters are stripped, matching the backend's normalisation", () => {
Talk-Leee/src\lib\audio-recording.test.ts:94:test("a blank recorder mimeType falls back to the request, then to WebM", () => {
Talk-Leee/src\lib\backend-api.admin.test.ts:4:test("backendApi.admin.auditLogs.list encodes filters and pagination", async () => {
Talk-Leee/src\lib\backend-api.admin.test.ts:58:test("backendApi.admin.tenants.suspend posts to suspension endpoint", async () => {
Talk-Leee/src\lib\backend-api.assistant.test.ts:138:test("backendApi.assistantRuns.retry posts to retry endpoint", async () => {
Talk-Leee/src\lib\backend-api.assistant.test.ts:25:test("backendApi.assistantRuns.list supports filtering and sorting query params", async () => {
Talk-Leee/src\lib\backend-api.assistant.test.ts:4:test("backendApi.assistantActions.list calls assistant actions list endpoint", async () => {
Talk-Leee/src\lib\backend-api.assistant.test.ts:61:test("backendApi.assistant.plan posts payload to plan endpoint", async () => {
Talk-Leee/src\lib\backend-api.assistant.test.ts:92:test("backendApi.assistant.execute posts payload to execute endpoint and parses run", async () => {
Talk-Leee/src\lib\backend-api.calendar-events.test.ts:110:test("backendApi.calendarEvents.update patches event by id", async () => {
Talk-Leee/src\lib\backend-api.calendar-events.test.ts:159:test("backendApi.calendarEvents.cancel deletes event by id", async () => {
Talk-Leee/src\lib\backend-api.calendar-events.test.ts:25:test("backendApi.calendarEvents.list supports pagination query params", async () => {
Talk-Leee/src\lib\backend-api.calendar-events.test.ts:4:test("backendApi.calendarEvents.list calls calendar events list endpoint", async () => {
Talk-Leee/src\lib\backend-api.calendar-events.test.ts:51:test("backendApi.calendarEvents.create posts event payload to create endpoint", async () => {
Talk-Leee/src\lib\backend-api.connectors.test.ts:31:test("backendApi.connectors.disconnect calls disconnect endpoint via POST", async () => {
Talk-Leee/src\lib\backend-api.connectors.test.ts:4:test("backendApi.connectors.authorize calls authorize endpoint with redirect_uri", async () => {
Talk-Leee/src\lib\backend-api.connectors.test.ts:51:test("backendApi.connectors.status parses connector statuses", async () => {
Talk-Leee/src\lib\backend-api.email.test.ts:4:test("backendApi.email.templates.list parses templates and maps fields", async () => {
Talk-Leee/src\lib\backend-api.email.test.ts:45:test("backendApi.email.send posts template_id and returns normalized messageId", async () => {
Talk-Leee/src\lib\backend-api.voice-calls.test.ts:4:test("backendApi.voiceCalls.guard posts guarded call payload and parses allow response", async () => {
Talk-Leee/src\lib\backend-api.voice-calls.test.ts:55:test("backendApi.voiceCalls.start posts start payload and parses active session response", async () => {
Talk-Leee/src\lib\billing-api.test.ts:107:test("a 403 for one invoice by id is still a load failure", async () => {
Talk-Leee/src\lib\billing-api.test.ts:55:test("a 403 on the usage endpoint surfaces as isError, not as empty data", async () => {
Talk-Leee/src\lib\billing-api.test.ts:65:test("a 500 on the invoices endpoint surfaces as isError, not as an empty list", async () => {
Talk-Leee/src\lib\billing-api.test.ts:75:test("a network failure on the invoices endpoint surfaces as isError", async () => {
Talk-Leee/src\lib\billing-api.test.ts:87:test("a successful empty invoice list stays an ordinary empty result", async () => {
Talk-Leee/src\lib\billing-api.test.ts:97:test("a 404 for one invoice by id is 'not found', not a load failure", async () => {
Talk-Leee/src\lib\campaign-performance.test.ts:102:test("groupEventTime buckets dates into groups", () => {
Talk-Leee/src\lib\campaign-performance.test.ts:33:test("normalizeCampaignStatus maps known statuses", () => {
Talk-Leee/src\lib\campaign-performance.test.ts:42:test("campaignProgressPct clamps and handles zero leads", () => {
Talk-Leee/src\lib\campaign-performance.test.ts:48:test("campaignSuccessRatePct uses completed / (completed+failed)", () => {
Talk-Leee/src\lib\campaign-performance.test.ts:54:test("applyCampaignFilters supports status, success range, and query", () => {
Talk-Leee/src\lib\campaign-performance.test.ts:71:test("applyCampaignSort supports multi-column and stable tiebreak", () => {
Talk-Leee/src\lib\campaign-performance.test.ts:85:test("paginate returns correct range and pageCount", () => {
Talk-Leee/src\lib\campaign-performance.test.ts:94:test("parseCommandInput reads prefixes and query", () => {
Talk-Leee/src\lib\connectors-utils.test.ts:11:test("connectorCardActionFromStatus maps all states", () => {
Talk-Leee/src\lib\connectors-utils.test.ts:19:test("connectorCardActionLabel renders user-facing labels", () => {
Talk-Leee/src\lib\connectors-utils.test.ts:25:test("extractAuthorizationUrl accepts multiple response shapes", () => {
Talk-Leee/src\lib\connectors-utils.test.ts:32:test("parseConnectorsCallback handles success and failure", () => {
Talk-Leee/src\lib\connectors-utils.test.ts:44:test("parseConnectorsCallback uses default providerType when missing from query", () => {
Talk-Leee/src\lib\connectors-utils.test.ts:50:test("formatLastSync is stable for empty and invalid values", () => {
Talk-Leee/src\lib\contact-form.test.ts:6:test("manual contact payload carries every operational contact field", () => {
Talk-Leee/src\lib\dashboard-api.inbound.test.ts:4:test("inbound call list and detail preserve direction and caller/DID parties", async () => {
Talk-Leee/src\lib\dashboard-layout-removal.test.ts:7:test("dashboard layout removes theme toggle and removes global sidebar toggle", () => {
Talk-Leee/src\lib\email-audit.test.ts:29:test("createAttempt records pending entry and persists to storage", () => {
Talk-Leee/src\lib\email-audit.test.ts:56:test("markSuccess and markFailed update the audit entry", () => {
Talk-Leee/src\lib\email-audit.test.ts:79:test("exportHistoryJson includes exportedAt and items", () => {
Talk-Leee/src\lib\email-audit.test.ts:97:test("clearAll removes items and clears storage", () => {
Talk-Leee/src\lib\email-utils.test.ts:10:test("isValidEmail accepts simple valid addresses and rejects invalid ones", () => {
Talk-Leee/src\lib\email-utils.test.ts:18:test("normalizeEmailList trims and de-duplicates case-insensitively", () => {
Talk-Leee/src\lib\email-utils.test.ts:23:test("buildResponsiveHtmlDocument wraps fragments and injects viewport meta", () => {
Talk-Leee/src\lib\email-utils.test.ts:31:test("buildResponsiveHtmlDocument injects viewport into existing head", () => {
Talk-Leee/src\lib\email-utils.test.ts:36:test("buildResponsiveHtmlDocument does not duplicate viewport meta", () => {
Talk-Leee/src\lib\email-utils.test.ts:5:test("splitEmailInput splits by whitespace, commas, semicolons, and newlines", () => {
Talk-Leee/src\lib\env.test.ts:12:test("publicAppConfig exposes only public configuration metadata", () => {
Talk-Leee/src\lib\env.test.ts:5:test("isPublicEnvKey only allows NEXT_PUBLIC client-safe keys", () => {
Talk-Leee/src\lib\extended-api.recordings.test.ts:30:test("recording delete sends the reason and caller-owned idempotency key", async () => {
Talk-Leee/src\lib\extended-api.recordings.test.ts:59:test("recording delete preserves the backend legal-hold error code", async () => {
Talk-Leee/src\lib\extended-api.recordings.test.ts:7:test("recording playback and download use distinct authorized endpoints", async () => {
Talk-Leee/src\lib\extended-api.rewards.test.ts:6:test("review reward display is sourced from the verified ledger balance endpoint", async () => {
Talk-Leee/src\lib\http-client.session-expired.test.ts:134:test("non-401 errors do NOT fire the session-expired handler", async () => {
Talk-Leee/src\lib\http-client.session-expired.test.ts:164:test("a thrown handler does not derail the request rejection", async () => {
Talk-Leee/src\lib\http-client.session-expired.test.ts:18:test("401 fires the registered session-expired handler and clears the token", async () => {
Talk-Leee/src\lib\http-client.session-expired.test.ts:53:test("multiple parallel 401s fire the handler only ONCE (idempotent)", async () => {
Talk-Leee/src\lib\http-client.session-expired.test.ts:95:test("resetSessionExpiredLatch re-arms the handler after a successful login", async () => {
Talk-Leee/src\lib\http-client.test.ts:123:test("requestRaw refreshes on 401 then retries and returns the raw binary Response", async () => {
Talk-Leee/src\lib\http-client.test.ts:156:test("requestRaw surfaces the backend error detail on a non-OK response", async () => {
Talk-Leee/src\lib\http-client.test.ts:181:test("http client preserves structured FastAPI detail codes", async () => {
Talk-Leee/src\lib\http-client.test.ts:211:test("requestRaw preserves canonical error codes for binary endpoints", async () => {
Talk-Leee/src\lib\http-client.test.ts:26:test("http client refreshes on 401 then retries the original request", async () => {
Talk-Leee/src\lib\http-client.test.ts:5:test("http client injects Authorization header when token present", async () => {
Talk-Leee/src\lib\http-client.test.ts:59:test("http client surfaces 401 when refresh also fails", async () => {
Talk-Leee/src\lib\http-client.test.ts:93:test("http client maps 429 response to rate_limited with retryAfterMs", async () => {
Talk-Leee/src\lib\inbound-api.test.ts:110:test("transfer capability request is scoped to the edited inbound config", async () => {
Talk-Leee/src\lib\inbound-api.test.ts:134:test("archived campaign inventory is requested only for the archived view", async () => {
Talk-Leee/src\lib\inbound-api.test.ts:154:test("create sends only the confirmed inbound contract and an idempotency key", async () => {
Talk-Leee/src\lib\inbound-api.test.ts:186:test("an ambiguous retry reuses its idempotency key and a later operation gets a fresh key", async () => {
Talk-Leee/src\lib\inbound-api.test.ts:209:test("an expired ambiguous retry is blocked before the server claim can roll over", async () => {
Talk-Leee/src\lib\inbound-api.test.ts:231:test("update uses PUT and includes the stale-edit token", async () => {
Talk-Leee/src\lib\inbound-api.test.ts:250:test("assignment uses the explicit audited endpoint and optimistic version", async () => {
Talk-Leee/src\lib\inbound-api.test.ts:280:test("archive uses the confirmed lifecycle endpoint and optimistic version", async () => {
Talk-Leee/src\lib\inbound-api.test.ts:54:test("inbound parser accepts the production envelope and masks the DID", () => {
Talk-Leee/src\lib\inbound-api.test.ts:66:test("verified phone inventory excludes assigned and unverified numbers", () => {
Talk-Leee/src\lib\inbound-api.test.ts:78:test("readiness fails closed without an explicit server ready flag", () => {
Talk-Leee/src\lib\inbound-api.test.ts:86:test("transfer capability parsing requires all explicit server gates", () => {
Talk-Leee/src\lib\inbound-permissions.test.ts:36:test("capabilities fail closed when server permission discovery is unavailable", () => {
Talk-Leee/src\lib\inbound-permissions.test.ts:6:test("effective permissions keep inbound capabilities distinct", () => {
Talk-Leee/src\lib\lead-details-api.test.ts:6:test("campaign lead-field reads and writes use the campaign-scoped contract", async () => {
Talk-Leee/src\lib\lead-outcome.test.ts:17:test("captured-detail-adjacent negative verdicts never produce an interested badge", () => {
Talk-Leee/src\lib\lead-outcome.test.ts:23:test("missing and telephony-only outcomes remain unknown", () => {
Talk-Leee/src\lib\lead-outcome.test.ts:6:test("positive lead verdicts produce the interested state", () => {
Talk-Leee/src\lib\media-permissions.test.ts:27:test("broader call permissions do not grant recording access", () => {
Talk-Leee/src\lib\media-permissions.test.ts:38:test("platform admin is an explicit bypass and missing discovery fails closed", () => {
Talk-Leee/src\lib\media-permissions.test.ts:6:test("recording permissions stay independent", () => {
Talk-Leee/src\lib\meetings-utils.test.ts:13:test("splitAndSortMeetings splits by start time and sorts correctly", () => {
Talk-Leee/src\lib\meetings-utils.test.ts:27:test("meetingLeadLabel prefers explicit leadName, else participant name/email", () => {
Talk-Leee/src\lib\meetings-utils.test.ts:49:test("meetingStatusLabel normalizes status values", () => {
Talk-Leee/src\lib\meetings-utils.test.ts:61:test("meetingParticipantSummary returns first two and extra count", () => {
Talk-Leee/src\lib\meetings-utils.test.ts:78:test("sortMeetings can sort by title and startTime", () => {
Talk-Leee/src\lib\meetings-utils.test.ts:99:test("sanitizeMeetingNotesHtml removes scripts and event handlers", () => {
Talk-Leee/src\lib\notifications.test.ts:28:test("create adds notification and toast by default", () => {
Talk-Leee/src\lib\notifications.test.ts:46:test("markRead and markAllRead set readAt", () => {
Talk-Leee/src\lib\notifications.test.ts:69:test("dismissToast removes toast only", () => {
Talk-Leee/src\lib\notifications.test.ts:88:test("clearAll removes history and toasts", () => {
Talk-Leee/src\lib\queries\inbound-queries.test.ts:9:test("campaign cache commits never rewrite capability objects", () => {
Talk-Leee/src\lib\reminders-utils.test.ts:27:test("sanitizeFailureReason strips dangerous characters and truncates", () => {
Talk-Leee/src\lib\reminders-utils.test.ts:35:test("groupReminders groups by meeting id then contact id", () => {
Talk-Leee/src\lib\reminders-utils.test.ts:47:test("sortReminders sorts by scheduledAt asc/desc", () => {
Talk-Leee/src\lib\reminders-utils.test.ts:58:test("retryGuidance returns actionable strings for failed reminders", () => {
Talk-Leee/src\lib\review-permissions.test.ts:17:test("the readonly role's real permission set cannot write a review", () => {
Talk-Leee/src\lib\review-permissions.test.ts:34:test("calls:create grants write", () => {
Talk-Leee/src\lib\review-permissions.test.ts:42:test("platform:admin grants both", () => {
Talk-Leee/src\lib\review-permissions.test.ts:50:test("permissions are matched case- and whitespace-insensitively", () => {
Talk-Leee/src\lib\review-permissions.test.ts:54:test("a missing permission set fails closed and says so", () => {
Talk-Leee/src\lib\review-permissions.test.ts:64:test("authorization and validation refusals are not retryable", () => {
Talk-Leee/src\lib\review-permissions.test.ts:71:test("transport, rate-limit and server faults are retryable", () => {
Talk-Leee/src\lib\review-permissions.test.ts:9:test("calls:read alone can read reviews but not write one", () => {
Talk-Leee/src\lib\routes.test.ts:14:test("new routes use DashboardLayout", () => {
Talk-Leee/src\lib\routes.test.ts:21:    if (/<DashboardLayout/.test(assistantReminders)) {
Talk-Leee/src\lib\sidebar.test.ts:49:test("hydrate reads collapsed state from localStorage", () => {
Talk-Leee/src\lib\sidebar.test.ts:66:test("setCollapsed persists payload", () => {
Talk-Leee/src\lib\sidebar.test.ts:83:test("storage event updates snapshot in current tab", () => {
Talk-Leee/src\lib\structural-auth-isolation.test.ts:77:        if (!/\.(ts|tsx)$/.test(entry)) continue;
Talk-Leee/src\lib\structural-auth-isolation.test.ts:84:test("only auth-context (and allowlisted bridges) read the canonical token", () => {
Talk-Leee/src\lib\structural-auth-isolation.test.ts:91:            if (pattern.test(source)) {
Talk-Leee/src\lib\structural-auth-isolation.test.ts:99:        if (FORBIDDEN_DIRECT_KEY.test(source)) {
Talk-Leee/src\lib\structural-no-bare-fetch.test.ts:77:        if (!/\.(ts|tsx)$/.test(entry)) continue;
Talk-Leee/src\lib\structural-no-bare-fetch.test.ts:87:test("no bare fetch() to /api/v1 outside the shared HttpClient", () => {
Talk-Leee/src\lib\structural-no-bare-fetch.test.ts:93:        if (!IMPORTS_API_BASE_URL.test(source)) continue;
Talk-Leee/src\lib\structural-no-bare-fetch.test.ts:94:        if (!HAS_FETCH_CALL.test(source)) continue;
Talk-Leee/src\lib\structural-single-httpclient.test.ts:60:        if (!/\.(ts|tsx)$/.test(entry)) continue;
Talk-Leee/src\lib\structural-single-httpclient.test.ts:67:test("only api.ts (and the server-side route) call createHttpClient", () => {
Talk-Leee/src\lib\structural-single-httpclient.test.ts:73:        if (!CALL_PATTERN.test(source)) continue;
Talk-Leee/src\lib\structural-single-me-call.test.ts:65:        if (!/\.(ts|tsx)$/.test(entry)) continue;
Talk-Leee/src\lib\structural-single-me-call.test.ts:72:test("api.getMe() callers are limited to AuthContext (+ OAuth callback)", () => {
Talk-Leee/src\lib\structural-single-me-call.test.ts:78:        if (!CALL_PATTERN.test(source)) continue;
Talk-Leee/src\lib\theme-implementation.test.ts:10:test("Theme Implementation Verification", async (t) => {
Talk-Leee/src\lib\theme-implementation.test.ts:11:    await t.test("globals.css defines theme variables and transitions", () => {
Talk-Leee/src\lib\theme-implementation.test.ts:26:    await t.test("theme-provider.tsx implements localStorage and context", () => {
Talk-Leee/src\lib\theme-implementation.test.ts:43:    await t.test("navbar.tsx includes theme toggle", () => {
Talk-Leee/src\lib\theme-implementation.test.ts:54:    await t.test("sidebar.tsx inherits theme", () => {
Talk-Leee/src\lib\topup-api.test.ts:100:test("a top-up on an account that already bought some still registers", () => {
Talk-Leee/src\lib\topup-api.test.ts:104:test("no baseline means no claim", () => {
Talk-Leee/src\lib\topup-api.test.ts:110:test("prices render from minor units, not major", () => {
Talk-Leee/src\lib\topup-api.test.ts:116:test("a sub-penny per-minute rate keeps its precision", () => {
Talk-Leee/src\lib\topup-api.test.ts:120:test("the currency comes from the package, not a hardcoded default", () => {
Talk-Leee/src\lib\topup-api.test.ts:124:test("a missing currency falls back rather than throwing", () => {
Talk-Leee/src\lib\topup-api.test.ts:130:test("every order status the backend defines has a label and a tone", () => {
Talk-Leee/src\lib\topup-api.test.ts:147:test("a pending order says it is unpaid, not that it failed", () => {
Talk-Leee/src\lib\topup-api.test.ts:36:test("an unlimited plan is never offered a top-up", () => {
Talk-Leee/src\lib\topup-api.test.ts:43:test("a metered plan is offered a top-up", () => {
Talk-Leee/src\lib\topup-api.test.ts:47:test("nothing is offered before the balance is known", () => {
Talk-Leee/src\lib\topup-api.test.ts:55:test("under 15% remaining reads as low", () => {
Talk-Leee/src\lib\topup-api.test.ts:62:test("exactly 15% remaining is not low yet", () => {
Talk-Leee/src\lib\topup-api.test.ts:69:test("an unlimited plan is never low", () => {
Talk-Leee/src\lib\topup-api.test.ts:76:test("a zero allocation does not divide by zero", () => {
Talk-Leee/src\lib\topup-api.test.ts:82:test("coming back from the payment page is not proof on its own", () => {
Talk-Leee/src\lib\topup-api.test.ts:89:test("the ledger total moving up is proof", () => {
Talk-Leee/src\lib\topup-api.test.ts:93:test("a refund landing in the same window is not a successful top-up", () => {
Talk-Leee/src\proxy.trailing-slash.test.ts:102:test("api routes are never redirected", () => {
Talk-Leee/src\proxy.trailing-slash.test.ts:113:test("build assets and static files are never redirected", () => {
Talk-Leee/src\proxy.trailing-slash.test.ts:123:test("a dot in a non-final segment does not exempt the path", () => {
Talk-Leee/src\proxy.trailing-slash.test.ts:127:test("non-absolute input is ignored", () => {
Talk-Leee/src\proxy.trailing-slash.test.ts:84:test("every page route redirects to its trailing-slashed form", () => {
Talk-Leee/src\proxy.trailing-slash.test.ts:90:test("page route count matches the app router page count", () => {
Talk-Leee/src\proxy.trailing-slash.test.ts:95:test("already-canonical paths are not redirected again", () => {
Talk-Leee/src\server\api-security.test.ts:17:test("parseStripeSignatureHeader extracts timestamp and v1 signatures", () => {
Talk-Leee/src\server\api-security.test.ts:28:test("verifyStripeWebhookSignature accepts valid signature and rejects invalid", () => {
Talk-Leee/src\server\api-security.test.ts:6:test("sanitizeUnknown removes prototype pollution keys", () => {
Talk-Leee/src\server\auth-core.test.ts:155:test("sessions rotate on role/scope change and include usage/billing mapping (db)", async (t) => {
Talk-Leee/src\server\auth-core.test.ts:35:test("password strength validator rejects weak passwords", () => {
Talk-Leee/src\server\auth-core.test.ts:41:test("argon2 hashes and verifies passwords", async () => {
Talk-Leee/src\server\auth-core.test.ts:48:test("auth token is extracted from Authorization header", () => {
Talk-Leee/src\server\auth-core.test.ts:53:test("auth token is extracted from cookie header", () => {
Talk-Leee/src\server\auth-core.test.ts:58:test("session cookies are httpOnly and sameSite", () => {
Talk-Leee/src\server\auth-core.test.ts:61:    assert.ok(/talklee_auth_token=/.test(set));
Talk-Leee/src\server\auth-core.test.ts:62:    assert.ok(/HttpOnly/i.test(set));
Talk-Leee/src\server\auth-core.test.ts:63:    assert.ok(/SameSite=Lax/i.test(set));
Talk-Leee/src\server\auth-core.test.ts:64:    assert.ok(/Secure/i.test(set));
Talk-Leee/src\server\auth-core.test.ts:67:    assert.ok(/Max-Age=0/.test(cleared));
Talk-Leee/src\server\auth-core.test.ts:68:    assert.ok(/HttpOnly/i.test(cleared));
Talk-Leee/src\server\auth-core.test.ts:71:test("sessions enforce absolute expiry, idle timeout, binding, and rotation (db)", async (t) => {
Talk-Leee/src\server\mfa.test.ts:5:test("RFC 6238 SHA1 test vectors match", () => {
Talk-Leee/src\server\passkeys.test.ts:22:test("webauthn config falls back to request host and origin in non-production", () => {
Talk-Leee/src\server\passkeys.test.ts:41:test("webauthn config requires rp id and allowed origins in production", () => {
Talk-Leee/src\server\passkeys.test.ts:57:test("webauthn config uses explicit allowlist when configured", () => {
Talk-Leee/src\server\rbac.test.ts:15:test("platform_admin can access any partner and tenant", () => {
Talk-Leee/src\server\rbac.test.ts:21:test("partner_admin is restricted to their partner", () => {
Talk-Leee/src\server\rbac.test.ts:29:test("tenant users cannot access other tenants or partners", () => {
Talk-Leee/src\server\rbac.test.ts:39:test("permission checks always allow platform_admin", () => {
Talk-Leee/src\server\voice-security.test.ts:129:test("call_guard enforces dedicated call rate limits per tenant", async () => {
Talk-Leee/src\server\voice-security.test.ts:15:test("call_guard allows valid calls and lifecycle updates counters", async () => {
Talk-Leee/src\server\voice-security.test.ts:163:test("call_guard rejects disallowed features and then blocks repeated failed attempts", async () => {
Talk-Leee/src\server\voice-security.test.ts:201:test("call_guard rejects callers outside the tenant scope", async () => {
Talk-Leee/src\server\voice-security.test.ts:59:test("startGuardedVoiceCallSessionWithService makes call_guard non-bypassable for start flow", async () => {
Talk-Leee/src\server\voice-security.test.ts:89:test("call_guard enforces concurrency limits and supports overage reservations", async () => {
Talk-Leee/test\minutes-usage-layout.test.mjs:10:test("computeFittedFontPx stays within bounds and fits container", () => {
Talk-Leee/test\minutes-usage-layout.test.mjs:29:test("computeFittedFontPx decreases with longer text", () => {
Talk-Leee/test\minutes-usage-layout.test.mjs:50:test("computeMinutesUsageFontPx fits both halves across value ranges", () => {
Talk-Leee/tests\donut-label-layout.test.mjs:26:test("scales font size with segment area within bounds", () => {
Talk-Leee/tests\donut-label-layout.test.mjs:5:test("renders ellipsis fallback for extremely small segment", () => {
Talk-Leee/tests\donut-label-layout.test.mjs:58:test("wraps to two lines and truncates with ellipsis when space is limited", () => {
Talk-Leee/tests\donut-label-layout.test.mjs:80:test("produces stable snapshot for a typical segment", () => {
````

## Appendix E — Exact goals.md snapshot at the release commit

Every line is numbered so later reports can cite a stable report line even if goals.md changes.

````text
G0001 | # Talk-lee Product Delivery Checklist
G0002 | 
G0003 | **Delivery target:** September 10, 2026
G0004 | **Planning start:** August 22, 2026
G0005 | **Team:** Two developers, with agent testing and prompt tuning running in parallel
G0006 | **Release rule:** Freeze the release candidate on September 8; September 9 is validation and rollback rehearsal; September 10 is controlled release.
G0007 | 
G0008 | > **Execution audit — 2026-08-31:** 213 of 532 literal checklist items are
G0009 | > evidence-backed; 319 remain open. Inbound MVP is 29/31, prompt backend is
G0010 | > 10/10, interested-lead capture is 22/24, and expanded contacts is 38/40.
G0011 | > Unchecked items must not be treated as implicit passes: they include live
G0012 | > carrier/payment/CRM tests, the 200-tenant and soak evidence, owner approvals,
G0013 | > historical team-process milestones and remaining product work. The proof,
G0014 | > premortem and exact production gates are in
G0015 | > `docs/sessions/reports/report14.md`.
G0016 | 
G0017 | ## 1. Ownership
G0018 | 
G0019 | ### Developer A — Backend, Voice and Integrations
G0020 | 
G0021 | - Campaign, call and review APIs
G0022 | - Feedback/reward ledger and abuse controls
G0023 | - Inbound call routing and session creation
G0024 | - Generic lead-generation prompt rendering
G0025 | - Lead-information capture and structured call outcomes
G0026 | - Contact schema and campaign-variable mapping
G0027 | - Billing top-up APIs and payment-provider webhook handling
G0028 | - Salesforce OAuth and MVP synchronization
G0029 | - Security-page backend endpoints
G0030 | - Database migrations, audit logs, tests and observability
G0031 | 
G0032 | ### Developer B — Frontend and Product Experience
G0033 | 
G0034 | - Conversation review panel
G0035 | - Reward display and review history
G0036 | - Security section moved into the main sidebar
G0037 | - Inbound campaign creation and management pages
G0038 | - Interested-lead details panel/form
G0039 | - Information tooltips and popovers
G0040 | - Token/creativity explanations in AI Options
G0041 | - AI Summary explanation popovers
G0042 | - Billing top-up interface
G0043 | - Salesforce connection interface
G0044 | - Expanded contacts form, table, import template and validation
G0045 | - Frontend tests, loading states, empty states and error handling
G0046 | 
G0047 | ### Parallel QA/Prompt-Tuning Track
G0048 | 
G0049 | - Freeze one prompt version for every test batch
G0050 | - Run controlled lead-generation calls
G0051 | - Review recordings and transcripts
G0052 | - Label conversation problems
G0053 | - Score the agent against an agreed rubric
G0054 | - Change only one important prompt behavior per experiment
G0055 | - Record prompt version, runtime configuration and test result
G0056 | 
G0057 | ---
G0058 | 
G0059 | ## 2. Priority and Scope
G0060 | 
G0061 | ### P0 — Must be ready by September 10
G0062 | 
G0063 | - [x] Generic lead-generation prompt connected to live campaign runtime
G0064 | - [x] Prompt version and hash visible in call logs
G0065 | - [x] Expanded contact fields available to the agent
G0066 | - [x] Structured interested-lead information capture
G0067 | - [x] Per-conversation review and feedback storage
G0068 | - [x] Security moved from Settings to the main sidebar
G0069 | - [ ] Inbound campaign MVP
G0070 | - [x] AI Options and AI Summary information tooltips
G0071 | - [x] Billing minute top-up MVP
G0072 | - [ ] Client-management and multi-tenant validation with 200 test clients
G0073 | - [ ] End-to-end test and controlled release
G0074 | 
G0075 | ### P1 — Deliver as MVP if P0 remains healthy
G0076 | 
G0077 | - [ ] Review reward points/credits
G0078 | - [ ] Salesforce OAuth connection
G0079 | - [ ] One-way Talk-lee-to-Salesforce lead/contact synchronization
G0080 | - [x] Review analytics dashboard
G0081 | 
G0082 | ### P2 — Do not block September 10
G0083 | 
G0084 | - [ ] Automatic AI fine-tuning from feedback
G0085 | - [ ] Cash rewards or withdrawable rewards
G0086 | - [ ] Full bidirectional Salesforce synchronization
G0087 | - [ ] Salesforce opportunity, task and campaign synchronization
G0088 | - [ ] Advanced inbound IVR and multi-level call flows
G0089 | - [ ] Fully automated prompt deployment based only on user reviews
G0090 | 
G0091 | ---
G0092 | 
G0093 | ## 3. Conversation Review and Reward System
G0094 | 
G0095 | ### Backend
G0096 | 
G0097 | - [x] Create a `conversation_reviews` table.
G0098 | - [x] Store `review_id`, `tenant_id`, `user_id`, `call_id`, `campaign_id`, `rating`, `review_tags`, `comment`, `created_at` and `updated_at`.
G0099 | - [x] Add structured tags:
G0100 |   - [x] Agent did not understand
G0101 |   - [x] Agent interrupted caller
G0102 |   - [x] Agent did not answer the question
G0103 |   - [x] Response was too long
G0104 |   - [x] Response was too slow
G0105 |   - [x] Agent repeated itself
G0106 |   - [x] Wrong qualification question
G0107 |   - [x] Wrong call outcome
G0108 |   - [x] Poor objection handling
G0109 |   - [x] Incorrect information
G0110 |   - [x] Good conversation
G0111 | - [x] Allow one active review per user per call; edits update the same review.
G0112 | - [x] Verify the reviewer belongs to the call's tenant.
G0113 | - [x] Prevent users from reviewing calls they cannot access.
G0114 | - [ ] Record prompt version, model, campaign and call trace with the review.
G0115 | - [x] Create a review-reward ledger rather than directly changing balances.
G0116 | - [x] Make rewards idempotent so repeat submissions cannot create duplicate credits.
G0117 | - [ ] Add daily reward limits and suspicious-activity monitoring.
G0118 | - [x] Do not reward an empty review unless a simple rating is intentionally eligible.
G0119 | - [ ] Add admin controls to enable, disable and configure reward amounts.
G0120 | - [ ] Add API tests for tenant isolation, duplicate rewards and unauthorized calls.
G0121 | 
G0122 | > ### ⚠️ TICK AUDIT, 2026-08-24 — four ticks were wrong and have been reversed
G0123 | >
G0124 | > Re-verified every tick against the code rather than against memory. Four did
G0125 | > not survive. They are listed here rather than quietly flipped, because a
G0126 | > checklist nobody can trust is worse than no checklist.
G0127 | >
G0128 | > - **"Record prompt version, model, campaign and call trace"** — reversed.
G0129 | >   `prompt_template`, `prompt_version`, `prompt_hash` and `campaign_id` ARE
G0130 | >   written. **`llm_model` is not.** The column exists (migration 0015 line 123)
G0131 | >   but the `INSERT` never populates it, and `calls` has no model column to
G0132 | >   source it from. It cannot be honestly completed by reading the tenant's
G0133 | >   *current* model at review time: the model may have changed since the call,
G0134 | >   so that would attribute an old call to a model it never ran on. Completing
G0135 | >   this needs `calls.llm_model` captured at call time — a migration plus a
G0136 | >   write-path change.
G0137 | > - **"Add API tests for tenant isolation, duplicate rewards and unauthorized
G0138 | >   calls"** — reversed. `test_conversation_reviews.py` has 24 tests and they
G0139 | >   are all pure-function validation: tag vocabulary, rating bounds, comment
G0140 | >   handling, reward eligibility. **None of the three things this line names is
G0141 | >   tested.** True API-level tests are also blocked by the httpx/starlette
G0142 | >   TestClient mismatch (#79).
G0143 | > - **"Display confidence or 'needs review'"** (§8) — completed 2026-09-01.
G0144 | >   Actionable AI classifications now show an explicit **Needs review** signal.
G0145 | >   The UI deliberately does not invent a numerical confidence value because
G0146 | >   the summary contract does not provide one. Component tests cover both the
G0147 | >   actionable and ordinary-summary states.
G0148 | > - **"Popovers do not cover save buttons or critical fields"** (§8) — reversed.
G0149 | >   Radix collision handling makes this *likely*, but it was never checked on a
G0150 | >   real narrow screen, and "likely" is not "done".
G0151 | >
G0152 | > **What was fixed rather than reversed:** "loading, retry and permission-error
G0153 | > states" was ticked with no retry anywhere in the panel. A **Try again** button
G0154 | > now sits in the error state — the form still holds every word, so the only
G0155 | > thing missing was a way to send them again. That tick now stands.
G0156 | >
G0157 | > **Status of the two unticked items (2026-08-23).** Both are partly built and
G0158 | > deliberately not ticked:
G0159 | >
G0160 | > - *Daily limits and suspicious-activity monitoring* — the daily cap is
G0161 | >   enforced (`reward_daily_cap()`, per user per UTC day, logged as
G0162 | >   `review_reward_daily_cap_reached`). There is no separate monitoring or
G0163 | >   alerting beyond that log line.
G0164 | > - *Admin controls for reward amounts* — configurable, but through environment
G0165 | >   variables (`REVIEW_REWARDS_ENABLED`, `REVIEW_REWARD_POINTS`,
G0166 | >   `REVIEW_REWARD_DAILY_MAX`), not a UI. Rewards are OFF by default. Both are
G0167 | >   P1 concerns and neither blocks review capture, which works with rewards
G0168 | >   disabled.
G0169 | 
G0170 | ### Frontend
G0171 | 
G0172 | - [x] Add a **Review conversation** section to every completed call page/drawer.
G0173 | - [x] Show recording and transcript beside the review form when available.
G0174 | - [x] Add 1–5 rating or thumbs-up/thumbs-down control.
G0175 | - [x] Add multi-select problem tags.
G0176 | - [x] Add an optional written comment.
G0177 | - [x] Show reward eligibility before submission.
G0178 | - [x] Show a clear confirmation after successful submission.
G0179 | - [x] Allow review editing without issuing a second reward.
G0180 | - [x] Add loading, retry and permission-error states.
G0181 | - [x] Add accessibility labels and keyboard navigation.
G0182 | - [x] Put the feedback controls **beside the recording's play button**, not only
G0183 |       on the call page.
G0184 | - [x] Offer all three response types on every recording: **thumbs up/down, a
G0185 |       voice note, and typed text**.
G0186 | - [x] Hard-cap a feedback voice note at **30 seconds**.
G0187 | - [x] Surface submitted reviews in the **admin panel** (`/admin/reviews`).
G0188 | 
G0189 | > **Where reviewing actually happens (2026-08-24).** Three ways to answer a
G0190 | > recording, sitting on the recording itself, because they cost different
G0191 | > amounts and carry different amounts of information — force one shape and
G0192 | > people use none:
G0193 | >
G0194 | > - **Thumbs up / down** — one click, says only better/worse, but it is the one
G0195 | >   people will actually give while working down a list. Thumbs-down writes
G0196 | >   rating 2, which puts the call in the "needs listening" queue (1s and 2s);
G0197 | >   1 stays available to mean something worse, chosen deliberately in the panel.
G0198 | > - **A voice note, capped at 30 seconds** — the fastest way to say something
G0199 | >   nuanced ("she'd already said no twice and it kept pitching"), which is
G0200 | >   exactly the feedback nobody types. The recorder stops itself at 30s, so it
G0201 | >   is a cutoff rather than a warning, and the server validates it too.
G0202 | > - **Typed text** — the only option when you are somewhere you cannot talk.
G0203 | >
G0204 | > Thumb and text write `conversation_reviews` (rating + comment, one review per
G0205 | > user per call). The voice note writes `call_feedback` — one note per call,
G0206 | > stored durably then transcribed. Kept separate because a voice note has a
G0207 | > lifecycle (upload, transcribe, retry) and a review is a structured judgement.
G0208 | >
G0209 | > **None of them overwrites another.** `submitReview` is a PUT of the whole
G0210 | > review, so a thumb resends the existing comment and tags untouched, and saving
G0211 | > a comment resends the existing rating. Without that, whichever control you
G0212 | > used last would silently erase the other.
G0213 | >
G0214 | > Live on the **Recordings** page (full bar under each player) and the **calls
G0215 | > list** (thumbs only — the row is a fixed grid, and an expanding panel in an
G0216 | > `auto` column would squash the other columns; the full panel is one click away
G0217 | > on the call page).
G0218 | >
G0219 | > Reading everyone's reviews is a different job from leaving one, so the
G0220 | > management view moved from a top-level `/reviews` route to `/admin/reviews`.
G0221 | > Its endpoint was always `require_admin_tenant`; only the navigation disagreed,
G0222 | > which meant a non-admin who clicked it got a bare 403. `/reviews` now
G0223 | > redirects, preserving existing links.
G0224 | 
G0225 | ### Safe Improvement Loop
G0226 | 
G0227 | - [x] Do not let a single review automatically rewrite the production prompt.
G0228 | - [x] Aggregate reviews by prompt version and failure category.
G0229 | - [x] Manually verify low-rated calls against recordings/transcripts.
G0230 | - [ ] Convert verified problems into evaluation cases.
G0231 | - [ ] Test candidate prompts against the evaluation set.
G0232 | - [ ] Deploy a prompt only after human approval and canary testing.
G0233 | - [x] Retain rollback access to the previous prompt version.
G0234 | 
G0235 | ### Acceptance Criteria
G0236 | 
G0237 | - [x] A valid user can review an accessible completed call.
G0238 | - [x] Unauthorized users receive no call or review data.
G0239 | - [x] A call cannot generate duplicate rewards for the same reviewer.
G0240 | - [x] Review edits preserve the original reward transaction.
G0241 | - [x] Admin can filter results by campaign, prompt version, rating and tag.
G0242 | - [x] Review submission does not change production prompts automatically.
G0243 | 
G0244 | ---
G0245 | 
G0246 | ## 4. Security as a Main Sidebar Section
G0247 | 
G0248 | ### Frontend
G0249 | 
G0250 | - [x] Add **Security** to the main left navigation.
G0251 | - [x] Remove or redirect the old Security entry inside Settings.
G0252 | - [x] Preserve deep links and bookmarks with a route redirect.
G0253 | - [x] Display only security controls the current role can manage.
G0254 | - [x] Include sections for:
G0255 |   - [x] Password/account security
G0256 |   - [x] Multi-factor authentication status
G0257 |   - [x] Active sessions
G0258 |   - [x] API keys/tokens
G0259 |   - [x] Audit activity
G0260 |   - [ ] Allowed IPs, if supported
G0261 |   - [x] Data retention and recording controls, if supported
G0262 | 
G0263 | > **Built 2026-08-24 — `/security`, reachable from the sidebar.**
G0264 | >
G0265 | > Password change (which signs out every other session), passkeys, 2FA status
G0266 | > with turn-off and recovery-code rotation, and active sessions. API keys and
G0267 | > audit activity appear only for admins — and the backend enforces that
G0268 | > independently, so hiding them is convenience, not the boundary.
G0269 | >
G0270 | > **"Allowed IPs" is left unticked because it is not supported.** There is no IP
G0271 | > allow-list anywhere in the backend. The page says so in as many words rather
G0272 | > than showing an empty panel that implies a control exists — a security page
G0273 | > that overstates what it enforces is worse than one that admits a gap.
G0274 | >
G0275 | > The Settings → Security tab is now a pointer to `/security`. The controls were
G0276 | > *moved*, not copied: MFA disable and recovery-code regeneration went with
G0277 | > them, so the same panel cannot exist in two files and drift apart.
G0278 | >
G0279 | > Retention is shown read-only, because it is set by plan rather than per user.
G0280 | 
G0281 | ### Backend/Security
G0282 | 
G0283 | - [x] Reuse existing endpoints where possible.
G0284 | - [x] Apply tenant and role authorization to every security endpoint.
G0285 | - [x] Never return raw API secrets after creation.
G0286 | - [x] Audit key creation, rotation, revocation and security-setting changes.
G0287 | - [x] Add rate limiting to sensitive actions.
G0288 | - [x] Add tests for viewer, partner-admin, tenant-admin and master-admin roles.
G0289 | 
G0290 | ### Acceptance Criteria
G0291 | 
G0292 | - [x] Security is directly accessible from the left sidebar.
G0293 | - [x] Old URLs redirect correctly.
G0294 | - [x] Unauthorized controls are hidden and rejected by the backend.
G0295 | - [x] Sensitive mutations appear in the audit log.
G0296 | 
G0297 | > **2026-08-31 verification note:** endpoint-auth, RBAC, admin-tenant-isolation,
G0298 | > API-security/rate-limit and audit-log suites cover the checked backend items.
G0299 | > “Allowed IPs” remains the sole unsupported control and is still presented as
G0300 | > unavailable rather than as an empty or misleading security feature.
G0301 | 
G0302 | ---
G0303 | 
G0304 | ## 5. Inbound Campaign MVP
G0305 | 
G0306 | ### Campaign Configuration
G0307 | 
G0308 | - [x] Add campaign type: `outbound` or `inbound`.
G0309 | - [x] Add an **Inbound** section to the sidebar.
G0310 | - [x] Allow users to create an inbound campaign.
G0311 | - [x] Required settings:
G0312 |   - [x] Campaign name
G0313 |   - [x] Assigned phone number/SIP route
G0314 |   - [x] Agent/prompt selection
G0315 |   - [x] Voice selection
G0316 |   - [x] Business hours and timezone
G0317 |   - [x] After-hours behavior
G0318 |   - [x] Greeting/opening message
G0319 |   - [x] Human transfer destination
G0320 |   - [x] Voicemail/fallback behavior
G0321 |   - [x] Recording and disclosure policy
G0322 |   - [x] Call outcome rules
G0323 | - [x] Prevent the same inbound number from being actively assigned to conflicting campaigns.
G0324 | - [x] Add activate, pause and archive actions.
G0325 | 
G0326 | ### Runtime
G0327 | 
G0328 | - [x] Resolve incoming DID/SIP destination to tenant and inbound campaign.
G0329 | - [x] Create the session with the correct tenant, campaign, prompt and voice.
G0330 | - [x] Pass caller phone number as contact context where permitted.
G0331 | - [x] Apply business-hours logic before starting the normal agent flow.
G0332 | - [x] Support transfer failure and after-hours fallback.
G0333 | - [x] Persist inbound direction, DID, caller ID, campaign ID and outcome.
G0334 | - [x] Apply concurrency, quota and billing checks.
G0335 | - [x] Add structured logs for routing decisions.
G0336 | 
G0337 | ### Acceptance Criteria
G0338 | 
G0339 | - [x] A test number routes to exactly one correct tenant/campaign.
G0340 | - [ ] The correct inbound agent answers with the configured greeting.
G0341 | - [x] Calls outside business hours follow the configured fallback.
G0342 | - [ ] Transfer success and failure are handled clearly.
G0343 | - [x] Inbound minutes appear correctly in usage/billing.
G0344 | - [x] Tenant A cannot see or route Tenant B's calls.
G0345 | 
G0346 | > **2026-08-31 verification note:** checked items above are backed by repository
G0347 | > migrations, tenant-scoped services/UI, and automated tests (including the
G0348 | > two-DID/two-tenant routing proof). They do not substitute for the frozen live
G0349 | > carrier canary. The two unchecked criteria deliberately require that live
G0350 | > evidence: first configured greeting audio on the production route, and an
G0351 | > approved transfer success/failure exercise with transfer gates enabled.
G0352 | 
G0353 | ---
G0354 | 
G0355 | ## 6. Generic Lead-Generation Prompt: Implementation and Testing
G0356 | 
G0357 | ### Backend Integration
G0358 | 
G0359 | - [x] Store the master template as `generic_lead_generation` with a version.
G0360 | - [x] Do not use `You are a helpful AI assistant` for campaign calls.
G0361 | - [x] Load the selected campaign's prompt and configuration from the database.
G0362 | - [x] Render campaign variables before session creation.
G0363 | - [x] Fail validation when required variables are missing.
G0364 | - [x] Never send unresolved `{{variable}}` placeholders to the LLM.
G0365 | - [x] Add lead-specific context separately from stable system instructions.
G0366 | - [x] Log `campaign_id`, `prompt_template`, `prompt_version` and `prompt_hash`.
G0367 | - [x] Keep prompt versions immutable after use; create a new version for changes.
G0368 | - [x] Add rollback to the previous approved prompt version.
G0369 | 
G0370 | > **2026-08-31 verification note:** the prompt registry/composer, strict slot
G0371 | > validation, durable prompt identity and archived-body rollback are covered by
G0372 | > `test_prompt_versions.py`, `test_prompt_identity_persist.py` and
G0373 | > `test_prompt_rollback.py`. The controlled-call matrix and release scorecard
G0374 | > below remain open because no immutable 30-call evidence set was supplied.
G0375 | 
G0376 | ### Prompt Test Matrix
G0377 | 
G0378 | - [ ] Opening and reason for calling
G0379 | - [ ] Prospect says they are busy
G0380 | - [ ] Prospect asks, "Why are you calling?"
G0381 | - [ ] Prospect asks, "What does your company do?"
G0382 | - [ ] Prospect asks about price
G0383 | - [ ] Interested prospect
G0384 | - [ ] Not interested
G0385 | - [ ] Existing provider
G0386 | - [ ] Send information by email
G0387 | - [ ] Callback request
G0388 | - [ ] Human transfer request
G0389 | - [ ] Wrong number
G0390 | - [ ] Do-not-call request
G0391 | - [ ] Prospect interrupts the agent
G0392 | - [ ] Prospect gives several business details in one answer
G0393 | - [ ] Prospect provides incomplete information
G0394 | - [ ] Normal successful booking
G0395 | - [ ] Tool/booking/transfer failure
G0396 | 
G0397 | ### Conversation Scorecard
G0398 | 
G0399 | - [ ] Direct questions answered before qualification
G0400 | - [ ] One question asked at a time
G0401 | - [ ] Responses normally limited to one or two sentences
G0402 | - [ ] No repeated pitch after a clear rejection
G0403 | - [ ] No fabricated facts or tool success
G0404 | - [ ] Correct details captured
G0405 | - [ ] Correct outcome selected
G0406 | - [ ] Natural closing
G0407 | - [ ] No talking over the caller
G0408 | - [ ] No stale audio after interruption
G0409 | 
G0410 | ### Release Gate
G0411 | 
G0412 | - [ ] At least 30 controlled calls on one frozen prompt/runtime version.
G0413 | - [ ] At least five speakers and two accents.
G0414 | - [ ] At least 95% of direct questions answered correctly.
G0415 | - [ ] At least 95% correct call outcome classification.
G0416 | - [ ] Zero ignored do-not-call requests.
G0417 | - [ ] Zero fabricated bookings, transfers or prices.
G0418 | - [ ] No old prompt used in any test call.
G0419 | - [ ] Every call has a call ID, recording/transcript, prompt version and score.
G0420 | 
G0421 | ---
G0422 | 
G0423 | ## 7. Interested-Lead Information Form
G0424 | 
G0425 | ### Data Model
G0426 | 
G0427 | - [x] Create a structured `lead_capture` or `call_lead_details` record linked to call, campaign, contact and tenant.
G0428 | - [x] Support campaign-defined custom fields.
G0429 | - [x] Field types: text, number, email, phone, date/time, single select, multi-select and notes.
G0430 | - [x] Mark fields as required, optional, agent-visible and user-visible.
G0431 | - [x] Record the source of each value: imported contact, caller statement, agent inference or manual edit.
G0432 | - [x] Do not treat inferred values as confirmed facts.
G0433 | 
G0434 | ### Agent Behavior
G0435 | 
G0436 | - [x] Supply required field definitions to the agent.
G0437 | - [x] Let the agent extract fields from natural conversation.
G0438 | - [x] Do not force the agent to ask for information already provided.
G0439 | - [x] Confirm important contact and appointment information.
G0440 | - [x] Use `unknown` when information was not provided.
G0441 | - [x] Update structured fields after each confirmed detail or at call completion.
G0442 | 
G0443 | ### Frontend
G0444 | 
G0445 | - [x] Show an **Interested lead** badge when interest is detected/confirmed.
G0446 | - [x] Open a compact lead-information panel from the call page.
G0447 | - [x] Display captured business/customer details in a readable form.
G0448 | - [x] Highlight missing required fields.
G0449 | - [x] Allow authorized users to correct or complete details.
G0450 | - [x] Show who/what supplied each value.
G0451 | - [x] Add save, validation and conflict handling.
G0452 | 
G0453 | ### Acceptance Criteria
G0454 | 
G0455 | - [x] Information spoken once is captured without being asked again.
G0456 | - [x] The form is linked to the correct tenant, call and contact.
G0457 | - [x] Missing details remain visibly missing rather than being invented.
G0458 | - [ ] Manual corrections are audited.
G0459 | - [ ] Captured information is available to approved CRM synchronization.
G0460 | 
G0461 | > **2026-08-31 verification note:** migration `0020`, the tenant-scoped capture
G0462 | > service, per-turn/teardown persistence, lead-details API and call-page panel
G0463 | > prove the checked behavior. Manual edits retain `source=manual_edit`, but no
G0464 | > separate actor/action audit event was found; CRM availability is also not an
G0465 | > approved connector synchronization proof. Those two items stay unchecked.
G0466 | 
G0467 | ---
G0468 | 
G0469 | ## 8. Tooltips and Information Popovers
G0470 | 
G0471 | ### Component
G0472 | 
G0473 | - [x] Build one reusable tooltip/popover component.
G0474 | - [x] Support mouse hover, keyboard focus and mobile tap.
G0475 | - [x] Add a short label plus optional "Learn more" content.
G0476 | - [x] Avoid hiding essential warnings only inside tooltips.
G0477 | - [x] Ensure the popup stays inside the viewport.
G0478 | 
G0479 | ### AI Options
G0480 | 
G0481 | - [x] Add information help for **Tokens** explaining:
G0482 |   - [x] Tokens are pieces of input/output text.
G0483 |   - [x] Higher limits allow longer replies but may increase latency and cost.
G0484 |   - [x] Voice-agent replies should normally remain short.
G0485 | - [x] Add information help for **Creativity/Temperature** explaining:
G0486 |   - [x] Lower values are more consistent and predictable.
G0487 |   - [x] Higher values are more varied but may increase mistakes.
G0488 |   - [x] Recommended lead-generation range is shown without silently changing it.
G0489 | 
G0490 | ### AI Summary
G0491 | 
G0492 | - [x] Add hover/focus information for every main metric or conclusion.
G0493 | - [x] Explain how the summary was generated.
G0494 | - [x] Distinguish transcript facts from AI-inferred conclusions.
G0495 | - [x] Display confidence or "needs review" where appropriate.
G0496 | - [x] Explain key terms such as qualified, interested, callback and unsuccessful.
G0497 | 
G0498 | ### Acceptance Criteria
G0499 | 
G0500 | - [x] Every requested help icon works with mouse and keyboard.
G0501 | - [x] Mobile users can open and close the same information.
G0502 | - [x] Explanations use simple language.
G0503 | - [ ] Popovers do not cover save buttons or critical fields.
G0504 | 
G0505 | ---
G0506 | 
G0507 | ## 9. Billing Minute Top-Up
G0508 | 
G0509 | ### Backend
G0510 | 
G0511 | - [x] Define approved top-up packages and currency.
G0512 | - [x] Create a top-up order before payment.
G0513 | - [x] Use the payment provider's hosted checkout or secure payment flow.
G0514 | - [x] Verify signed payment webhooks.
G0515 | - [x] Make webhook processing idempotent.
G0516 | - [x] Credit minutes only after verified successful payment.
G0517 | - [x] Record money and minutes in an immutable billing ledger.
G0518 | - [x] Handle failed, cancelled, duplicate, refunded and disputed payments.
G0519 | - [x] Send receipt/confirmation according to configured channel.
G0520 | - [x] Add admin reconciliation view or export.
G0521 | 
G0522 | ### Frontend
G0523 | 
G0524 | - [x] Add **Top up minutes** to Billing.
G0525 | - [x] Show current minute balance.
G0526 | - [x] Show package minutes, price, currency and expiry rules.
G0527 | - [x] Show payment status and top-up history.
G0528 | - [x] Prevent double submission while checkout is starting.
G0529 | - [x] Show clear failure and retry guidance.
G0530 | 
G0531 | ### Acceptance Criteria
G0532 | 
G0533 | - [x] Successful verified payment credits minutes once.
G0534 | - [x] Duplicate webhook does not duplicate minutes.
G0535 | - [x] Failed/cancelled payment adds no minutes.
G0536 | - [x] Tenant billing records remain isolated. — The 2026-08-31 production proof
G0537 |       confirmed the application role no longer has `BYPASSRLS` (a bare
G0538 |       connection saw zero call rows while the explicit tenant/bypass context
G0539 |       saw 1,041). Tenant-aware pooled acquisition, RLS policies and the static
G0540 |       invariant now protect billing/usage paths; live canary reconciliation is
G0541 |       still required by the global release gate.
G0542 | - [x] New balance is reflected in call quota enforcement.
G0543 | 
G0544 | ---
G0545 | 
G0546 | ## 10. Salesforce MVP
G0547 | 
G0548 | > **Frontend safety status — 2026-09-01:** Salesforce is now visible in the
G0549 | > connector interface, but it fails closed when the server does not advertise
G0550 | > Salesforce support: the card says **Unavailable**, explains the missing
G0551 | > server capability and exposes no Connect/Reconnect OAuth action. This is not
G0552 | > a completed Salesforce connector. The checklist below stays open until
G0553 | > tenant-scoped OAuth, encrypted token storage, refresh, sync, retry,
G0554 | > reconciliation and isolation are implemented and proven end to end.
G0555 | 
G0556 | ### September 10 Scope
G0557 | 
G0558 | - [ ] Add Salesforce as a connector.
G0559 | - [ ] Implement OAuth authorization with secure state validation.
G0560 | - [ ] Store tokens encrypted and tenant-scoped.
G0561 | - [ ] Refresh access tokens safely.
G0562 | - [ ] Allow the tenant to disconnect Salesforce.
G0563 | - [ ] Map Talk-lee contact/lead fields to Salesforce Lead or Contact fields.
G0564 | - [ ] Push qualified/interested leads to Salesforce.
G0565 | - [ ] Store Salesforce object ID and synchronization status.
G0566 | - [ ] Retry transient failures with bounded backoff.
G0567 | - [ ] Send failures to a dead-letter/reconciliation queue.
G0568 | - [ ] Prevent duplicate Salesforce records with a documented matching strategy.
G0569 | - [ ] Add audit logs without exposing access tokens.
G0570 | 
G0571 | ### Explicitly Deferred
G0572 | 
G0573 | - [ ] Bidirectional synchronization
G0574 | - [ ] Salesforce opportunity creation
G0575 | - [ ] Salesforce campaign membership
G0576 | - [ ] Activity/task synchronization
G0577 | - [ ] Complex custom-object mapping
G0578 | - [ ] Historical bulk synchronization
G0579 | 
G0580 | ### Acceptance Criteria
G0581 | 
G0582 | - [ ] Tenant can connect and disconnect Salesforce safely.
G0583 | - [ ] One qualified test lead reaches the correct Salesforce account.
G0584 | - [ ] Retrying the same event does not create an unintended duplicate.
G0585 | - [ ] Authentication and API failures are visible to the tenant/admin.
G0586 | - [ ] One tenant cannot access another tenant's Salesforce connection.
G0587 | 
G0588 | ---
G0589 | 
G0590 | ## 11. Expanded Contact Fields
G0591 | 
G0592 | ### Canonical Contact Model
G0593 | 
G0594 | - [x] `first_name`
G0595 | - [x] `last_name`
G0596 | - [x] `full_name` as display/derived field where possible
G0597 | - [x] `mobile_number`
G0598 | - [x] `business_number`
G0599 | - [x] `email`
G0600 | - [x] `company_name`
G0601 | - [x] `job_title` or role
G0602 | - [x] `best_time_to_call`
G0603 | - [x] `timezone`
G0604 | - [x] `calling_notes`
G0605 | - [x] `preferred_contact_method`, if needed
G0606 | - [x] `do_not_call`
G0607 | - [x] `custom_fields`
G0608 | 
G0609 | ### Data Rules
G0610 | 
G0611 | - [x] Avoid storing duplicate conflicting `phone_number` and `mobile_number` values without defining a canonical calling number.
G0612 | - [x] Add `primary_phone_type` or a clear priority rule.
G0613 | - [x] Normalize phone numbers to E.164 while preserving display formatting if needed.
G0614 | - [x] Validate email without rejecting legitimate formats.
G0615 | - [x] Interpret `best_time_to_call` together with timezone.
G0616 | - [x] Do not call when `do_not_call=true`.
G0617 | - [x] Encrypt or protect sensitive contact data according to platform policy.
G0618 | - [ ] Audit imports, edits and deletions.
G0619 | 
G0620 | ### Frontend and Import
G0621 | 
G0622 | - [x] Update add/edit contact form.
G0623 | - [x] Update contact details view and table columns.
G0624 | - [x] Update CSV import template.
G0625 | - [x] Add column mapping during import.
G0626 | - [x] Show row-level validation failures.
G0627 | - [x] Add duplicate detection and merge/skip decision.
G0628 | - [x] Let campaign creation select which contact fields the agent may use.
G0629 | 
G0630 | > **Verified 2026-09-01:** both guided and detailed campaign creation use the
G0631 | > server-owned contact-field registry, expose explicit per-field access and
G0632 | > requiredness, and block saving if the policy cannot be loaded. Creation retry
G0633 | > reuses the already-created campaign ID so a failed policy save cannot create a
G0634 | > duplicate campaign. The same policy editor is available on knowledge-driven
G0635 | > campaign edit pages.
G0636 | 
G0637 | ### Agent Context
G0638 | 
G0639 | - [x] Pass only necessary fields into the call prompt/context.
G0640 | - [x] Use the preferred calling number.
G0641 | - [x] Respect best time to call and timezone in dialer scheduling.
G0642 | - [x] Supply calling notes without allowing them to override system/security rules.
G0643 | - [x] Clearly delimit imported notes as untrusted data.
G0644 | - [x] Avoid reading internal notes aloud unless explicitly required.
G0645 | 
G0646 | ### Acceptance Criteria
G0647 | 
G0648 | - [x] Manual and CSV-created contacts produce the same schema.
G0649 | - [x] Dialer chooses the correct phone number.
G0650 | - [x] Agent receives approved name, business and call notes.
G0651 | - [x] Best-time scheduling respects timezone.
G0652 | - [x] Do-not-call contacts cannot be queued.
G0653 | 
G0654 | > **2026-08-31 verification note:** migration `0020`, the canonical field
G0655 | > registry, CSV mapper, contact page, phone normalizer, timezone precedence,
G0656 | > prompt-safety fences and DNC guard cover the checked items. A complete
G0657 | > import/edit/delete audit trail and per-campaign field allowlist were not
G0658 | > found, so those two items remain open.
G0659 | 
G0660 | ---
G0661 | 
G0662 | ## 12. Client Management and 200-Tenant Validation
G0663 | 
G0664 | ### Test Objective
G0665 | 
G0666 | Prove that Talk-lee can manage at least 200 separate client organizations without mixing their data, permissions, files, calls, usage or billing. This is a multi-tenant correctness and platform-capacity test—not only a login test.
G0667 | 
G0668 | Use synthetic test clients and synthetic contact information. Do not use real client data for this test.
G0669 | 
G0670 | ### Test Population
G0671 | 
G0672 | - [ ] Create 200 synthetic tenant/client organizations.
G0673 | - [ ] Give every tenant a unique tenant ID, company name and subscription.
G0674 | - [ ] Create at least one tenant administrator for every tenant.
G0675 | - [ ] Create additional role samples across the population:
G0676 |   - [ ] Tenant admin
G0677 |   - [ ] Campaign manager
G0678 |   - [ ] Agent/operator
G0679 |   - [ ] Billing user
G0680 |   - [ ] Read-only user
G0681 |   - [ ] Partner/reseller user, where supported
G0682 | - [ ] Create a master-admin account that can manage all 200 tenants.
G0683 | - [ ] Seed different plans, balances, quotas and feature permissions.
G0684 | - [ ] Seed active, trial, suspended, cancelled and overdue client states.
G0685 | - [ ] Generate contacts, campaigns, calls, transcripts, recordings, attachments, reviews and billing records for every tenant.
G0686 | - [ ] Keep a deterministic seed/manifest so failed records can be traced and the test can be repeated.
G0687 | 
G0688 | ### Authentication and Session Tests
G0689 | 
G0690 | - [ ] Sign in successfully as a user from each of the 200 tenants.
G0691 | - [ ] Confirm every login resolves the correct tenant and role.
G0692 | - [ ] Test 200 sequential sign-ins.
G0693 | - [ ] Test 200 concurrent active authenticated sessions.
G0694 | - [ ] Test repeated login, logout, token refresh and session expiry.
G0695 | - [ ] Confirm a user switching browser tabs cannot inherit another tenant's context.
G0696 | - [ ] Confirm cached API responses are tenant-scoped.
G0697 | - [ ] Confirm password reset and invitation links are tenant/user specific.
G0698 | - [ ] Confirm disabled or suspended users cannot create new sessions.
G0699 | - [ ] Confirm revoked sessions stop working.
G0700 | - [ ] Check that session cookies/tokens use secure settings and are not exposed in logs.
G0701 | 
G0702 | ### Tenant Isolation Matrix
G0703 | 
G0704 | For selected tenant pairs—and through automated tests across all 200 tenants—attempt to read or mutate another tenant's resources by changing IDs in URLs and API requests.
G0705 | 
G0706 | - [ ] Tenant profile and settings
G0707 | - [ ] Users, invitations and roles
G0708 | - [ ] Contacts and imported lead lists
G0709 | - [ ] Campaigns and campaign configurations
G0710 | - [ ] Phone numbers and SIP configurations
G0711 | - [ ] Inbound routing and transfer destinations
G0712 | - [ ] Calls, recordings and transcripts
G0713 | - [ ] Conversation reviews and rewards
G0714 | - [ ] Interested-lead forms and captured details
G0715 | - [ ] Attachments and generated download links
G0716 | - [ ] Meetings, reminders, email and SMS records
G0717 | - [ ] Connectors and Salesforce credentials
G0718 | - [ ] Usage, quotas, invoices, payments and minute balances
G0719 | - [ ] API keys, audit logs and security settings
G0720 | 
G0721 | Every unauthorized cross-tenant request must be rejected without revealing whether the target resource exists.
G0722 | 
G0723 | ### Client Management/Admin Tests
G0724 | 
G0725 | - [ ] Master admin can search, filter and paginate 200 tenants.
G0726 | - [ ] Master admin can open a tenant without loading unrelated tenant data.
G0727 | - [ ] Create a new tenant and verify default plan, roles, quotas and settings.
G0728 | - [ ] Edit tenant profile and subscription safely.
G0729 | - [ ] Suspend a tenant and verify its users/campaigns cannot continue prohibited activity.
G0730 | - [ ] Reactivate a tenant without corrupting historical data.
G0731 | - [ ] Archive/cancel a tenant according to retention policy.
G0732 | - [ ] Verify impersonation/support-access features, if present, are authorized, time-limited and audited.
G0733 | - [ ] Verify bulk actions require confirmation and cannot silently affect the wrong clients.
G0734 | - [ ] Confirm tenant list totals, status counts and pagination remain correct after changes.
G0735 | 
G0736 | ### Attachments, Recordings and File Storage
G0737 | 
G0738 | - [ ] Upload permitted file types for all 200 tenants.
G0739 | - [ ] Reject prohibited file types and oversized files.
G0740 | - [ ] Validate MIME type/content rather than trusting the filename.
G0741 | - [ ] Confirm object/storage keys contain safe tenant scoping.
G0742 | - [ ] Confirm download URLs cannot be reused to access another tenant's file.
G0743 | - [ ] Test attachment preview, download, replacement and deletion.
G0744 | - [ ] Confirm deleting a database row does not leave sensitive files indefinitely without a cleanup policy.
G0745 | - [ ] Confirm deleting/replacing a file does not break another tenant's file.
G0746 | - [ ] Scan uploads for malware if the product accepts arbitrary attachments.
G0747 | - [ ] Verify encryption, retention and backup/restore behavior.
G0748 | - [ ] Check quotas for total storage, per-file size and file count.
G0749 | - [ ] Verify recordings and transcripts remain linked to the correct call and tenant.
G0750 | 
G0751 | ### Billing, Plans and Minute Balances
G0752 | 
G0753 | - [ ] Seed different plans and limits across the 200 tenants.
G0754 | - [ ] Confirm every tenant sees only its own subscription, invoices and payments.
G0755 | - [ ] Confirm plan features and quotas are enforced independently.
G0756 | - [ ] Test minute deductions for inbound and outbound calls.
G0757 | - [ ] Test top-up purchases and balance updates.
G0758 | - [ ] Confirm duplicate payment webhooks do not duplicate minutes.
G0759 | - [ ] Confirm failed, cancelled, refunded and disputed payments adjust access/balance correctly.
G0760 | - [ ] Confirm one tenant's call cannot deduct another tenant's minutes.
G0761 | - [ ] Reconcile call-duration records against billed minutes.
G0762 | - [ ] Test zero balance, low balance, quota exceeded and unlimited/enterprise conditions.
G0763 | - [ ] Confirm currency, taxes and invoice numbering follow the configured billing rules.
G0764 | - [ ] Confirm billing administrators can see billing while unauthorized roles cannot.
G0765 | - [ ] Confirm every balance change has an immutable ledger/audit event.
G0766 | 
G0767 | ### Campaigns, Calls and Contacts
G0768 | 
G0769 | - [ ] Create outbound and inbound campaigns under multiple tenants.
G0770 | - [ ] Confirm campaign lists, counts and dashboards are tenant-scoped.
G0771 | - [ ] Import contacts for all tenants using expanded contact fields.
G0772 | - [ ] Confirm duplicate detection runs only within the intended tenant scope.
G0773 | - [ ] Confirm best-time-to-call and timezone rules are applied per contact.
G0774 | - [ ] Confirm do-not-call rules block queueing and calling.
G0775 | - [ ] Start campaigns for several tenants simultaneously.
G0776 | - [ ] Confirm concurrency and quotas are enforced per tenant and globally.
G0777 | - [ ] Confirm incoming numbers route to the correct tenant/campaign.
G0778 | - [ ] Confirm prompts, voices, transfer numbers and connectors never cross tenants.
G0779 | - [ ] Confirm call outcomes, interested-lead details and reviews attach to the correct tenant.
G0780 | 
G0781 | ### Connector and Credential Isolation
G0782 | 
G0783 | - [ ] Connect different Salesforce/test integrations for selected tenants.
G0784 | - [ ] Confirm each connector uses only its owning tenant's encrypted credentials.
G0785 | - [ ] Confirm disconnecting Tenant A does not affect Tenant B.
G0786 | - [ ] Test token refresh and expired/revoked credential behavior.
G0787 | - [ ] Confirm connector jobs and retry queues preserve tenant ID.
G0788 | - [ ] Confirm dead-letter/retry records contain no raw secrets.
G0789 | - [ ] Confirm webhook events resolve the correct tenant before processing.
G0790 | 
G0791 | ### Performance and Capacity
G0792 | 
G0793 | - [ ] Measure tenant-list page with 200 clients.
G0794 | - [ ] Measure dashboard/API response time with seeded tenant data.
G0795 | - [ ] Test 200 active user sessions with realistic navigation and API requests.
G0796 | - [ ] Test concurrent contact imports, attachment uploads and report views.
G0797 | - [ ] Test simultaneous campaign activity within the safe call-capacity limit.
G0798 | - [ ] Monitor application CPU, memory, database connections, query latency, cache hit rate, queue depth and storage errors.
G0799 | - [ ] Identify N+1 queries and missing indexes.
G0800 | - [ ] Verify pagination is used instead of loading all tenants/calls/contacts into memory.
G0801 | - [ ] Define and record P50, P95 and maximum response times for critical endpoints.
G0802 | - [ ] Run a soak test for at least two hours to detect memory, connection or queue leaks.
G0803 | 
G0804 | ### Failure and Recovery Tests
G0805 | 
G0806 | - [ ] Restart backend services while 200 sessions exist and verify safe recovery.
G0807 | - [ ] Simulate database timeout and connection-pool exhaustion.
G0808 | - [ ] Simulate Redis/cache unavailability.
G0809 | - [ ] Simulate object-storage upload/download failure.
G0810 | - [ ] Simulate payment-webhook retry and duplication.
G0811 | - [ ] Simulate connector/API failure.
G0812 | - [ ] Confirm failures do not mix tenants or corrupt balances.
G0813 | - [ ] Confirm retry jobs remain idempotent and tenant-scoped.
G0814 | - [ ] Confirm monitoring identifies the affected tenant without exposing another tenant's data.
G0815 | - [ ] Test backup restore in a non-production environment and verify tenant/file relationships.
G0816 | 
G0817 | ### Audit, Privacy and Data Lifecycle
G0818 | 
G0819 | - [ ] Audit tenant creation, suspension, deletion and subscription changes.
G0820 | - [ ] Audit user invitations, role changes and sensitive access.
G0821 | - [ ] Audit attachment, recording, billing and connector actions.
G0822 | - [ ] Redact tokens, passwords, payment data and unnecessary personal information from logs.
G0823 | - [ ] Verify retention and deletion rules for calls, recordings, transcripts, attachments and reviews.
G0824 | - [ ] Verify tenant export contains only that tenant's data.
G0825 | - [ ] Verify tenant deletion/anonymization does not delete shared platform configuration or another tenant's records.
G0826 | 
G0827 | ### Required Evidence
G0828 | 
G0829 | - [ ] Test run ID, environment and timestamp
G0830 | - [ ] Exact application/runtime version and configuration
G0831 | - [ ] Synthetic tenant seed/manifest version
G0832 | - [ ] Total tenants/users/sessions created
G0833 | - [ ] Total checks passed, failed and skipped
G0834 | - [ ] Cross-tenant access attempt results
G0835 | - [ ] Billing reconciliation report
G0836 | - [ ] Attachment/storage reconciliation report
G0837 | - [ ] Performance P50/P95/max results
G0838 | - [ ] CPU, memory, database, cache and queue graphs
G0839 | - [ ] Every failed scenario with request/trace ID
G0840 | - [ ] Retest evidence after fixes
G0841 | - [ ] Cleanup confirmation for synthetic accounts and stored files
G0842 | 
G0843 | ### Release Acceptance Criteria
G0844 | 
G0845 | - [ ] All 200 tenants can be created, authenticated and managed.
G0846 | - [ ] Zero successful cross-tenant data-access attempts.
G0847 | - [ ] Zero attachments, calls, contacts, credentials or billing records assigned to the wrong tenant.
G0848 | - [ ] Zero incorrect minute deductions or duplicate top-up credits.
G0849 | - [ ] All role restrictions behave as designed.
G0850 | - [ ] No unresolved P0/P1 security or data-integrity defects.
G0851 | - [ ] Critical dashboard/API P95 response time meets the agreed target under the 200-session test.
G0852 | - [ ] No sustained memory, connection or queue leak during the soak test.
G0853 | - [ ] Backup/restore and rollback procedures are proven in a safe environment.
G0854 | - [ ] Synthetic test data is removed or clearly isolated after validation.
G0855 | 
G0856 | ---
G0857 | 
G0858 | ## 13. Delivery Calendar
G0859 | 
G0860 | ### August 22–24 — Design and Contracts
G0861 | 
G0862 | **Developer A**
G0863 | 
G0864 | - [ ] Finalize database migrations and API contracts.
G0865 | - [ ] Define prompt template schema/versioning.
G0866 | - [ ] Define inbound routing and billing rules.
G0867 | - [ ] Define review reward ledger and Salesforce MVP boundary.
G0868 | - [ ] Define 200-tenant synthetic seed, tenant-isolation matrix and billing/file reconciliation tests.
G0869 | 
G0870 | **Developer B**
G0871 | 
G0872 | - [ ] Produce page/component wireframes.
G0873 | - [ ] Define sidebar changes and routes.
G0874 | - [ ] Define shared tooltip/popover and form components.
G0875 | - [ ] Confirm frontend API payloads with Developer A.
G0876 | 
G0877 | **Joint gate**
G0878 | 
G0879 | - [ ] API contracts frozen before parallel implementation.
G0880 | - [ ] Migration rollback plan reviewed.
G0881 | 
G0882 | ### August 25–29 — Core P0 Build
G0883 | 
G0884 | **Developer A**
G0885 | 
G0886 | - [ ] Contact migration and agent-context mapping.
G0887 | - [ ] Generic prompt runtime integration and logging.
G0888 | - [ ] Conversation review APIs.
G0889 | - [ ] Interested-lead structured capture.
G0890 | - [ ] Inbound routing foundation.
G0891 | 
G0892 | **Developer B**
G0893 | 
G0894 | - [ ] Expanded contacts UI/import mapping.
G0895 | - [ ] Conversation review UI.
G0896 | - [ ] Interested-lead panel.
G0897 | - [ ] Security sidebar/page move.
G0898 | - [ ] Tooltip/popover component.
G0899 | 
G0900 | **Parallel QA**
G0901 | 
G0902 | - [ ] Establish baseline calls using the old prompt.
G0903 | - [ ] Prepare 30-call test scripts and scoring rubric.
G0904 | - [ ] Prepare the 200-tenant test environment and synthetic accounts.
G0905 | 
G0906 | ### August 30–September 3 — Inbound, Billing and UX Completion
G0907 | 
G0908 | **Developer A**
G0909 | 
G0910 | - [ ] Complete inbound campaign MVP.
G0911 | - [ ] Complete top-up order, webhook and ledger flow.
G0912 | - [ ] Add reward idempotency and abuse controls.
G0913 | - [ ] Add backend tests and audit events.
G0914 | 
G0915 | **Developer B**
G0916 | 
G0917 | - [ ] Complete inbound campaign pages.
G0918 | - [ ] Complete minute top-up UI.
G0919 | - [ ] Complete AI Options and AI Summary help content.
G0920 | - [ ] Complete reward display and review analytics basics.
G0921 | 
G0922 | **Parallel QA**
G0923 | 
G0924 | - [ ] Run prompt experiment batch 1.
G0925 | - [ ] Review failures and approve prompt version 1.1 only if evidence supports it.
G0926 | 
G0927 | ### September 4–6 — Salesforce MVP and Integration Testing
G0928 | 
G0929 | **Developer A**
G0930 | 
G0931 | - [ ] Salesforce OAuth and one-way lead/contact push.
G0932 | - [ ] Retry, deduplication and reconciliation handling.
G0933 | - [ ] End-to-end tenant/security tests.
G0934 | 
G0935 | **Developer B**
G0936 | 
G0937 | - [ ] Salesforce connector and field-mapping UI.
G0938 | - [ ] Integration status/error interface.
G0939 | - [ ] Complete responsive and accessibility checks.
G0940 | 
G0941 | **Joint gate**
G0942 | 
G0943 | - [ ] If any P0 item is unstable, pause Salesforce and finish P0.
G0944 | 
G0945 | ### September 7 — Integrated Release Candidate
G0946 | 
G0947 | - [ ] Merge only reviewed changes.
G0948 | - [ ] Apply migrations in staging/canary.
G0949 | - [ ] Run backend, frontend and C++ builds/tests.
G0950 | - [ ] Verify runtime configuration and secrets.
G0951 | - [ ] Verify inbound and outbound dashboard routing.
G0952 | - [ ] Verify billing in payment-provider test mode.
G0953 | - [ ] Seed and smoke-test the 200 synthetic tenant/client accounts.
G0954 | 
G0955 | ### September 8 — Freeze and Controlled Validation
G0956 | 
G0957 | - [ ] Freeze code, prompt and runtime configuration.
G0958 | - [ ] Run 30 controlled lead-generation calls.
G0959 | - [ ] Run inbound call matrix.
G0960 | - [ ] Run review/reward abuse tests.
G0961 | - [ ] Run top-up duplicate-webhook tests.
G0962 | - [ ] Run tenant isolation/security tests.
G0963 | - [ ] Run Salesforce duplicate/retry tests if included.
G0964 | - [ ] Run the complete 200-tenant isolation, billing, attachment and session test.
G0965 | - [ ] Run the two-hour multi-tenant soak test.
G0966 | 
G0967 | ### September 9 — Fix Only Release Blockers
G0968 | 
G0969 | - [ ] No new features.
G0970 | - [ ] Fix only confirmed release-blocking defects.
G0971 | - [ ] Repeat affected regression tests.
G0972 | - [ ] Rehearse code, database and configuration rollback.
G0973 | - [ ] Prepare release notes and known limitations.
G0974 | 
G0975 | ### September 10 — Controlled Release
G0976 | 
G0977 | - [ ] Deploy exact approved commit/images.
G0978 | - [ ] Apply verified migrations.
G0979 | - [ ] Confirm effective prompt version and configuration.
G0980 | - [ ] Run one inbound and one outbound smoke call.
G0981 | - [ ] Verify billing and review submission.
G0982 | - [ ] Monitor errors, call failures, latency and payment webhooks.
G0983 | - [ ] Increase traffic gradually only if metrics remain healthy.
G0984 | 
G0985 | ---
G0986 | 
G0987 | ## 14. Definition of Done
G0988 | 
G0989 | A feature is not "done" merely because code is pushed.
G0990 | 
G0991 | - [ ] Product behavior matches acceptance criteria.
G0992 | - [ ] Tenant authorization is enforced in backend tests.
G0993 | - [ ] Database migration and rollback are tested.
G0994 | - [ ] Frontend handles loading, empty, success and failure states.
G0995 | - [ ] Audit and operational logs contain useful identifiers but no secrets.
G0996 | - [ ] Automated tests pass in a reproducible environment.
G0997 | - [ ] Feature is tested in staging/canary.
G0998 | - [ ] Documentation and configuration are updated.
G0999 | - [ ] Monitoring and error reporting exist.
G1000 | - [ ] A rollback version and procedure are recorded.
G1001 | - [ ] Product owner accepts the feature using a real end-to-end scenario.
G1002 | 
G1003 | ---
G1004 | 
G1005 | ## 15. Daily Management Checklist
G1006 | 
G1007 | - [ ] 15-minute morning stand-up.
G1008 | - [ ] Each developer states yesterday's evidence, today's goal and blocker.
G1009 | - [ ] No task remains "90% done" without a named missing acceptance item.
G1010 | - [ ] Pull requests stay small enough to review.
G1011 | - [ ] Database/API contract changes are communicated before frontend work continues.
G1012 | - [ ] Production is not used as the development test environment.
G1013 | - [ ] Prompt changes are versioned and tested separately from code changes.
G1014 | - [ ] End-of-day tracker shows completed, blocked, failed-test and ready-for-review items.
G1015 | 
G1016 | ## Final Delivery Decision
G1017 | 
G1018 | The September 10 release should prioritize reliable P0 behavior. If the team falls behind, defer Salesforce beyond the connector MVP and defer automatic AI training. Do not sacrifice inbound routing correctness, tenant security, billing integrity, prompt runtime correctness or contact-data safety to claim that every requested feature shipped.
````

## Appendix F — Complete committed patch

This is the exact patch carried by the release commit. It proves both what changed and what did not change.

````diff
diff --git a/Talk-Leee/src/app/admin/reviews/page.tsx b/Talk-Leee/src/app/admin/reviews/page.tsx
index 49f9726f..80d5ec7d 100644
--- a/Talk-Leee/src/app/admin/reviews/page.tsx
+++ b/Talk-Leee/src/app/admin/reviews/page.tsx
@@ -74,10 +74,14 @@ export default function ReviewsPage() {
     const options = useQuery({
         queryKey: ["reviewOptions"],
         queryFn: () => extendedApi.getReviewOptions(),
         staleTime: 5 * 60_000,
     });
+    const rewardBalance = useQuery({
+        queryKey: ["reviewRewardBalance"],
+        queryFn: () => extendedApi.getReviewRewardBalance(),
+    });
 
     // 403 here means the account can see calls but not the tenant-wide view.
     const forbidden = (list.error as { status?: number })?.status === 403;
 
     const reset = () => { setPromptVersion(""); setTag(""); setRatingMax(""); setPage(1); };
@@ -96,11 +100,11 @@ export default function ReviewsPage() {
                     </p>
                 </div>
             ) : (
                 <div className="space-y-6">
                     {/* headline numbers */}
-                    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
+                    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
                         <Stat label="Reviews" value={t?.reviews ?? 0} loading={summary.isLoading} />
                         <Stat
                             label="Average rating"
                             value={t?.avg_rating != null ? `${t.avg_rating} / 5` : "--"}
                             loading={summary.isLoading}
@@ -111,10 +115,26 @@ export default function ReviewsPage() {
                             hint="rated 1 or 2"
                             loading={summary.isLoading}
                             emphasise={(t?.low_rated ?? 0) > 0}
                         />
                         <Stat label="Calls reviewed" value={t?.calls_reviewed ?? 0} loading={summary.isLoading} />
+                        <Stat
+                            label="Your review points"
+                            value={rewardBalance.data?.entries
+                                ? rewardBalance.data.total_points
+                                : rewardBalance.data?.rewards_enabled
+                                    ? 0
+                                    : "Off"}
+                            hint={rewardBalance.isError
+                                ? "Ledger unavailable"
+                                : rewardBalance.data?.entries
+                                    ? `${rewardBalance.data.entries} verified ledger ${rewardBalance.data.entries === 1 ? "entry" : "entries"}`
+                                    : rewardBalance.data?.rewards_enabled
+                                        ? "No verified awards yet"
+                                        : "Reward awards are not enabled"}
+                            loading={rewardBalance.isLoading}
+                        />
                     </div>
 
                     {/* the Safe Improvement Loop's two questions */}
                     <div className="grid gap-4 lg:grid-cols-2">
                         <Card title="By prompt version" hint="Which revision is doing worse">
diff --git a/Talk-Leee/src/app/api/v1/[...path]/route.ts b/Talk-Leee/src/app/api/v1/[...path]/route.ts
index 56e7d27f..0885d8ff 100644
--- a/Talk-Leee/src/app/api/v1/[...path]/route.ts
+++ b/Talk-Leee/src/app/api/v1/[...path]/route.ts
@@ -1933,10 +1933,11 @@ async function handleInner(request: Request, segments: string[], state: { cached
             items: [
                 { type: "calendar", status: "disconnected", last_sync: null, error_message: null, provider: null },
                 { type: "email", status: "disconnected", last_sync: null, error_message: null, provider: null },
                 { type: "crm", status: "disconnected", last_sync: null, error_message: null, provider: null },
                 { type: "drive", status: "disconnected", last_sync: null, error_message: null, provider: null },
+                { type: "salesforce", status: "disconnected", last_sync: null, error_message: null, provider: null },
             ],
         });
     }
 
     {
diff --git a/Talk-Leee/src/app/calls/[id]/page.tsx b/Talk-Leee/src/app/calls/[id]/page.tsx
index e6f5c77b..144afbde 100644
--- a/Talk-Leee/src/app/calls/[id]/page.tsx
+++ b/Talk-Leee/src/app/calls/[id]/page.tsx
@@ -378,10 +378,11 @@ export default function CallDetailPage() {
                             transition={{ delay: 0.35 }}
                         >
                             <LeadDetailsPanel
                                 callId={callId}
                                 campaignId={call?.campaign_id ?? undefined}
+                                leadOutcome={call?.lead_outcome}
                             />
                         </motion.div>
                     </div>
 
                     {/* Transcript */}
diff --git a/Talk-Leee/src/app/campaigns/[id]/edit/page.tsx b/Talk-Leee/src/app/campaigns/[id]/edit/page.tsx
index f0d15e8c..c2cd672f 100644
--- a/Talk-Leee/src/app/campaigns/[id]/edit/page.tsx
+++ b/Talk-Leee/src/app/campaigns/[id]/edit/page.tsx
@@ -16,10 +16,11 @@ import { DashboardLayout } from "@/components/layout/dashboard-layout";
 import {
     CampaignForm,
     type CampaignFormInitial,
 } from "@/components/campaigns/campaign-form";
 import { CampaignBasicsEditor } from "@/components/campaigns/campaign-basics-editor";
+import { CampaignLeadFieldsManager } from "@/components/campaigns/campaign-lead-fields";
 import { dashboardApi, type PersonaType, type CampaignCallingSchedule } from "@/lib/dashboard-api";
 import { ArrowLeft, RefreshCw } from "lucide-react";
 import { motion } from "framer-motion";
 
 /**
@@ -149,25 +150,28 @@ export default function EditCampaignPage() {
                         {error}
                     </div>
                 </div>
             ) : initial ? (
                 knowledgeDriven ? (
-                    <CampaignBasicsEditor
-                        campaignId={campaignId}
-                        initial={{
-                            name: initial.name,
-                            description: initial.description,
-                            companyName: initial.company_name,
-                            personaType: initial.persona_type,
-                            agentNames: initial.agent_names,
-                            agentNameGenders: initial.agent_name_genders,
-                            voiceId: initial.voice_id,
-                            ttsProvider,
-                            goal: initial.goal,
-                            callingSchedule,
-                        }}
-                    />
+                    <div className="space-y-6">
+                        <CampaignBasicsEditor
+                            campaignId={campaignId}
+                            initial={{
+                                name: initial.name,
+                                description: initial.description,
+                                companyName: initial.company_name,
+                                personaType: initial.persona_type,
+                                agentNames: initial.agent_names,
+                                agentNameGenders: initial.agent_name_genders,
+                                voiceId: initial.voice_id,
+                                ttsProvider,
+                                goal: initial.goal,
+                                callingSchedule,
+                            }}
+                        />
+                        <CampaignLeadFieldsManager campaignId={campaignId} />
+                    </div>
                 ) : (
                     <CampaignForm mode="edit" campaignId={campaignId} initialData={initial} />
                 )
             ) : null}
         </DashboardLayout>
diff --git a/Talk-Leee/src/app/connectors/page.tsx b/Talk-Leee/src/app/connectors/page.tsx
index 171b3882..d2ab6fd0 100644
--- a/Talk-Leee/src/app/connectors/page.tsx
+++ b/Talk-Leee/src/app/connectors/page.tsx
@@ -8,20 +8,21 @@ import { Button } from "@/components/ui/button";
 import { ConnectorCard } from "@/components/connectors/connector-card";
 import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
 import { useConnectorStatuses, queryKeys } from "@/lib/api-hooks";
 import { isApiClientError } from "@/lib/http-client";
 import type { ConnectorProviderStatus } from "@/lib/models";
+import type { ConnectorProviderType } from "@/lib/connectors-utils";
 import { notificationsStore } from "@/lib/notifications";
 import { cn } from "@/lib/utils";
-import { CalendarDays, Mail, UsersRound, HardDrive } from "lucide-react";
+import { CalendarDays, Cloud, Mail, UsersRound, HardDrive } from "lucide-react";
 
 function formatError(err: unknown) {
     if (isApiClientError(err)) return err.message;
     return err instanceof Error ? err.message : "Request failed";
 }
 
-type ProviderType = "calendar" | "email" | "crm" | "drive";
+type ProviderType = ConnectorProviderType;
 
 type ProviderCard = {
     type: ProviderType;
     name: string;
     description: string;
@@ -56,10 +57,17 @@ const PROVIDERS: ProviderCard[] = [
         name: "Google Drive",
         description: "Connect file storage for documents and recordings.",
         accent: "border-amber-500/20 bg-gradient-to-br from-amber-500/10 to-orange-500/5",
         icon: HardDrive,
     },
+    {
+        type: "salesforce",
+        name: "Salesforce",
+        description: "Send approved qualified and interested leads to Salesforce with tenant-scoped OAuth.",
+        accent: "border-blue-500/20 bg-gradient-to-br from-blue-500/10 to-cyan-500/5",
+        icon: Cloud,
+    },
 ];
 
 export default function ConnectorsPage() {
     const qc = useQueryClient();
     const router = useRouter();
@@ -142,11 +150,11 @@ export default function ConnectorsPage() {
     }, [qc]);
 
     const byType = useMemo(() => {
         const map = new Map<ProviderType, ConnectorProviderStatus>();
         for (const item of q.data?.items ?? []) {
-            if (item.type === "calendar" || item.type === "email" || item.type === "crm" || item.type === "drive") {
+            if (item.type === "calendar" || item.type === "email" || item.type === "crm" || item.type === "drive" || item.type === "salesforce") {
                 map.set(item.type, item);
             }
         }
         return map;
     }, [q.data?.items]);
@@ -155,11 +163,11 @@ export default function ConnectorsPage() {
         const raw = searchParams.get("required") ?? "";
         const list = raw
             .split(",")
             .map((x) => x.trim())
             .filter(Boolean);
-        const allowed: ProviderType[] = ["calendar", "email", "crm", "drive"];
+        const allowed: ProviderType[] = ["calendar", "email", "crm", "drive", "salesforce"];
         return list.filter((x): x is ProviderType => (allowed as string[]).includes(x));
     }, [searchParams]);
 
     const next = useMemo(() => searchParams.get("next") ?? "", [searchParams]);
 
@@ -220,10 +228,14 @@ export default function ConnectorsPage() {
                                         status={status}
                                         lastSync={data?.last_sync}
                                         provider={data?.provider}
                                         errorMessage={data?.error_message}
                                         oauthCallbackPath={`/connectors/${p.type}/callback`}
+                                        available={p.type !== "salesforce" || Boolean(data)}
+                                        unavailableReason={p.type === "salesforce" && !data
+                                            ? "Salesforce is not enabled by this server yet. No OAuth request will be started until the backend advertises the capability."
+                                            : undefined}
                                     />
                                 );
                             })}
                         </div>
                     </CardContent>
diff --git a/Talk-Leee/src/app/contacts/page.tsx b/Talk-Leee/src/app/contacts/page.tsx
index 8da79186..d215bffb 100644
--- a/Talk-Leee/src/app/contacts/page.tsx
+++ b/Talk-Leee/src/app/contacts/page.tsx
@@ -4,14 +4,15 @@ import { useState, useEffect, useRef, useMemo, useCallback } from "react";
 import { DashboardLayout } from "@/components/layout/dashboard-layout";
 import { Button } from "@/components/ui/button";
 import { Select } from "@/components/ui/select";
 import { Input } from "@/components/ui/input";
 import { Label } from "@/components/ui/label";
-import { dashboardApi, Campaign, Contact, ContactMutation } from "@/lib/dashboard-api";
+import { dashboardApi, Campaign, Contact } from "@/lib/dashboard-api";
 import { extendedApi, BulkImportResponse } from "@/lib/extended-api";
 import { sharedHttpClient } from "@/lib/api";
 import { parseContactsCsv } from "@/lib/contact-csv";
+import { contactPayload, EMPTY_CONTACT_FORM, type ContactFormState } from "@/lib/contact-form";
 import { ContactLists } from "@/components/campaigns/contact-lists";
 import { CsvImportMapper } from "@/components/contacts/csv-import-mapper";
 import { Upload, FileText, CheckCircle, AlertCircle, Loader2, Download, X, Search, Plus, Pencil, Trash2, ChevronDown } from "lucide-react";
 import { motion } from "framer-motion";
 import Link from "next/link";
@@ -117,55 +118,10 @@ type ImportFieldError = {
     field?: string | null;
     value?: string | null;
     error: string;
 };
 
-type ContactFormState = {
-    phone_number: string;
-    first_name: string;
-    last_name: string;
-    mobile_number: string;
-    business_number: string;
-    email: string;
-    company_name: string;
-    best_time_to_call: string;
-    timezone: string;
-    calling_notes: string;
-};
-
-const EMPTY_CONTACT_FORM: ContactFormState = {
-    phone_number: "",
-    first_name: "",
-    last_name: "",
-    mobile_number: "",
-    business_number: "",
-    email: "",
-    company_name: "",
-    best_time_to_call: "",
-    timezone: "",
-    calling_notes: "",
-};
-
-function contactPayload(form: ContactFormState): ContactMutation {
-    const firstName = form.first_name.trim();
-    const lastName = form.last_name.trim();
-    const fullName = [firstName, lastName].filter(Boolean).join(" ");
-    return {
-        phone_number: form.phone_number.trim(),
-        mobile_number: form.mobile_number.trim(),
-        full_name: fullName,
-        first_name: firstName || undefined,
-        last_name: lastName || undefined,
-        email: form.email.trim(),
-        company_name: form.company_name.trim(),
-        business_number: form.business_number.trim(),
-        best_time_to_call: form.best_time_to_call.trim(),
-        timezone: form.timezone.trim(),
-        calling_notes: form.calling_notes.trim(),
-    };
-}
-
 export default function ContactsPage() {
     const [campaigns, setCampaigns] = useState<Campaign[]>([]);
     const [selectedCampaign, setSelectedCampaign] = useState<string>("");
     const [file, setFile] = useState<File | null>(null);
     const [parsed, setParsed] = useState<ParseSummary | null>(null);
@@ -322,21 +278,27 @@ export default function ContactsPage() {
             last_name: contact.last_name || fallbackName.slice(1).join(" ") || "",
             mobile_number: mobileNumber,
             business_number: contact.business_number || "",
             email: contact.email || "",
             company_name: contact.company_name || "",
+            job_title: contact.job_title || "",
             best_time_to_call: contact.best_time_to_call || "",
             timezone: contact.timezone || "",
             calling_notes: contact.calling_notes || "",
+            preferred_contact_method: contact.preferred_contact_method || "",
+            do_not_call: Boolean(contact.do_not_call),
         });
         setShowContactDetails(Boolean(
             mobileNumber
             || contact.business_number
             || contact.company_name
+            || contact.job_title
             || contact.best_time_to_call
             || contact.timezone
             || contact.calling_notes
+            || contact.preferred_contact_method
+            || contact.do_not_call
         ));
         setShowAddContact(true);
     }
 
     async function handleDeleteContact(contactId: string, phone: string) {
@@ -896,10 +858,20 @@ export default function ContactsPage() {
                                 value={contactForm.company_name}
                                 onChange={(event) => setContactForm((previous) => ({ ...previous, company_name: event.target.value }))}
                                 placeholder="Acme Roofing"
                             />
                         </div>
+                        <div>
+                            <Label htmlFor="contact-job-title">Job Title or Role</Label>
+                            <Input
+                                id="contact-job-title"
+                                autoComplete="organization-title"
+                                value={contactForm.job_title}
+                                onChange={(event) => setContactForm((previous) => ({ ...previous, job_title: event.target.value }))}
+                                placeholder="Operations Manager"
+                            />
+                        </div>
                         <div>
                             <Label htmlFor="contact-best-time">Best Time to Call</Label>
                             <Input
                                 id="contact-best-time"
                                 value={contactForm.best_time_to_call}
@@ -914,20 +886,47 @@ export default function ContactsPage() {
                                 value={contactForm.timezone}
                                 onChange={(event) => setContactForm((previous) => ({ ...previous, timezone: event.target.value }))}
                                 placeholder="America/New_York"
                             />
                         </div>
+                        <div>
+                            <Label htmlFor="contact-preferred-method">Preferred Contact Method</Label>
+                            <select
+                                id="contact-preferred-method"
+                                value={contactForm.preferred_contact_method}
+                                onChange={(event) => setContactForm((previous) => ({ ...previous, preferred_contact_method: event.target.value }))}
+                                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
+                            >
+                                <option value="">No preference</option>
+                                <option value="phone">Phone</option>
+                                <option value="email">Email</option>
+                                <option value="sms">SMS</option>
+                                <option value="whatsapp">WhatsApp</option>
+                            </select>
+                        </div>
                         <div className="sm:col-span-2 lg:col-span-3">
                             <Label htmlFor="contact-calling-notes">Calling Notes</Label>
                             <textarea
                                 id="contact-calling-notes"
                                 value={contactForm.calling_notes}
                                 onChange={(event) => setContactForm((previous) => ({ ...previous, calling_notes: event.target.value }))}
                                 placeholder="Context the agent should know before calling"
                                 className="flex min-h-20 w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground ring-offset-background transition-[background-color,border-color,box-shadow] placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                             />
                         </div>
+                        <label className="flex items-start gap-3 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 sm:col-span-2 lg:col-span-3">
+                            <input
+                                type="checkbox"
+                                checked={contactForm.do_not_call}
+                                onChange={(event) => setContactForm((previous) => ({ ...previous, do_not_call: event.target.checked }))}
+                                className="mt-0.5 h-4 w-4 rounded border-input accent-amber-600"
+                            />
+                            <span>
+                                <span className="block text-sm font-medium text-foreground">Do not call this contact</span>
+                                <span className="block text-xs text-muted-foreground">This is an operational suppression flag. The dialer will exclude the contact; the agent will never be asked to discuss it.</span>
+                            </span>
+                        </label>
                     </div>
                 )}
             </div>
 
             <div className="flex flex-wrap gap-2">
@@ -1094,14 +1093,25 @@ export default function ContactsPage() {
                                     return (
                                         <article key={contact.id} className={`rounded-xl border border-border p-4 ${contact.is_lead ? "bg-emerald-500/5" : "bg-background/40"}`}>
                                             <div className="flex items-start justify-between gap-3">
                                                 <div className="min-w-0">
                                                     <p className="break-words text-sm font-semibold tabular-nums text-foreground">{contact.phone_number}</p>
-                                                    <p className="mt-1 truncate text-sm text-muted-foreground">{displayName}</p>
-                                                    <p className="truncate text-xs text-muted-foreground">{contact.email || "No email"}</p>
-                                                </div>
-                                                {contact.is_lead ? (
+                                                     <p className="mt-1 truncate text-sm text-muted-foreground">{displayName}</p>
+                                                     <p className="truncate text-xs text-muted-foreground">
+                                                         {[contact.job_title, contact.company_name].filter(Boolean).join(" · ") || contact.email || "No company or email"}
+                                                     </p>
+                                                     {(contact.best_time_to_call || contact.timezone || contact.preferred_contact_method) && (
+                                                         <p className="mt-1 text-xs text-muted-foreground">
+                                                             {[contact.best_time_to_call, contact.timezone, contact.preferred_contact_method].filter(Boolean).join(" · ")}
+                                                         </p>
+                                                     )}
+                                                 </div>
+                                                 {contact.do_not_call ? (
+                                                     <span className="shrink-0 rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-700 dark:text-amber-300">
+                                                         Do not call
+                                                     </span>
+                                                 ) : contact.is_lead ? (
                                                     <span className="shrink-0 rounded-full border border-emerald-500/20 bg-emerald-500/10 px-2 py-0.5 text-xs font-medium text-emerald-700 dark:text-emerald-400">
                                                         Lead — follow up
                                                     </span>
                                                 ) : (
                                                     <span className="shrink-0 rounded-full border border-border bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
@@ -1134,29 +1144,41 @@ export default function ContactsPage() {
                             </div>
                             <div className="hidden overflow-x-auto md:block">
                                 <table className="w-full">
                                     <thead className="border-b border-border">
                                         <tr>
-                                            <th className="whitespace-nowrap px-4 py-2 text-left text-xs font-medium uppercase text-muted-foreground">Phone</th>
-                                            <th className="whitespace-nowrap px-4 py-2 text-left text-xs font-medium uppercase text-muted-foreground">Name</th>
-                                            <th className="whitespace-nowrap px-4 py-2 text-left text-xs font-medium uppercase text-muted-foreground">Email</th>
+                                             <th className="whitespace-nowrap px-4 py-2 text-left text-xs font-medium uppercase text-muted-foreground">Contact</th>
+                                             <th className="whitespace-nowrap px-4 py-2 text-left text-xs font-medium uppercase text-muted-foreground">Company / role</th>
+                                             <th className="whitespace-nowrap px-4 py-2 text-left text-xs font-medium uppercase text-muted-foreground">Call window</th>
                                             <th className="whitespace-nowrap px-4 py-2 text-left text-xs font-medium uppercase text-muted-foreground">Status</th>
                                             <th className="whitespace-nowrap px-4 py-2 text-right text-xs font-medium uppercase text-muted-foreground">Actions</th>
                                         </tr>
                                     </thead>
                                     <tbody className="divide-y divide-border/60">
                                         {contacts.map((contact) => (
                                             <tr key={contact.id} className={`transition-colors hover:bg-muted/30 ${contact.is_lead ? "bg-green-500/5" : ""}`}>
-                                                <td className="whitespace-nowrap px-4 py-3 text-sm tabular-nums text-foreground">{contact.phone_number}</td>
-                                                <td className="whitespace-nowrap px-4 py-3 text-sm text-muted-foreground">
-                                                    {contact.first_name || contact.last_name
-                                                        ? `${contact.first_name || ""} ${contact.last_name || ""}`.trim()
-                                                        : contact.full_name?.trim() || "--"}
-                                                </td>
-                                                <td className="whitespace-nowrap px-4 py-3 text-sm text-muted-foreground">{contact.email || "--"}</td>
-                                                <td className="whitespace-nowrap px-4 py-3 text-sm">
-                                                    {contact.is_lead ? (
+                                                 <td className="px-4 py-3 text-sm">
+                                                     <p className="font-medium text-foreground">{contact.first_name || contact.last_name
+                                                         ? `${contact.first_name || ""} ${contact.last_name || ""}`.trim()
+                                                         : contact.full_name?.trim() || "Name unavailable"}</p>
+                                                     <p className="whitespace-nowrap text-xs tabular-nums text-muted-foreground">{contact.phone_number}</p>
+                                                     {contact.email ? <p className="max-w-56 truncate text-xs text-muted-foreground">{contact.email}</p> : null}
+                                                 </td>
+                                                 <td className="px-4 py-3 text-sm text-muted-foreground">
+                                                     <p>{contact.company_name || "--"}</p>
+                                                     {contact.job_title ? <p className="text-xs">{contact.job_title}</p> : null}
+                                                 </td>
+                                                 <td className="px-4 py-3 text-sm text-muted-foreground">
+                                                     <p>{contact.best_time_to_call || "Any time"}</p>
+                                                     <p className="text-xs">{[contact.timezone, contact.preferred_contact_method].filter(Boolean).join(" · ") || "No preference"}</p>
+                                                 </td>
+                                                 <td className="whitespace-nowrap px-4 py-3 text-sm">
+                                                     {contact.do_not_call ? (
+                                                         <span className="w-fit rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-700 dark:text-amber-300">
+                                                             Do not call
+                                                         </span>
+                                                     ) : contact.is_lead ? (
                                                         <span className="w-fit rounded-full border border-emerald-500/20 bg-emerald-500/10 px-2 py-0.5 text-xs font-medium text-emerald-700 dark:text-emerald-400">
                                                             Lead — follow up
                                                         </span>
                                                     ) : (
                                                         <span className="rounded-full border border-border bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
diff --git a/Talk-Leee/src/components/calls/CallSummaryCard.test.tsx b/Talk-Leee/src/components/calls/CallSummaryCard.test.tsx
new file mode 100644
index 00000000..2b7c1619
--- /dev/null
+++ b/Talk-Leee/src/components/calls/CallSummaryCard.test.tsx
@@ -0,0 +1,43 @@
+import assert from "node:assert/strict";
+import test from "node:test";
+
+import { cleanup, render, screen } from "@testing-library/react";
+
+import { CallSummaryCard, summaryNeedsReview } from "@/components/calls/CallSummaryCard";
+import type { CallSummaryObj } from "@/lib/dashboard-api";
+
+function summary(patch: Partial<CallSummaryObj> = {}): CallSummaryObj {
+    return {
+        headline: "Call recap",
+        outcome: "answered",
+        what_happened: "The caller asked about the service.",
+        key_points: [],
+        objections: [],
+        commitments: [],
+        action_items: [],
+        sentiment: "neutral",
+        next_step: "",
+        notable_quotes: [],
+        ...patch,
+    };
+}
+
+test.afterEach(cleanup);
+
+test("actionable AI classifications receive a visible needs-review signal", () => {
+    const value = summary({ outcome: "qualified", qualification_status: "qualified" });
+    assert.equal(summaryNeedsReview(value), true);
+
+    render(<CallSummaryCard isLoading={false} isError={false} data={{ available: true, summary: value }} />);
+    assert.ok(screen.getByText("Needs review"));
+    assert.ok(screen.getByRole("button", { name: /why this summary needs review/i }));
+});
+
+test("a summary with no actionable classification does not invent confidence", () => {
+    const value = summary();
+    assert.equal(summaryNeedsReview(value), false);
+
+    render(<CallSummaryCard isLoading={false} isError={false} data={{ available: true, summary: value }} />);
+    assert.equal(screen.queryByText("Needs review"), null);
+    assert.doesNotMatch(document.body.textContent ?? "", /\d+% confidence/i);
+});
diff --git a/Talk-Leee/src/components/calls/CallSummaryCard.tsx b/Talk-Leee/src/components/calls/CallSummaryCard.tsx
index 03e6331e..86a58bb7 100644
--- a/Talk-Leee/src/components/calls/CallSummaryCard.tsx
+++ b/Talk-Leee/src/components/calls/CallSummaryCard.tsx
@@ -1,8 +1,8 @@
 "use client";
 
-import { Loader2, AlertCircle, TrendingUp, TrendingDown, Minus, Sparkles } from "lucide-react";
+import { Loader2, AlertCircle, TrendingUp, TrendingDown, Minus, Sparkles, ShieldAlert } from "lucide-react";
 import type { CallSummaryObj, CallSummaryEnvelope } from "@/lib/dashboard-api";
 import { InfoTip } from "@/components/ui/info-tip";
 
 // ---------------------------------------------------------------------------
 // Outcome chip
@@ -56,10 +56,37 @@ function BulletList({ items }: { items: string[] }) {
 function knownSummaryValue(value?: string): value is string {
     const normalized = value?.trim().toLowerCase();
     return Boolean(normalized && normalized !== "unknown" && normalized !== "none");
 }
 
+/**
+ * Show a review signal when the model has produced a classification that could
+ * cause a human or CRM workflow to act. The API does not return calibrated
+ * confidence, so the UI must not invent a percentage.
+ */
+export function summaryNeedsReview(summary: CallSummaryObj): boolean {
+    const verdicts = [summary.outcome, summary.qualification_status]
+        .filter((value): value is string => Boolean(value?.trim()))
+        .map((value) => value.trim().toLowerCase().replace(/[\s-]+/g, "_"));
+    const actionable = verdicts.some((value) => (
+        value.includes("qualified")
+        || value.includes("interested")
+        || value.includes("callback")
+        || value.includes("nurture")
+        || value.includes("achieved")
+        || value.includes("positive")
+        || value.includes("success")
+    ));
+    const hasQualificationEvidence = [
+        summary.identified_need,
+        summary.decision_maker_status,
+        summary.timeline,
+        summary.budget_information,
+    ].some(knownSummaryValue);
+    return actionable || hasQualificationEvidence;
+}
+
 // ---------------------------------------------------------------------------
 // Main card
 // ---------------------------------------------------------------------------
 
 function SummaryBody({ summary }: { summary: CallSummaryObj }) {
@@ -80,10 +107,11 @@ function SummaryBody({ summary }: { summary: CallSummaryObj }) {
         { label: "Budget", value: summary.budget_information },
     ].filter(
         (item): item is { label: string; value: string } => knownSummaryValue(item.value),
     );
     const hasQualification = Boolean(qualificationStatus || qualificationDetails.length > 0);
+    const needsReview = summaryNeedsReview(summary);
 
     return (
         <div className="space-y-4">
             {/* PROVENANCE, STATED ONCE AND IN THE PAGE (goals.md §8)
                 §8 asks the summary to distinguish transcript facts from AI
@@ -101,10 +129,25 @@ function SummaryBody({ summary }: { summary: CallSummaryObj }) {
                 </span>
             </p>
 
             {/* Header row: outcome chip + sentiment */}
             <div className="flex flex-wrap items-center gap-2">
+                {needsReview && (
+                    <span className="inline-flex items-center gap-1">
+                        <span className="inline-flex items-center gap-1 rounded-full border border-amber-500/40 bg-amber-500/10 px-2.5 py-0.5 text-xs font-semibold text-amber-700 dark:text-amber-300">
+                            <ShieldAlert className="h-3.5 w-3.5" aria-hidden />
+                            Needs review
+                        </span>
+                        <InfoTip label="Why this summary needs review">
+                            This summary contains an actionable AI classification or qualification
+                            detail. The API does not provide calibrated confidence, so Talk-Lee
+                            shows an honest review warning instead of inventing a percentage.
+                            Confirm it against the transcript or recording before follow-up or CRM
+                            synchronization.
+                        </InfoTip>
+                    </span>
+                )}
                 {summary.outcome && (
                     <span className="inline-flex items-center gap-1">
                         <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold ${outcomeColor(summary.outcome)}`}>
                             {summary.outcome.replace(/_/g, " ")}
                         </span>
diff --git a/Talk-Leee/src/components/calls/lead-details-panel.tsx b/Talk-Leee/src/components/calls/lead-details-panel.tsx
index 2162ed7e..ed84c5c4 100644
--- a/Talk-Leee/src/components/calls/lead-details-panel.tsx
+++ b/Talk-Leee/src/components/calls/lead-details-panel.tsx
@@ -41,10 +41,11 @@ import {
     SOURCE_LABEL,
     SOURCE_TONE,
     leadDetailsApi,
     type CapturedDetail,
 } from "@/lib/lead-details-api";
+import { leadInterestState } from "@/lib/lead-outcome";
 
 export function leadDetailsQueryKey(callId: string) {
     return ["leadDetails", callId] as const;
 }
 
@@ -158,27 +159,28 @@ function DetailRow({
 }
 
 export function LeadDetailsPanel({
     callId,
     campaignId,
+    leadOutcome,
 }: {
     callId: string;
     campaignId?: string;
+    leadOutcome?: string | null;
 }) {
     const query = useQuery({
         queryKey: leadDetailsQueryKey(callId),
         queryFn: () => leadDetailsApi.detailsForCall(callId, campaignId),
         enabled: Boolean(callId),
     });
 
     const details = query.data?.details ?? [];
     const missing = query.data?.missing_required ?? [];
 
-    // The badge §7 asks for. "Interested" is not a separate flag — it is what
-    // having captured anything MEANS: the agent only gets structured detail out
-    // of someone who engaged.
-    const interested = details.length > 0;
+    // The badge comes from the post-call verdict, never from "some fields were
+    // captured". A caller can give their name and still explicitly decline.
+    const interested = leadInterestState(leadOutcome) === "interested";
 
     const retry = useCallback(() => void query.refetch(), [query]);
 
     if (query.isLoading) {
         return (
diff --git a/Talk-Leee/src/components/campaigns/campaign-form.tsx b/Talk-Leee/src/components/campaigns/campaign-form.tsx
index b2eb7404..0788ea63 100644
--- a/Talk-Leee/src/components/campaigns/campaign-form.tsx
+++ b/Talk-Leee/src/components/campaigns/campaign-form.tsx
@@ -30,11 +30,16 @@ import {
     parseAgentNames,
     parseKvList,
     parseList,
 } from "@/lib/campaign-personas";
 import { conflictingNames, pruneGenders } from "@/components/campaigns/agent-name-gender";
+import {
+    CampaignLeadFieldsPicker,
+    useCampaignLeadFieldDraft,
+} from "@/components/campaigns/campaign-lead-fields";
 import { aiOptionsApi, AIProviderConfig, VoiceInfo } from "@/lib/ai-options-api";
+import { leadDetailsApi } from "@/lib/lead-details-api";
 import { captureException } from "@/lib/monitoring";
 import { ChevronDown, Loader2, Play, RefreshCw, Square, Volume2, Check } from "lucide-react";
 import { motion } from "framer-motion";
 
 export type CampaignFormMode = "create" | "edit";
@@ -79,10 +84,14 @@ export function CampaignForm({ mode, campaignId, initialData }: Props) {
     const isEdit = mode === "edit";
     const seed = initialData ?? EMPTY_INITIAL;
 
     const [submitting, setSubmitting] = useState(false);
     const [error, setError] = useState("");
+    // If campaign creation succeeds but saving lead-field policy fails, retry
+    // against the created id instead of creating a duplicate campaign.
+    const [createdCampaignId, setCreatedCampaignId] = useState<string | null>(null);
+    const leadFields = useCampaignLeadFieldDraft(isEdit ? campaignId : undefined);
     const [voices, setVoices] = useState<VoiceInfo[]>([]);
     const [loadingVoices, setLoadingVoices] = useState(true);
     const [previewingVoiceId, setPreviewingVoiceId] = useState<string | null>(null);
     const [globalAiConfig, setGlobalAiConfig] = useState<AIProviderConfig | null>(null);
     const previewAudioRef = useRef<HTMLAudioElement | null>(null);
@@ -343,10 +352,14 @@ export function CampaignForm({ mode, campaignId, initialData }: Props) {
         ? voices.filter((voice) => voice.provider === globalAiConfig.tts_provider)
         : voices;
 
     async function handleSubmit(e: React.FormEvent) {
         e.preventDefault();
+        if (leadFields.isLoading || leadFields.isError) {
+            setError("Contact-field settings must load successfully before this campaign can be saved.");
+            return;
+        }
         if (!formData.voice_id) {
             setError("Select a voice from the active global TTS provider before saving the campaign.");
             return;
         }
         const agentNames = parseAgentNames(agentNamesRaw);
@@ -383,23 +396,33 @@ export function CampaignForm({ mode, campaignId, initialData }: Props) {
                 return Object.keys(kept).length > 0 ? kept : undefined;
             })(),
             campaign_slots: buildCampaignSlots(),
         };
 
+        let targetCampaignId = isEdit ? campaignId : createdCampaignId ?? undefined;
         try {
             if (isEdit) {
                 if (!campaignId) {
                     throw new Error("Internal error: edit mode without a campaignId.");
                 }
                 const result = await dashboardApi.updateCampaign(campaignId, payload);
-                router.push(`/campaigns/${result.campaign.id}`);
-            } else {
+                targetCampaignId = result.campaign.id;
+            } else if (!targetCampaignId) {
                 const result = await dashboardApi.createCampaign(payload);
-                router.push(`/campaigns/${result.campaign.id}`);
+                targetCampaignId = result.campaign.id;
+                setCreatedCampaignId(targetCampaignId);
             }
+
+            await leadDetailsApi.setCampaignFields(targetCampaignId!, leadFields.fields);
+            router.push(`/campaigns/${targetCampaignId}`);
         } catch (err) {
-            setError(err instanceof Error ? err.message : "Failed to save campaign");
+            const detail = err instanceof Error ? err.message : "Failed to save campaign";
+            setError(
+                targetCampaignId
+                    ? `Campaign details were saved, but its contact-field policy was not. Retry to finish without creating a duplicate. ${detail}`
+                    : detail,
+            );
         } finally {
             setSubmitting(false);
         }
     }
 
@@ -1040,18 +1063,30 @@ export function CampaignForm({ mode, campaignId, initialData }: Props) {
                         <p className="text-xs text-muted-foreground">
                             Layered on top of the generic guardrails and the persona you picked. Optional but recommended for campaign-specific callouts.
                         </p>
                     </div>
 
+                    <div className="border-t border-border pt-6">
+                        <CampaignLeadFieldsPicker
+                            specs={leadFields.specs}
+                            value={leadFields.fields}
+                            onChange={leadFields.setFields}
+                            isLoading={leadFields.isLoading}
+                            error={leadFields.isError ? leadFields.error : undefined}
+                            onRetry={leadFields.retry}
+                            disabled={submitting}
+                        />
+                    </div>
+
                     {error && (
                         <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/30 rounded-lg p-3">
                             {error}
                         </div>
                     )}
 
                     <div className="flex gap-4">
-                        <Button type="submit" disabled={submitting || !formData.voice_id}>
+                        <Button type="submit" disabled={submitting || leadFields.isLoading || leadFields.isError || !formData.voice_id}>
                             {submitting ? (
                                 <>
                                     <Loader2 className="w-4 h-4 animate-spin" />
                                     {submittingLabel}
                                 </>
diff --git a/Talk-Leee/src/components/campaigns/campaign-lead-fields.test.ts b/Talk-Leee/src/components/campaigns/campaign-lead-fields.test.ts
new file mode 100644
index 00000000..63864c9d
--- /dev/null
+++ b/Talk-Leee/src/components/campaigns/campaign-lead-fields.test.ts
@@ -0,0 +1,67 @@
+import assert from "node:assert/strict";
+import test, { afterEach } from "node:test";
+import { createElement } from "react";
+import { cleanup, screen } from "@testing-library/react";
+
+import { CampaignLeadFieldsPicker, defaultCampaignLeadFields } from "@/components/campaigns/campaign-lead-fields";
+import type { CampaignLeadField, ContactFieldSpec } from "@/lib/lead-details-api";
+import { ensureDom } from "@/test-utils/dom";
+
+ensureDom();
+afterEach(cleanup);
+
+function spec(key: string, agentUsable = true): ContactFieldSpec {
+    return {
+        key,
+        label: key.replace(/_/g, " "),
+        field_type: "text",
+        aliases: [],
+        agent_usable: agentUsable,
+        max_len: 255,
+    };
+}
+
+test("new campaign defaults include useful conversational fields only", () => {
+    const fields = defaultCampaignLeadFields([
+        spec("email"),
+        spec("company_name"),
+        spec("job_title"),
+        spec("best_time_to_call"),
+        spec("calling_notes"),
+        spec("timezone", false),
+        spec("do_not_call", false),
+    ]);
+
+    assert.deepEqual(fields.map((field) => field.field_key), [
+        "email",
+        "company_name",
+        "job_title",
+        "best_time_to_call",
+        "calling_notes",
+    ]);
+    assert.equal(fields.every((field) => field.agent_visible && field.user_visible), true);
+    assert.equal(fields.every((field) => !field.is_required), true);
+});
+
+test("the picker makes field access and requiredness explicit", async () => {
+    const userEvent = (await import("@testing-library/user-event")).default;
+    const user = userEvent.setup({ document: globalThis.document });
+    const specs = [spec("email"), spec("company_name"), spec("timezone", false)];
+    let value: CampaignLeadField[] = [];
+
+    const view = () => createElement(CampaignLeadFieldsPicker, {
+        specs,
+        value,
+        onChange: (next: CampaignLeadField[]) => { value = next; },
+    });
+    const rendered = (await import("@testing-library/react")).render(view());
+
+    await user.click(screen.getByRole("checkbox", { name: /email/i }));
+    rendered.rerender(view());
+    await user.click(screen.getByRole("checkbox", { name: /required for this campaign/i }));
+
+    assert.equal(value.length, 1);
+    assert.equal(value[0]?.field_key, "email");
+    assert.equal(value[0]?.is_required, true);
+    assert.equal(screen.queryByRole("checkbox", { name: /timezone/i }), null);
+});
diff --git a/Talk-Leee/src/components/campaigns/campaign-lead-fields.tsx b/Talk-Leee/src/components/campaigns/campaign-lead-fields.tsx
new file mode 100644
index 00000000..c5f6858b
--- /dev/null
+++ b/Talk-Leee/src/components/campaigns/campaign-lead-fields.tsx
@@ -0,0 +1,234 @@
+"use client";
+
+import { useMemo, useState } from "react";
+import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
+import { AlertCircle, Check, Loader2, RefreshCw, Sparkles } from "lucide-react";
+
+import { Button } from "@/components/ui/button";
+import {
+    leadDetailsApi,
+    type CampaignLeadField,
+    type ContactFieldSpec,
+} from "@/lib/lead-details-api";
+
+const DEFAULT_CAPTURE_KEYS = new Set([
+    "email",
+    "company_name",
+    "job_title",
+    "best_time_to_call",
+    "calling_notes",
+]);
+
+export function defaultCampaignLeadFields(specs: ContactFieldSpec[]): CampaignLeadField[] {
+    return specs
+        .filter((field) => field.agent_usable && DEFAULT_CAPTURE_KEYS.has(field.key))
+        .map((field, index) => ({
+            field_key: field.key,
+            label: field.label,
+            field_type: field.field_type,
+            is_required: false,
+            agent_visible: true,
+            user_visible: true,
+            options: null,
+            sort_order: index,
+        }));
+}
+
+export function useCampaignLeadFieldDraft(campaignId?: string) {
+    const [draft, setDraft] = useState<CampaignLeadField[] | null>(null);
+    const specQuery = useQuery({
+        queryKey: ["contactFieldSpec"],
+        queryFn: () => leadDetailsApi.fieldSpec(),
+        staleTime: 10 * 60_000,
+    });
+    const savedQuery = useQuery({
+        queryKey: ["campaignLeadFields", campaignId],
+        queryFn: () => leadDetailsApi.campaignFields(campaignId!),
+        enabled: Boolean(campaignId),
+    });
+
+    const initial = useMemo(() => {
+        if (campaignId) return savedQuery.data?.fields ?? [];
+        return specQuery.data ? defaultCampaignLeadFields(specQuery.data.fields) : [];
+    }, [campaignId, savedQuery.data, specQuery.data]);
+
+    return {
+        specs: specQuery.data?.fields ?? [],
+        fields: draft ?? initial,
+        setFields: setDraft,
+        isLoading: specQuery.isLoading || (Boolean(campaignId) && savedQuery.isLoading),
+        isError: specQuery.isError || savedQuery.isError,
+        error: specQuery.error ?? savedQuery.error,
+        retry: () => {
+            void specQuery.refetch();
+            if (campaignId) void savedQuery.refetch();
+        },
+    };
+}
+
+export function CampaignLeadFieldsPicker({
+    specs,
+    value,
+    onChange,
+    isLoading = false,
+    error,
+    onRetry,
+    disabled = false,
+}: {
+    specs: ContactFieldSpec[];
+    value: CampaignLeadField[];
+    onChange: (fields: CampaignLeadField[]) => void;
+    isLoading?: boolean;
+    error?: unknown;
+    onRetry?: () => void;
+    disabled?: boolean;
+}) {
+    const usable = specs.filter((field) => field.agent_usable && field.key !== "full_name");
+    const selected = new Map(value.map((field) => [field.field_key, field]));
+
+    function normalized(fields: CampaignLeadField[]): CampaignLeadField[] {
+        const byKey = new Map(fields.map((field) => [field.field_key, field]));
+        return usable
+            .filter((field) => byKey.has(field.key))
+            .map((field, index) => ({ ...byKey.get(field.key)!, sort_order: index }));
+    }
+
+    function toggle(field: ContactFieldSpec, checked: boolean) {
+        if (checked) {
+            onChange(normalized([
+                ...value,
+                {
+                    field_key: field.key,
+                    label: field.label,
+                    field_type: field.field_type,
+                    is_required: false,
+                    agent_visible: true,
+                    user_visible: true,
+                    options: null,
+                    sort_order: value.length,
+                },
+            ]));
+        } else {
+            onChange(normalized(value.filter((item) => item.field_key !== field.key)));
+        }
+    }
+
+    function setRequired(fieldKey: string, required: boolean) {
+        onChange(value.map((field) => (
+            field.field_key === fieldKey ? { ...field, is_required: required } : field
+        )));
+    }
+
+    return (
+        <section className="space-y-4" aria-labelledby="campaign-lead-fields-heading">
+            <div className="flex flex-wrap items-start justify-between gap-3">
+                <div>
+                    <h2 id="campaign-lead-fields-heading" className="flex items-center gap-2 text-base font-semibold text-foreground">
+                        <Sparkles className="h-4 w-4 text-emerald-500" aria-hidden />
+                        Contact details the agent may capture
+                    </h2>
+                    <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
+                        Choose only information this campaign needs. “Required” means a missing
+                        answer remains visibly incomplete; the agent must never invent it.
+                    </p>
+                </div>
+                {!isLoading && !error ? (
+                    <span className="rounded-full border border-border bg-muted px-2.5 py-1 text-xs text-muted-foreground">
+                        {value.length} selected
+                    </span>
+                ) : null}
+            </div>
+
+            {isLoading ? (
+                <div className="flex items-center gap-2 rounded-xl border border-border bg-muted/30 p-4 text-sm text-muted-foreground" role="status">
+                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Loading contact fields…
+                </div>
+            ) : error ? (
+                <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4" role="alert">
+                    <p className="flex items-center gap-2 text-sm text-destructive">
+                        <AlertCircle className="h-4 w-4" aria-hidden />
+                        Contact-field settings could not be loaded. Campaign saving is blocked so the agent cannot receive an accidental field set.
+                    </p>
+                    {onRetry ? <Button type="button" variant="outline" size="sm" onClick={onRetry}><RefreshCw className="h-4 w-4" aria-hidden /> Retry</Button> : null}
+                </div>
+            ) : (
+                <div className="grid gap-3 sm:grid-cols-2">
+                    {usable.map((field) => {
+                        const current = selected.get(field.key);
+                        return (
+                            <div key={field.key} className={`rounded-xl border p-3 transition-colors ${current ? "border-emerald-500/35 bg-emerald-500/5" : "border-border bg-background/40"}`}>
+                                <label className="flex cursor-pointer items-start gap-3">
+                                    <input
+                                        type="checkbox"
+                                        checked={Boolean(current)}
+                                        disabled={disabled}
+                                        onChange={(event) => toggle(field, event.target.checked)}
+                                        className="mt-0.5 h-4 w-4 rounded border-input accent-emerald-600"
+                                    />
+                                    <span className="min-w-0">
+                                        <span className="block text-sm font-medium text-foreground">{field.label}</span>
+                                        <span className="block text-xs text-muted-foreground">{field.field_type.replace(/_/g, " ")}</span>
+                                    </span>
+                                </label>
+                                {current ? (
+                                    <label className="mt-3 flex cursor-pointer items-center gap-2 border-t border-border/60 pt-2 text-xs text-muted-foreground">
+                                        <input
+                                            type="checkbox"
+                                            checked={current.is_required}
+                                            disabled={disabled}
+                                            onChange={(event) => setRequired(field.key, event.target.checked)}
+                                            className="h-3.5 w-3.5 rounded border-input accent-amber-600"
+                                        />
+                                        Required for this campaign
+                                    </label>
+                                ) : null}
+                            </div>
+                        );
+                    })}
+                </div>
+            )}
+        </section>
+    );
+}
+
+export function CampaignLeadFieldsManager({ campaignId }: { campaignId: string }) {
+    const queryClient = useQueryClient();
+    const draft = useCampaignLeadFieldDraft(campaignId);
+    const save = useMutation({
+        mutationFn: () => leadDetailsApi.setCampaignFields(campaignId, draft.fields),
+        onSuccess: (response) => {
+            draft.setFields(response.fields);
+            queryClient.setQueryData(["campaignLeadFields", campaignId], response);
+        },
+    });
+
+    return (
+        <div className="content-card space-y-4">
+            <CampaignLeadFieldsPicker
+                specs={draft.specs}
+                value={draft.fields}
+                onChange={draft.setFields}
+                isLoading={draft.isLoading}
+                error={draft.isError ? draft.error : undefined}
+                onRetry={draft.retry}
+                disabled={save.isPending}
+            />
+            {save.isError ? (
+                <p className="text-sm text-destructive" role="alert">
+                    {save.error instanceof Error ? save.error.message : "The contact-field settings could not be saved."}
+                </p>
+            ) : null}
+            {save.isSuccess ? (
+                <p className="flex items-center gap-2 text-sm text-emerald-700 dark:text-emerald-300" role="status">
+                    <Check className="h-4 w-4" aria-hidden /> Contact-field settings saved.
+                </p>
+            ) : null}
+            <div className="flex justify-end">
+                <Button type="button" onClick={() => save.mutate()} disabled={draft.isLoading || draft.isError || save.isPending}>
+                    {save.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Check className="h-4 w-4" aria-hidden />}
+                    {save.isPending ? "Saving…" : "Save contact fields"}
+                </Button>
+            </div>
+        </div>
+    );
+}
diff --git a/Talk-Leee/src/components/campaigns/campaign-wizard.tsx b/Talk-Leee/src/components/campaigns/campaign-wizard.tsx
index 328b5bdd..7f0c86b0 100644
--- a/Talk-Leee/src/components/campaigns/campaign-wizard.tsx
+++ b/Talk-Leee/src/components/campaigns/campaign-wizard.tsx
@@ -24,10 +24,15 @@ import { dashboardApi, PersonaType, CampaignCallingSchedule } from "@/lib/dashbo
 import { api } from "@/lib/api";
 import { AgentNameGender, pruneGenders } from "@/components/campaigns/agent-name-gender";
 import { PERSONAS, parseAgentNames } from "@/lib/campaign-personas";
 import { VoiceProviderPicker } from "@/components/campaigns/voice-provider-picker";
 import { CallingScheduleEditor } from "@/components/campaigns/calling-schedule-editor";
+import {
+    CampaignLeadFieldsPicker,
+    useCampaignLeadFieldDraft,
+} from "@/components/campaigns/campaign-lead-fields";
+import { leadDetailsApi } from "@/lib/lead-details-api";
 
 const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
 const STEPS = ["Basics", "Knowledge", "Review"] as const;
 
 function fmtBytes(n: number): string {
@@ -61,10 +66,12 @@ export function CampaignWizard() {
 
     // Step 3 — preview + submit
     const [preview, setPreview] = useState<{ system_prompt: string; greeting: string } | null>(null);
     const [previewLoading, setPreviewLoading] = useState(false);
     const [submitting, setSubmitting] = useState(false);
+    const [createdCampaignId, setCreatedCampaignId] = useState<string | null>(null);
+    const leadFields = useCampaignLeadFieldDraft();
 
     const agentNames = useMemo(() => parseAgentNames(agentNamesRaw), [agentNamesRaw]);
     const basicsValid = name.trim() && companyName.trim() && agentNames.length >= 1 && voiceId;
 
     const onPickFile = (f: File | null) => {
@@ -103,39 +110,54 @@ export function CampaignWizard() {
             setPreviewLoading(false);
         }
     };
 
     const onCreate = async () => {
+        if (leadFields.isLoading || leadFields.isError) {
+            setError("Contact-field settings must load successfully before this campaign can be created.");
+            return;
+        }
         setSubmitting(true);
         setError(null);
+        let campaignId = createdCampaignId;
         try {
-            const { campaign } = await dashboardApi.createCampaign({
-                name: name.trim(),
-                description: undefined,
-                system_prompt: goal.trim(),      // additional instructions
-                voice_id: voiceId,
-                tts_provider: provider || undefined,   // per-campaign engine
-                goal: goal.trim() || undefined,
-                persona_type: personaType,
-                company_name: companyName.trim(),
-                agent_names: agentNames,
-                agent_name_genders: pruneGenders(agentGenders, agentNames),
-                campaign_slots: {},
-                knowledge_driven: true,
-                calling_schedule: schedule,
-            });
+            if (!campaignId) {
+                const { campaign } = await dashboardApi.createCampaign({
+                    name: name.trim(),
+                    description: undefined,
+                    system_prompt: goal.trim(),      // additional instructions
+                    voice_id: voiceId,
+                    tts_provider: provider || undefined,   // per-campaign engine
+                    goal: goal.trim() || undefined,
+                    persona_type: personaType,
+                    company_name: companyName.trim(),
+                    agent_names: agentNames,
+                    agent_name_genders: pruneGenders(agentGenders, agentNames),
+                    campaign_slots: {},
+                    knowledge_driven: true,
+                    calling_schedule: schedule,
+                });
+                campaignId = campaign.id;
+                setCreatedCampaignId(campaign.id);
+            }
+            await leadDetailsApi.setCampaignFields(campaignId, leadFields.fields);
             if (file) {
                 try {
-                    await api.uploadCampaignKnowledge(campaign.id, file);
+                    await api.uploadCampaignKnowledge(campaignId, file);
                 } catch {
-                    router.push(`/campaigns/${campaign.id}?knowledge_error=1`);
+                    router.push(`/campaigns/${campaignId}?knowledge_error=1`);
                     return;
                 }
             }
-            router.push(`/campaigns/${campaign.id}`);
+            router.push(`/campaigns/${campaignId}`);
         } catch (err) {
-            setError(err instanceof Error ? err.message : "Failed to create campaign");
+            const detail = err instanceof Error ? err.message : "Failed to create campaign";
+            setError(
+                campaignId
+                    ? `Campaign details were created, but its contact-field policy was not saved. Retry to finish without creating a duplicate. ${detail}`
+                    : detail,
+            );
             setSubmitting(false);
         }
     };
 
     return (
@@ -281,10 +303,22 @@ export function CampaignWizard() {
                         <p className="text-xs text-muted-foreground">
                             You can skip this and add knowledge later from the campaign page — but the agent
                             will only have its persona to work from until you do.
                         </p>
 
+                        <div className="border-t border-gray-200 pt-5 dark:border-white/10">
+                            <CampaignLeadFieldsPicker
+                                specs={leadFields.specs}
+                                value={leadFields.fields}
+                                onChange={leadFields.setFields}
+                                isLoading={leadFields.isLoading}
+                                error={leadFields.isError ? leadFields.error : undefined}
+                                onRetry={leadFields.retry}
+                                disabled={submitting}
+                            />
+                        </div>
+
                         <div className="flex justify-between pt-1">
                             <Button variant="ghost" onClick={() => setStep(0)}><ArrowLeft className="h-4 w-4" /> Back</Button>
                             <Button onClick={goToReview}>Next: Review <ArrowRight className="h-4 w-4" /></Button>
                         </div>
                     </div>
@@ -297,10 +331,11 @@ export function CampaignWizard() {
                             <SummaryRow label="Company" value={companyName} />
                             <SummaryRow label="Persona" value={PERSONAS.find((p) => p.value === personaType)?.title ?? personaType} />
                             <SummaryRow label="Agents" value={agentNames.join(", ")} />
                             <SummaryRow label="Voice" value={voiceName ? `${voiceName}${provider ? ` (${provider})` : ""}` : voiceId} />
                             <SummaryRow label="Knowledge" value={file ? file.name : "— none —"} />
+                            <SummaryRow label="Lead fields" value={leadFields.fields.length ? `${leadFields.fields.length} selected` : "— none —"} />
                         </div>
 
                         <div>
                             <Label>How the agent will open</Label>
                             <div className="mt-1 rounded-lg border border-gray-200 dark:border-white/10 bg-gray-50 dark:bg-white/5 px-3 py-2 text-sm italic text-gray-700 dark:text-zinc-300 min-h-[2.5rem]">
@@ -320,11 +355,11 @@ export function CampaignWizard() {
                             </details>
                         )}
 
                         <div className="flex justify-between pt-1">
                             <Button variant="ghost" onClick={() => setStep(1)} disabled={submitting}><ArrowLeft className="h-4 w-4" /> Back</Button>
-                            <Button onClick={onCreate} disabled={submitting}>
+                            <Button onClick={onCreate} disabled={submitting || leadFields.isLoading || leadFields.isError}>
                                 {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                                 {submitting ? "Creating…" : "Create campaign"}
                             </Button>
                         </div>
                     </div>
diff --git a/Talk-Leee/src/components/connectors/connector-card.test.ts b/Talk-Leee/src/components/connectors/connector-card.test.ts
index 6a9ac065..bd09b975 100644
--- a/Talk-Leee/src/components/connectors/connector-card.test.ts
+++ b/Talk-Leee/src/components/connectors/connector-card.test.ts
@@ -58,10 +58,29 @@ test("ConnectorCard shows Expired status and allows reconnect", () => {
     assert.equal(screen.getByRole("button", { name: "Reconnect" }).hasAttribute("disabled"), false);
     assert.equal(screen.getByRole("button", { name: "Disconnect" }).hasAttribute("disabled"), false);
     assert.equal(screen.queryByRole("button", { name: "Connect" }), null);
 });
 
+test("ConnectorCard exposes an unavailable server capability without a dead OAuth button", () => {
+    renderWithQueryClient(
+        createElement(ConnectorCard, {
+            type: "salesforce",
+            name: "Salesforce",
+            description: "Sync qualified leads",
+            icon: Mail,
+            status: "disconnected",
+            available: false,
+            unavailableReason: "Salesforce is not enabled by this server yet.",
+        })
+    );
+
+    assert.ok(screen.getByLabelText("Status: Unavailable"));
+    assert.ok(screen.getByText("Salesforce is not enabled by this server yet."));
+    assert.ok(screen.getByText("Server capability required"));
+    assert.equal(screen.queryByRole("button", { name: "Connect" }), null);
+});
+
 test("ConnectorCard calls authorize and shows loading state", async () => {
     const userEvent = (await import("@testing-library/user-event")).default;
     const user = userEvent.setup({ document: globalThis.document });
     const calls: string[] = [];
     const prevOpen = window.open;
diff --git a/Talk-Leee/src/components/connectors/connector-card.tsx b/Talk-Leee/src/components/connectors/connector-card.tsx
index 444a99f0..8510a900 100644
--- a/Talk-Leee/src/components/connectors/connector-card.tsx
+++ b/Talk-Leee/src/components/connectors/connector-card.tsx
@@ -48,10 +48,12 @@ export function ConnectorCard({
     errorMessage,
     oauthCallbackPath = "/connectors/callback",
     authorizeConnector,
     disconnectConnector,
     statusPillTheme,
+    available = true,
+    unavailableReason,
     className,
 }: {
     type: ConnectorProviderType;
     name: string;
     description: string;
@@ -63,10 +65,12 @@ export function ConnectorCard({
     errorMessage?: string | null;
     oauthCallbackPath?: string;
     authorizeConnector?: (input: { type: string; redirect_uri: string }) => Promise<{ authorization_url: string }>;
     disconnectConnector?: (input: { type: string }) => Promise<void>;
     statusPillTheme?: StatusPillTheme;
+    available?: boolean;
+    unavailableReason?: string;
     className?: string;
 }) {
     const qc = useQueryClient();
     const authorize = useAuthorizeConnector();
     const disconnect = useDisconnectConnector();
@@ -82,24 +86,25 @@ export function ConnectorCard({
         if (status === "expired") return "text-amber-700";
         return "text-muted-foreground";
     }, [status]);
 
     const detailsText = useMemo(() => {
+        if (!available) return unavailableReason?.trim() || "This connector is not enabled by the server.";
         if (inlineError) return inlineError;
         if (status === "error" || status === "expired") return errorMessage?.trim() || "Connection needs attention.";
         if (status === "connected") return "Syncing enabled.";
         return "Not connected.";
-    }, [errorMessage, inlineError, status]);
+    }, [available, errorMessage, inlineError, status, unavailableReason]);
 
     const authorizeFn = authorizeConnector ?? authorize.mutateAsync;
     const disconnectFn = disconnectConnector ?? disconnect.mutateAsync;
 
     const isBusy = pendingAction !== null;
 
-    const canConnect = status === "disconnected";
-    const canReconnect = status === "expired" || status === "error";
-    const canDisconnect = status === "connected" || status === "expired" || status === "error";
+    const canConnect = available && status === "disconnected";
+    const canReconnect = available && (status === "expired" || status === "error");
+    const canDisconnect = available && (status === "connected" || status === "expired" || status === "error");
 
     const connectOrReconnect = useCallback(async () => {
         setInlineError(undefined);
         setPendingAction(status === "disconnected" ? "connect" : "reconnect");
         try {
@@ -158,11 +163,16 @@ export function ConnectorCard({
                         </div>
                     </div>
                 </div>
 
                 <div className="flex items-center gap-2">
-                    <StatusPill state={statusPillState} theme={statusPillTheme} />
+                    <StatusPill
+                        state={statusPillState}
+                        theme={statusPillTheme}
+                        label={available ? undefined : "Unavailable"}
+                        tooltip={available ? undefined : unavailableReason || "This connector is not enabled by the server."}
+                    />
                 </div>
             </div>
 
             <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
                 <div className="rounded-xl border border-border bg-background/70 p-3">
@@ -177,10 +187,13 @@ export function ConnectorCard({
 
             <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
                 <div className="text-xs text-muted-foreground">{provider ? `Provider: ${provider}` : null}</div>
 
                 <div className="flex flex-wrap items-center gap-2">
+                    {!available ? (
+                        <span className="text-xs font-medium text-muted-foreground">Server capability required</span>
+                    ) : null}
                     {canConnect ? (
                         <Button
                             type="button"
                             variant="outline"
                             disabled={isBusy}
diff --git a/Talk-Leee/src/lib/connectors-utils.ts b/Talk-Leee/src/lib/connectors-utils.ts
index 4d858be6..e2112f1f 100644
--- a/Talk-Leee/src/lib/connectors-utils.ts
+++ b/Talk-Leee/src/lib/connectors-utils.ts
@@ -1,6 +1,6 @@
-export type ConnectorProviderType = "calendar" | "email" | "crm" | "drive";
+export type ConnectorProviderType = "calendar" | "email" | "crm" | "drive" | "salesforce";
 
 export type ConnectorCardAction = "connect" | "reconnect" | "disconnect";
 
 export function connectorCardActionFromStatus(status: string): ConnectorCardAction {
     if (status === "connected") return "disconnect";
diff --git a/Talk-Leee/src/lib/contact-form.test.ts b/Talk-Leee/src/lib/contact-form.test.ts
new file mode 100644
index 00000000..a96ef560
--- /dev/null
+++ b/Talk-Leee/src/lib/contact-form.test.ts
@@ -0,0 +1,27 @@
+import assert from "node:assert/strict";
+import test from "node:test";
+
+import { contactPayload, EMPTY_CONTACT_FORM } from "@/lib/contact-form";
+
+test("manual contact payload carries every operational contact field", () => {
+    const payload = contactPayload({
+        ...EMPTY_CONTACT_FORM,
+        phone_number: " +442079460000 ",
+        first_name: " Sian ",
+        last_name: " Roberts ",
+        company_name: " BuildWright ",
+        job_title: " Quantity Surveyor ",
+        best_time_to_call: " Mon-Fri 9-11 ",
+        timezone: " Europe/London ",
+        calling_notes: " Tender closes Friday ",
+        preferred_contact_method: "whatsapp",
+        do_not_call: true,
+    });
+
+    assert.equal(payload.full_name, "Sian Roberts");
+    assert.equal(payload.job_title, "Quantity Surveyor");
+    assert.equal(payload.preferred_contact_method, "whatsapp");
+    assert.equal(payload.do_not_call, true);
+    assert.equal(payload.timezone, "Europe/London");
+    assert.equal(payload.calling_notes, "Tender closes Friday");
+});
diff --git a/Talk-Leee/src/lib/contact-form.ts b/Talk-Leee/src/lib/contact-form.ts
new file mode 100644
index 00000000..ee65a2aa
--- /dev/null
+++ b/Talk-Leee/src/lib/contact-form.ts
@@ -0,0 +1,55 @@
+import type { ContactMutation } from "@/lib/dashboard-api";
+
+export type ContactFormState = {
+    phone_number: string;
+    first_name: string;
+    last_name: string;
+    mobile_number: string;
+    business_number: string;
+    email: string;
+    company_name: string;
+    job_title: string;
+    best_time_to_call: string;
+    timezone: string;
+    calling_notes: string;
+    preferred_contact_method: string;
+    do_not_call: boolean;
+};
+
+export const EMPTY_CONTACT_FORM: ContactFormState = {
+    phone_number: "",
+    first_name: "",
+    last_name: "",
+    mobile_number: "",
+    business_number: "",
+    email: "",
+    company_name: "",
+    job_title: "",
+    best_time_to_call: "",
+    timezone: "",
+    calling_notes: "",
+    preferred_contact_method: "",
+    do_not_call: false,
+};
+
+export function contactPayload(form: ContactFormState): ContactMutation {
+    const firstName = form.first_name.trim();
+    const lastName = form.last_name.trim();
+    const fullName = [firstName, lastName].filter(Boolean).join(" ");
+    return {
+        phone_number: form.phone_number.trim(),
+        mobile_number: form.mobile_number.trim(),
+        full_name: fullName,
+        first_name: firstName || undefined,
+        last_name: lastName || undefined,
+        email: form.email.trim(),
+        company_name: form.company_name.trim(),
+        job_title: form.job_title.trim(),
+        business_number: form.business_number.trim(),
+        best_time_to_call: form.best_time_to_call.trim(),
+        timezone: form.timezone.trim(),
+        calling_notes: form.calling_notes.trim(),
+        preferred_contact_method: form.preferred_contact_method.trim(),
+        do_not_call: form.do_not_call,
+    };
+}
diff --git a/Talk-Leee/src/lib/extended-api.rewards.test.ts b/Talk-Leee/src/lib/extended-api.rewards.test.ts
new file mode 100644
index 00000000..58dc5b09
--- /dev/null
+++ b/Talk-Leee/src/lib/extended-api.rewards.test.ts
@@ -0,0 +1,28 @@
+import assert from "node:assert/strict";
+import test from "node:test";
+
+import { extendedApi } from "@/lib/extended-api";
+
+test("review reward display is sourced from the verified ledger balance endpoint", async () => {
+    const originalFetch = globalThis.fetch;
+    const calls: Array<{ url: string; init?: RequestInit }> = [];
+    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
+        calls.push({ url: String(url), init });
+        return new Response(JSON.stringify({
+            total_points: 20,
+            entries: 2,
+            awarded_today: 1,
+            daily_cap: 5,
+            rewards_enabled: true,
+        }), { status: 200, headers: { "content-type": "application/json" } });
+    }) as typeof fetch;
+
+    try {
+        const balance = await extendedApi.getReviewRewardBalance();
+        assert.equal(balance.total_points, 20);
+        assert.match(calls[0]!.url, /\/calls\/reviews\/rewards\/balance$/);
+        assert.equal(calls[0]!.init?.method, "GET");
+    } finally {
+        globalThis.fetch = originalFetch;
+    }
+});
diff --git a/Talk-Leee/src/lib/extended-api.ts b/Talk-Leee/src/lib/extended-api.ts
index 21609177..3ab5c252 100644
--- a/Talk-Leee/src/lib/extended-api.ts
+++ b/Talk-Leee/src/lib/extended-api.ts
@@ -28,10 +28,18 @@ export interface ReviewOptions {
     points_per_review: number;
     daily_cap: number;
     bare_rating_earns_reward: boolean;
 }
 
+export interface ReviewRewardBalance {
+    total_points: number;
+    entries: number;
+    awarded_today: number;
+    daily_cap: number;
+    rewards_enabled: boolean;
+}
+
 /** One reviewer voice note about how the agent handled a call. */
 export interface CallFeedback {
     id: string;
     call_id: string;
     audio_url: string;
@@ -422,10 +430,15 @@ class ExtendedApi {
     /** Tag vocabulary and reward rules. Fetch once, render the form from it. */
     async getReviewOptions(): Promise<ReviewOptions> {
         return this.client.request({ path: "/calls/reviews/options", method: "GET" });
     }
 
+    /** A factual ledger balance. The UI never treats configuration alone as proof of an award. */
+    async getReviewRewardBalance(): Promise<ReviewRewardBalance> {
+        return this.client.request({ path: "/calls/reviews/rewards/balance", method: "GET" });
+    }
+
     /**
      * This user's own review of the call, or null when they have not left one.
      * Like getCallFeedback, "none yet" arrives as a thrown 404 and has to be
      * turned back into an ordinary empty result.
      */
diff --git a/Talk-Leee/src/lib/lead-details-api.test.ts b/Talk-Leee/src/lib/lead-details-api.test.ts
new file mode 100644
index 00000000..38218037
--- /dev/null
+++ b/Talk-Leee/src/lib/lead-details-api.test.ts
@@ -0,0 +1,38 @@
+import assert from "node:assert/strict";
+import test from "node:test";
+
+import { leadDetailsApi, type CampaignLeadField } from "@/lib/lead-details-api";
+
+test("campaign lead-field reads and writes use the campaign-scoped contract", async () => {
+    const originalFetch = globalThis.fetch;
+    const calls: Array<{ url: string; init?: RequestInit }> = [];
+    const fields: CampaignLeadField[] = [{
+        field_key: "company_name",
+        label: "Company",
+        field_type: "text",
+        is_required: true,
+        agent_visible: true,
+        user_visible: true,
+        options: null,
+        sort_order: 0,
+    }];
+    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
+        calls.push({ url: String(url), init });
+        return new Response(JSON.stringify({ fields }), {
+            status: 200,
+            headers: { "content-type": "application/json" },
+        });
+    }) as typeof fetch;
+
+    try {
+        await leadDetailsApi.campaignFields("campaign/unsafe");
+        await leadDetailsApi.setCampaignFields("campaign/unsafe", fields);
+
+        assert.match(calls[0]!.url, /\/campaigns\/campaign%2Funsafe\/lead-fields$/);
+        assert.equal(calls[0]!.init?.method, "GET");
+        assert.equal(calls[1]!.init?.method, "PUT");
+        assert.deepEqual(JSON.parse(String(calls[1]!.init?.body)), fields);
+    } finally {
+        globalThis.fetch = originalFetch;
+    }
+});
diff --git a/Talk-Leee/src/lib/lead-details-api.ts b/Talk-Leee/src/lib/lead-details-api.ts
index a94551d7..3d5f3f20 100644
--- a/Talk-Leee/src/lib/lead-details-api.ts
+++ b/Talk-Leee/src/lib/lead-details-api.ts
@@ -54,10 +54,21 @@ export interface CapturedDetail {
     confirmed: boolean;
     is_required: boolean;
     updated_at: string;
 }
 
+export interface CampaignLeadField {
+    field_key: string;
+    label: string;
+    field_type: string;
+    is_required: boolean;
+    agent_visible: boolean;
+    user_visible: boolean;
+    options?: string[] | null;
+    sort_order: number;
+}
+
 export interface ImportIssue {
     row: number;
     field: string;
     value: string;
     reason: string;
@@ -102,10 +113,28 @@ export const leadDetailsApi = {
             method: "GET",
             query: campaignId ? { campaign_id: campaignId } : undefined,
         });
     },
 
+    async campaignFields(campaignId: string): Promise<{ fields: CampaignLeadField[] }> {
+        return sharedHttpClient().request({
+            path: `/campaigns/${encodeURIComponent(campaignId)}/lead-fields`,
+            method: "GET",
+        });
+    },
+
+    async setCampaignFields(
+        campaignId: string,
+        fields: CampaignLeadField[],
+    ): Promise<{ fields: CampaignLeadField[] }> {
+        return sharedHttpClient().request({
+            path: `/campaigns/${encodeURIComponent(campaignId)}/lead-fields`,
+            method: "PUT",
+            body: fields,
+        });
+    },
+
     /**
      * A human correcting a value. Always lands as source=manual_edit, which
      * outranks everything, so it can never be overwritten by a later inference.
      */
     async correct(
diff --git a/Talk-Leee/src/lib/lead-outcome.test.ts b/Talk-Leee/src/lib/lead-outcome.test.ts
new file mode 100644
index 00000000..7e2bfa5e
--- /dev/null
+++ b/Talk-Leee/src/lib/lead-outcome.test.ts
@@ -0,0 +1,26 @@
+import assert from "node:assert/strict";
+import test from "node:test";
+
+import { leadInterestState } from "@/lib/lead-outcome";
+
+test("positive lead verdicts produce the interested state", () => {
+    for (const value of [
+        "qualified | strong fit",
+        "interested",
+        "callback | Tuesday morning",
+        "goal_achieved",
+    ]) {
+        assert.equal(leadInterestState(value), "interested", value);
+    }
+});
+
+test("captured-detail-adjacent negative verdicts never produce an interested badge", () => {
+    for (const value of ["no_interest", "not interested", "disqualified", "unsuccessful"]) {
+        assert.equal(leadInterestState(value), "not_interested", value);
+    }
+});
+
+test("missing and telephony-only outcomes remain unknown", () => {
+    assert.equal(leadInterestState(null), "unknown");
+    assert.equal(leadInterestState("answered"), "unknown");
+});
diff --git a/Talk-Leee/src/lib/lead-outcome.ts b/Talk-Leee/src/lib/lead-outcome.ts
new file mode 100644
index 00000000..8ca3b35a
--- /dev/null
+++ b/Talk-Leee/src/lib/lead-outcome.ts
@@ -0,0 +1,45 @@
+export type LeadInterestState = "interested" | "not_interested" | "unknown";
+
+function verdictToken(value: string | null | undefined): string {
+    return (value ?? "")
+        .split("|", 1)[0]
+        .trim()
+        .toLowerCase()
+        .replace(/[\s-]+/g, "_");
+}
+
+/**
+ * Translate the post-call lead verdict into the one claim the UI needs to
+ * make: did the analysis actually classify this person as worth following up?
+ *
+ * Capturing a phone number or company name is deliberately not enough. A
+ * caller can provide details and still say no, so deriving the green badge
+ * from the presence of captured fields would turn a rejection into a lead.
+ */
+export function leadInterestState(value: string | null | undefined): LeadInterestState {
+    const token = verdictToken(value);
+
+    if (
+        token === "qualified"
+        || token === "interested"
+        || token === "callback"
+        || token === "goal_achieved"
+        || token === "positive"
+    ) {
+        return "interested";
+    }
+
+    if (
+        token === "no_interest"
+        || token === "not_interested"
+        || token === "disqualified"
+        || token === "unqualified"
+        || token === "goal_not_achieved"
+        || token === "negative"
+        || token === "unsuccessful"
+    ) {
+        return "not_interested";
+    }
+
+    return "unknown";
+}
diff --git a/goals.md b/goals.md
index c0728d04..835a0fd8 100644
--- a/goals.md
+++ b/goals.md
@@ -75,11 +75,11 @@
 ### P1 — Deliver as MVP if P0 remains healthy
 
 - [ ] Review reward points/credits
 - [ ] Salesforce OAuth connection
 - [ ] One-way Talk-lee-to-Salesforce lead/contact synchronization
-- [ ] Review analytics dashboard
+- [x] Review analytics dashboard
 
 ### P2 — Do not block September 10
 
 - [ ] Automatic AI fine-tuning from feedback
 - [ ] Cash rewards or withdrawable rewards
@@ -138,14 +138,15 @@
 >   calls"** — reversed. `test_conversation_reviews.py` has 24 tests and they
 >   are all pure-function validation: tag vocabulary, rating bounds, comment
 >   handling, reward eligibility. **None of the three things this line names is
 >   tested.** True API-level tests are also blocked by the httpx/starlette
 >   TestClient mismatch (#79).
-> - **"Display confidence or 'needs review'"** (§8) — reversed. There is no
->   `confidence` field anywhere in the summary payload. A provenance banner was
->   added, which is a different thing; inventing a confidence number from
->   nothing would be worse than showing none.
+> - **"Display confidence or 'needs review'"** (§8) — completed 2026-09-01.
+>   Actionable AI classifications now show an explicit **Needs review** signal.
+>   The UI deliberately does not invent a numerical confidence value because
+>   the summary contract does not provide one. Component tests cover both the
+>   actionable and ordinary-summary states.
 > - **"Popovers do not cover save buttons or critical fields"** (§8) — reversed.
 >   Radix collision handling makes this *likely*, but it was never checked on a
 >   real narrow screen, and "likely" is not "done".
 >
 > **What was fixed rather than reversed:** "loading, retry and permission-error
@@ -489,11 +490,11 @@
 ### AI Summary
 
 - [x] Add hover/focus information for every main metric or conclusion.
 - [x] Explain how the summary was generated.
 - [x] Distinguish transcript facts from AI-inferred conclusions.
-- [ ] Display confidence or "needs review" where appropriate.
+- [x] Display confidence or "needs review" where appropriate.
 - [x] Explain key terms such as qualified, interested, callback and unsuccessful.
 
 ### Acceptance Criteria
 
 - [x] Every requested help icon works with mouse and keyboard.
@@ -542,10 +543,18 @@
 
 ---
 
 ## 10. Salesforce MVP
 
+> **Frontend safety status — 2026-09-01:** Salesforce is now visible in the
+> connector interface, but it fails closed when the server does not advertise
+> Salesforce support: the card says **Unavailable**, explains the missing
+> server capability and exposes no Connect/Reconnect OAuth action. This is not
+> a completed Salesforce connector. The checklist below stays open until
+> tenant-scoped OAuth, encrypted token storage, refresh, sync, retry,
+> reconciliation and isolation are implemented and proven end to end.
+
 ### September 10 Scope
 
 - [ ] Add Salesforce as a connector.
 - [ ] Implement OAuth authorization with secure state validation.
 - [ ] Store tokens encrypted and tenant-scoped.
@@ -614,11 +623,18 @@
 - [x] Update contact details view and table columns.
 - [x] Update CSV import template.
 - [x] Add column mapping during import.
 - [x] Show row-level validation failures.
 - [x] Add duplicate detection and merge/skip decision.
-- [ ] Let campaign creation select which contact fields the agent may use.
+- [x] Let campaign creation select which contact fields the agent may use.
+
+> **Verified 2026-09-01:** both guided and detailed campaign creation use the
+> server-owned contact-field registry, expose explicit per-field access and
+> requiredness, and block saving if the policy cannot be loaded. Creation retry
+> reuses the already-created campaign ID so a failed policy save cannot create a
+> duplicate campaign. The same policy editor is available on knowledge-driven
+> campaign edit pages.
 
 ### Agent Context
 
 - [x] Pass only necessary fields into the call prompt/context.
 - [x] Use the preferred calling number.
````

## Final verdict

Commit `48743d7f8a7e13f9da0592a9b49b9f2b5c03ec56` is on GitHub main, the GitHub frontend job passed, Vercel completed successfully, and the production alias returned HTTP 200. The frontend work in this report is live.

The broader product plan is not 100% complete. The remaining items are explicitly preserved above and in the included goals snapshot. No report line converts missing live or backend evidence into a completed claim.

End of report 15.
