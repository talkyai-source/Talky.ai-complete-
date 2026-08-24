# Talk-lee Product Delivery Checklist

**Delivery target:** September 10, 2026
**Planning start:** August 22, 2026
**Team:** Two developers, with agent testing and prompt tuning running in parallel
**Release rule:** Freeze the release candidate on September 8; September 9 is validation and rollback rehearsal; September 10 is controlled release.

## 1. Ownership

### Developer A — Backend, Voice and Integrations

- Campaign, call and review APIs
- Feedback/reward ledger and abuse controls
- Inbound call routing and session creation
- Generic lead-generation prompt rendering
- Lead-information capture and structured call outcomes
- Contact schema and campaign-variable mapping
- Billing top-up APIs and payment-provider webhook handling
- Salesforce OAuth and MVP synchronization
- Security-page backend endpoints
- Database migrations, audit logs, tests and observability

### Developer B — Frontend and Product Experience

- Conversation review panel
- Reward display and review history
- Security section moved into the main sidebar
- Inbound campaign creation and management pages
- Interested-lead details panel/form
- Information tooltips and popovers
- Token/creativity explanations in AI Options
- AI Summary explanation popovers
- Billing top-up interface
- Salesforce connection interface
- Expanded contacts form, table, import template and validation
- Frontend tests, loading states, empty states and error handling

### Parallel QA/Prompt-Tuning Track

- Freeze one prompt version for every test batch
- Run controlled lead-generation calls
- Review recordings and transcripts
- Label conversation problems
- Score the agent against an agreed rubric
- Change only one important prompt behavior per experiment
- Record prompt version, runtime configuration and test result

---

## 2. Priority and Scope

### P0 — Must be ready by September 10

- [x] Generic lead-generation prompt connected to live campaign runtime
- [x] Prompt version and hash visible in call logs
- [x] Expanded contact fields available to the agent
- [x] Structured interested-lead information capture
- [x] Per-conversation review and feedback storage
- [x] Security moved from Settings to the main sidebar
- [ ] Inbound campaign MVP
- [x] AI Options and AI Summary information tooltips
- [x] Billing minute top-up MVP
- [ ] Client-management and multi-tenant validation with 200 test clients
- [ ] End-to-end test and controlled release

### P1 — Deliver as MVP if P0 remains healthy

- [ ] Review reward points/credits
- [ ] Salesforce OAuth connection
- [ ] One-way Talk-lee-to-Salesforce lead/contact synchronization
- [ ] Review analytics dashboard

### P2 — Do not block September 10

- [ ] Automatic AI fine-tuning from feedback
- [ ] Cash rewards or withdrawable rewards
- [ ] Full bidirectional Salesforce synchronization
- [ ] Salesforce opportunity, task and campaign synchronization
- [ ] Advanced inbound IVR and multi-level call flows
- [ ] Fully automated prompt deployment based only on user reviews

---

## 3. Conversation Review and Reward System

### Backend

- [x] Create a `conversation_reviews` table.
- [x] Store `review_id`, `tenant_id`, `user_id`, `call_id`, `campaign_id`, `rating`, `review_tags`, `comment`, `created_at` and `updated_at`.
- [x] Add structured tags:
  - [x] Agent did not understand
  - [x] Agent interrupted caller
  - [x] Agent did not answer the question
  - [x] Response was too long
  - [x] Response was too slow
  - [x] Agent repeated itself
  - [x] Wrong qualification question
  - [x] Wrong call outcome
  - [x] Poor objection handling
  - [x] Incorrect information
  - [x] Good conversation
- [x] Allow one active review per user per call; edits update the same review.
- [x] Verify the reviewer belongs to the call's tenant.
- [x] Prevent users from reviewing calls they cannot access.
- [ ] Record prompt version, model, campaign and call trace with the review.
- [x] Create a review-reward ledger rather than directly changing balances.
- [x] Make rewards idempotent so repeat submissions cannot create duplicate credits.
- [ ] Add daily reward limits and suspicious-activity monitoring.
- [x] Do not reward an empty review unless a simple rating is intentionally eligible.
- [ ] Add admin controls to enable, disable and configure reward amounts.
- [ ] Add API tests for tenant isolation, duplicate rewards and unauthorized calls.

> ### ⚠️ TICK AUDIT, 2026-08-24 — four ticks were wrong and have been reversed
>
> Re-verified every tick against the code rather than against memory. Four did
> not survive. They are listed here rather than quietly flipped, because a
> checklist nobody can trust is worse than no checklist.
>
> - **"Record prompt version, model, campaign and call trace"** — reversed.
>   `prompt_template`, `prompt_version`, `prompt_hash` and `campaign_id` ARE
>   written. **`llm_model` is not.** The column exists (migration 0015 line 123)
>   but the `INSERT` never populates it, and `calls` has no model column to
>   source it from. It cannot be honestly completed by reading the tenant's
>   *current* model at review time: the model may have changed since the call,
>   so that would attribute an old call to a model it never ran on. Completing
>   this needs `calls.llm_model` captured at call time — a migration plus a
>   write-path change.
> - **"Add API tests for tenant isolation, duplicate rewards and unauthorized
>   calls"** — reversed. `test_conversation_reviews.py` has 24 tests and they
>   are all pure-function validation: tag vocabulary, rating bounds, comment
>   handling, reward eligibility. **None of the three things this line names is
>   tested.** True API-level tests are also blocked by the httpx/starlette
>   TestClient mismatch (#79).
> - **"Display confidence or 'needs review'"** (§8) — reversed. There is no
>   `confidence` field anywhere in the summary payload. A provenance banner was
>   added, which is a different thing; inventing a confidence number from
>   nothing would be worse than showing none.
> - **"Popovers do not cover save buttons or critical fields"** (§8) — reversed.
>   Radix collision handling makes this *likely*, but it was never checked on a
>   real narrow screen, and "likely" is not "done".
>
> **What was fixed rather than reversed:** "loading, retry and permission-error
> states" was ticked with no retry anywhere in the panel. A **Try again** button
> now sits in the error state — the form still holds every word, so the only
> thing missing was a way to send them again. That tick now stands.
>
> **Status of the two unticked items (2026-08-23).** Both are partly built and
> deliberately not ticked:
>
> - *Daily limits and suspicious-activity monitoring* — the daily cap is
>   enforced (`reward_daily_cap()`, per user per UTC day, logged as
>   `review_reward_daily_cap_reached`). There is no separate monitoring or
>   alerting beyond that log line.
> - *Admin controls for reward amounts* — configurable, but through environment
>   variables (`REVIEW_REWARDS_ENABLED`, `REVIEW_REWARD_POINTS`,
>   `REVIEW_REWARD_DAILY_MAX`), not a UI. Rewards are OFF by default. Both are
>   P1 concerns and neither blocks review capture, which works with rewards
>   disabled.

### Frontend

- [x] Add a **Review conversation** section to every completed call page/drawer.
- [x] Show recording and transcript beside the review form when available.
- [x] Add 1–5 rating or thumbs-up/thumbs-down control.
- [x] Add multi-select problem tags.
- [x] Add an optional written comment.
- [x] Show reward eligibility before submission.
- [x] Show a clear confirmation after successful submission.
- [x] Allow review editing without issuing a second reward.
- [x] Add loading, retry and permission-error states.
- [x] Add accessibility labels and keyboard navigation.
- [x] Put the feedback controls **beside the recording's play button**, not only
      on the call page.
- [x] Offer all three response types on every recording: **thumbs up/down, a
      voice note, and typed text**.
- [x] Hard-cap a feedback voice note at **30 seconds**.
- [x] Surface submitted reviews in the **admin panel** (`/admin/reviews`).

> **Where reviewing actually happens (2026-08-24).** Three ways to answer a
> recording, sitting on the recording itself, because they cost different
> amounts and carry different amounts of information — force one shape and
> people use none:
>
> - **Thumbs up / down** — one click, says only better/worse, but it is the one
>   people will actually give while working down a list. Thumbs-down writes
>   rating 2, which puts the call in the "needs listening" queue (1s and 2s);
>   1 stays available to mean something worse, chosen deliberately in the panel.
> - **A voice note, capped at 30 seconds** — the fastest way to say something
>   nuanced ("she'd already said no twice and it kept pitching"), which is
>   exactly the feedback nobody types. The recorder stops itself at 30s, so it
>   is a cutoff rather than a warning, and the server validates it too.
> - **Typed text** — the only option when you are somewhere you cannot talk.
>
> Thumb and text write `conversation_reviews` (rating + comment, one review per
> user per call). The voice note writes `call_feedback` — one note per call,
> stored durably then transcribed. Kept separate because a voice note has a
> lifecycle (upload, transcribe, retry) and a review is a structured judgement.
>
> **None of them overwrites another.** `submitReview` is a PUT of the whole
> review, so a thumb resends the existing comment and tags untouched, and saving
> a comment resends the existing rating. Without that, whichever control you
> used last would silently erase the other.
>
> Live on the **Recordings** page (full bar under each player) and the **calls
> list** (thumbs only — the row is a fixed grid, and an expanding panel in an
> `auto` column would squash the other columns; the full panel is one click away
> on the call page).
>
> Reading everyone's reviews is a different job from leaving one, so the
> management view moved from a top-level `/reviews` route to `/admin/reviews`.
> Its endpoint was always `require_admin_tenant`; only the navigation disagreed,
> which meant a non-admin who clicked it got a bare 403. `/reviews` now
> redirects, preserving existing links.

### Safe Improvement Loop

- [x] Do not let a single review automatically rewrite the production prompt.
- [x] Aggregate reviews by prompt version and failure category.
- [x] Manually verify low-rated calls against recordings/transcripts.
- [ ] Convert verified problems into evaluation cases.
- [ ] Test candidate prompts against the evaluation set.
- [ ] Deploy a prompt only after human approval and canary testing.
- [x] Retain rollback access to the previous prompt version.

### Acceptance Criteria

- [x] A valid user can review an accessible completed call.
- [x] Unauthorized users receive no call or review data.
- [x] A call cannot generate duplicate rewards for the same reviewer.
- [x] Review edits preserve the original reward transaction.
- [x] Admin can filter results by campaign, prompt version, rating and tag.
- [x] Review submission does not change production prompts automatically.

---

## 4. Security as a Main Sidebar Section

### Frontend

- [x] Add **Security** to the main left navigation.
- [x] Remove or redirect the old Security entry inside Settings.
- [x] Preserve deep links and bookmarks with a route redirect.
- [x] Display only security controls the current role can manage.
- [x] Include sections for:
  - [x] Password/account security
  - [x] Multi-factor authentication status
  - [x] Active sessions
  - [x] API keys/tokens
  - [x] Audit activity
  - [ ] Allowed IPs, if supported
  - [x] Data retention and recording controls, if supported

> **Built 2026-08-24 — `/security`, reachable from the sidebar.**
>
> Password change (which signs out every other session), passkeys, 2FA status
> with turn-off and recovery-code rotation, and active sessions. API keys and
> audit activity appear only for admins — and the backend enforces that
> independently, so hiding them is convenience, not the boundary.
>
> **"Allowed IPs" is left unticked because it is not supported.** There is no IP
> allow-list anywhere in the backend. The page says so in as many words rather
> than showing an empty panel that implies a control exists — a security page
> that overstates what it enforces is worse than one that admits a gap.
>
> The Settings → Security tab is now a pointer to `/security`. The controls were
> *moved*, not copied: MFA disable and recovery-code regeneration went with
> them, so the same panel cannot exist in two files and drift apart.
>
> Retention is shown read-only, because it is set by plan rather than per user.

### Backend/Security

- [ ] Reuse existing endpoints where possible.
- [ ] Apply tenant and role authorization to every security endpoint.
- [ ] Never return raw API secrets after creation.
- [ ] Audit key creation, rotation, revocation and security-setting changes.
- [ ] Add rate limiting to sensitive actions.
- [ ] Add tests for viewer, partner-admin, tenant-admin and master-admin roles.

### Acceptance Criteria

- [x] Security is directly accessible from the left sidebar.
- [x] Old URLs redirect correctly.
- [x] Unauthorized controls are hidden and rejected by the backend.
- [ ] Sensitive mutations appear in the audit log.

---

## 5. Inbound Campaign MVP

### Campaign Configuration

- [ ] Add campaign type: `outbound` or `inbound`.
- [ ] Add an **Inbound** section to the sidebar.
- [ ] Allow users to create an inbound campaign.
- [ ] Required settings:
  - [ ] Campaign name
  - [ ] Assigned phone number/SIP route
  - [ ] Agent/prompt selection
  - [ ] Voice selection
  - [ ] Business hours and timezone
  - [ ] After-hours behavior
  - [ ] Greeting/opening message
  - [ ] Human transfer destination
  - [ ] Voicemail/fallback behavior
  - [ ] Recording and disclosure policy
  - [ ] Call outcome rules
- [ ] Prevent the same inbound number from being actively assigned to conflicting campaigns.
- [ ] Add activate, pause and archive actions.

### Runtime

- [ ] Resolve incoming DID/SIP destination to tenant and inbound campaign.
- [ ] Create the session with the correct tenant, campaign, prompt and voice.
- [ ] Pass caller phone number as contact context where permitted.
- [ ] Apply business-hours logic before starting the normal agent flow.
- [ ] Support transfer failure and after-hours fallback.
- [ ] Persist inbound direction, DID, caller ID, campaign ID and outcome.
- [ ] Apply concurrency, quota and billing checks.
- [ ] Add structured logs for routing decisions.

### Acceptance Criteria

- [ ] A test number routes to exactly one correct tenant/campaign.
- [ ] The correct inbound agent answers with the configured greeting.
- [ ] Calls outside business hours follow the configured fallback.
- [ ] Transfer success and failure are handled clearly.
- [ ] Inbound minutes appear correctly in usage/billing.
- [ ] Tenant A cannot see or route Tenant B's calls.

---

## 6. Generic Lead-Generation Prompt: Implementation and Testing

### Backend Integration

- [ ] Store the master template as `generic_lead_generation` with a version.
- [ ] Do not use `You are a helpful AI assistant` for campaign calls.
- [ ] Load the selected campaign's prompt and configuration from the database.
- [ ] Render campaign variables before session creation.
- [ ] Fail validation when required variables are missing.
- [ ] Never send unresolved `{{variable}}` placeholders to the LLM.
- [ ] Add lead-specific context separately from stable system instructions.
- [ ] Log `campaign_id`, `prompt_template`, `prompt_version` and `prompt_hash`.
- [ ] Keep prompt versions immutable after use; create a new version for changes.
- [ ] Add rollback to the previous approved prompt version.

### Prompt Test Matrix

- [ ] Opening and reason for calling
- [ ] Prospect says they are busy
- [ ] Prospect asks, "Why are you calling?"
- [ ] Prospect asks, "What does your company do?"
- [ ] Prospect asks about price
- [ ] Interested prospect
- [ ] Not interested
- [ ] Existing provider
- [ ] Send information by email
- [ ] Callback request
- [ ] Human transfer request
- [ ] Wrong number
- [ ] Do-not-call request
- [ ] Prospect interrupts the agent
- [ ] Prospect gives several business details in one answer
- [ ] Prospect provides incomplete information
- [ ] Normal successful booking
- [ ] Tool/booking/transfer failure

### Conversation Scorecard

- [ ] Direct questions answered before qualification
- [ ] One question asked at a time
- [ ] Responses normally limited to one or two sentences
- [ ] No repeated pitch after a clear rejection
- [ ] No fabricated facts or tool success
- [ ] Correct details captured
- [ ] Correct outcome selected
- [ ] Natural closing
- [ ] No talking over the caller
- [ ] No stale audio after interruption

### Release Gate

- [ ] At least 30 controlled calls on one frozen prompt/runtime version.
- [ ] At least five speakers and two accents.
- [ ] At least 95% of direct questions answered correctly.
- [ ] At least 95% correct call outcome classification.
- [ ] Zero ignored do-not-call requests.
- [ ] Zero fabricated bookings, transfers or prices.
- [ ] No old prompt used in any test call.
- [ ] Every call has a call ID, recording/transcript, prompt version and score.

---

## 7. Interested-Lead Information Form

### Data Model

- [ ] Create a structured `lead_capture` or `call_lead_details` record linked to call, campaign, contact and tenant.
- [ ] Support campaign-defined custom fields.
- [ ] Field types: text, number, email, phone, date/time, single select, multi-select and notes.
- [ ] Mark fields as required, optional, agent-visible and user-visible.
- [ ] Record the source of each value: imported contact, caller statement, agent inference or manual edit.
- [ ] Do not treat inferred values as confirmed facts.

### Agent Behavior

- [ ] Supply required field definitions to the agent.
- [ ] Let the agent extract fields from natural conversation.
- [ ] Do not force the agent to ask for information already provided.
- [ ] Confirm important contact and appointment information.
- [ ] Use `unknown` when information was not provided.
- [ ] Update structured fields after each confirmed detail or at call completion.

### Frontend

- [ ] Show an **Interested lead** badge when interest is detected/confirmed.
- [ ] Open a compact lead-information panel from the call page.
- [ ] Display captured business/customer details in a readable form.
- [ ] Highlight missing required fields.
- [ ] Allow authorized users to correct or complete details.
- [ ] Show who/what supplied each value.
- [ ] Add save, validation and conflict handling.

### Acceptance Criteria

- [ ] Information spoken once is captured without being asked again.
- [ ] The form is linked to the correct tenant, call and contact.
- [ ] Missing details remain visibly missing rather than being invented.
- [ ] Manual corrections are audited.
- [ ] Captured information is available to approved CRM synchronization.

---

## 8. Tooltips and Information Popovers

### Component

- [x] Build one reusable tooltip/popover component.
- [x] Support mouse hover, keyboard focus and mobile tap.
- [x] Add a short label plus optional "Learn more" content.
- [x] Avoid hiding essential warnings only inside tooltips.
- [x] Ensure the popup stays inside the viewport.

### AI Options

- [x] Add information help for **Tokens** explaining:
  - [x] Tokens are pieces of input/output text.
  - [x] Higher limits allow longer replies but may increase latency and cost.
  - [x] Voice-agent replies should normally remain short.
- [x] Add information help for **Creativity/Temperature** explaining:
  - [x] Lower values are more consistent and predictable.
  - [x] Higher values are more varied but may increase mistakes.
  - [x] Recommended lead-generation range is shown without silently changing it.

### AI Summary

- [x] Add hover/focus information for every main metric or conclusion.
- [x] Explain how the summary was generated.
- [x] Distinguish transcript facts from AI-inferred conclusions.
- [ ] Display confidence or "needs review" where appropriate.
- [x] Explain key terms such as qualified, interested, callback and unsuccessful.

### Acceptance Criteria

- [x] Every requested help icon works with mouse and keyboard.
- [x] Mobile users can open and close the same information.
- [x] Explanations use simple language.
- [ ] Popovers do not cover save buttons or critical fields.

---

## 9. Billing Minute Top-Up

### Backend

- [x] Define approved top-up packages and currency.
- [x] Create a top-up order before payment.
- [x] Use the payment provider's hosted checkout or secure payment flow.
- [x] Verify signed payment webhooks.
- [x] Make webhook processing idempotent.
- [x] Credit minutes only after verified successful payment.
- [x] Record money and minutes in an immutable billing ledger.
- [x] Handle failed, cancelled, duplicate, refunded and disputed payments.
- [x] Send receipt/confirmation according to configured channel.
- [x] Add admin reconciliation view or export.

### Frontend

- [x] Add **Top up minutes** to Billing.
- [x] Show current minute balance.
- [x] Show package minutes, price, currency and expiry rules.
- [x] Show payment status and top-up history.
- [x] Prevent double submission while checkout is starting.
- [x] Show clear failure and retry guidance.

### Acceptance Criteria

- [x] Successful verified payment credits minutes once.
- [x] Duplicate webhook does not duplicate minutes.
- [x] Failed/cancelled payment adds no minutes.
- [ ] Tenant billing records remain isolated. — RLS policies written and every
      query is tenant-scoped, but this stays OPEN until #80: the production DB
      role is a superuser with BYPASSRLS, so no policy on any table is actually
      enforced. Ticking this would be claiming an isolation the database is not
      currently providing.
- [x] New balance is reflected in call quota enforcement.

---

## 10. Salesforce MVP

### September 10 Scope

- [ ] Add Salesforce as a connector.
- [ ] Implement OAuth authorization with secure state validation.
- [ ] Store tokens encrypted and tenant-scoped.
- [ ] Refresh access tokens safely.
- [ ] Allow the tenant to disconnect Salesforce.
- [ ] Map Talk-lee contact/lead fields to Salesforce Lead or Contact fields.
- [ ] Push qualified/interested leads to Salesforce.
- [ ] Store Salesforce object ID and synchronization status.
- [ ] Retry transient failures with bounded backoff.
- [ ] Send failures to a dead-letter/reconciliation queue.
- [ ] Prevent duplicate Salesforce records with a documented matching strategy.
- [ ] Add audit logs without exposing access tokens.

### Explicitly Deferred

- [ ] Bidirectional synchronization
- [ ] Salesforce opportunity creation
- [ ] Salesforce campaign membership
- [ ] Activity/task synchronization
- [ ] Complex custom-object mapping
- [ ] Historical bulk synchronization

### Acceptance Criteria

- [ ] Tenant can connect and disconnect Salesforce safely.
- [ ] One qualified test lead reaches the correct Salesforce account.
- [ ] Retrying the same event does not create an unintended duplicate.
- [ ] Authentication and API failures are visible to the tenant/admin.
- [ ] One tenant cannot access another tenant's Salesforce connection.

---

## 11. Expanded Contact Fields

### Canonical Contact Model

- [ ] `first_name`
- [ ] `last_name`
- [ ] `full_name` as display/derived field where possible
- [ ] `mobile_number`
- [ ] `business_number`
- [ ] `email`
- [ ] `company_name`
- [ ] `job_title` or role
- [ ] `best_time_to_call`
- [ ] `timezone`
- [ ] `calling_notes`
- [ ] `preferred_contact_method`, if needed
- [ ] `do_not_call`
- [ ] `custom_fields`

### Data Rules

- [ ] Avoid storing duplicate conflicting `phone_number` and `mobile_number` values without defining a canonical calling number.
- [ ] Add `primary_phone_type` or a clear priority rule.
- [ ] Normalize phone numbers to E.164 while preserving display formatting if needed.
- [ ] Validate email without rejecting legitimate formats.
- [ ] Interpret `best_time_to_call` together with timezone.
- [ ] Do not call when `do_not_call=true`.
- [ ] Encrypt or protect sensitive contact data according to platform policy.
- [ ] Audit imports, edits and deletions.

### Frontend and Import

- [ ] Update add/edit contact form.
- [ ] Update contact details view and table columns.
- [ ] Update CSV import template.
- [ ] Add column mapping during import.
- [ ] Show row-level validation failures.
- [ ] Add duplicate detection and merge/skip decision.
- [ ] Let campaign creation select which contact fields the agent may use.

### Agent Context

- [ ] Pass only necessary fields into the call prompt/context.
- [ ] Use the preferred calling number.
- [ ] Respect best time to call and timezone in dialer scheduling.
- [ ] Supply calling notes without allowing them to override system/security rules.
- [ ] Clearly delimit imported notes as untrusted data.
- [ ] Avoid reading internal notes aloud unless explicitly required.

### Acceptance Criteria

- [ ] Manual and CSV-created contacts produce the same schema.
- [ ] Dialer chooses the correct phone number.
- [ ] Agent receives approved name, business and call notes.
- [ ] Best-time scheduling respects timezone.
- [ ] Do-not-call contacts cannot be queued.

---

## 12. Client Management and 200-Tenant Validation

### Test Objective

Prove that Talk-lee can manage at least 200 separate client organizations without mixing their data, permissions, files, calls, usage or billing. This is a multi-tenant correctness and platform-capacity test—not only a login test.

Use synthetic test clients and synthetic contact information. Do not use real client data for this test.

### Test Population

- [ ] Create 200 synthetic tenant/client organizations.
- [ ] Give every tenant a unique tenant ID, company name and subscription.
- [ ] Create at least one tenant administrator for every tenant.
- [ ] Create additional role samples across the population:
  - [ ] Tenant admin
  - [ ] Campaign manager
  - [ ] Agent/operator
  - [ ] Billing user
  - [ ] Read-only user
  - [ ] Partner/reseller user, where supported
- [ ] Create a master-admin account that can manage all 200 tenants.
- [ ] Seed different plans, balances, quotas and feature permissions.
- [ ] Seed active, trial, suspended, cancelled and overdue client states.
- [ ] Generate contacts, campaigns, calls, transcripts, recordings, attachments, reviews and billing records for every tenant.
- [ ] Keep a deterministic seed/manifest so failed records can be traced and the test can be repeated.

### Authentication and Session Tests

- [ ] Sign in successfully as a user from each of the 200 tenants.
- [ ] Confirm every login resolves the correct tenant and role.
- [ ] Test 200 sequential sign-ins.
- [ ] Test 200 concurrent active authenticated sessions.
- [ ] Test repeated login, logout, token refresh and session expiry.
- [ ] Confirm a user switching browser tabs cannot inherit another tenant's context.
- [ ] Confirm cached API responses are tenant-scoped.
- [ ] Confirm password reset and invitation links are tenant/user specific.
- [ ] Confirm disabled or suspended users cannot create new sessions.
- [ ] Confirm revoked sessions stop working.
- [ ] Check that session cookies/tokens use secure settings and are not exposed in logs.

### Tenant Isolation Matrix

For selected tenant pairs—and through automated tests across all 200 tenants—attempt to read or mutate another tenant's resources by changing IDs in URLs and API requests.

- [ ] Tenant profile and settings
- [ ] Users, invitations and roles
- [ ] Contacts and imported lead lists
- [ ] Campaigns and campaign configurations
- [ ] Phone numbers and SIP configurations
- [ ] Inbound routing and transfer destinations
- [ ] Calls, recordings and transcripts
- [ ] Conversation reviews and rewards
- [ ] Interested-lead forms and captured details
- [ ] Attachments and generated download links
- [ ] Meetings, reminders, email and SMS records
- [ ] Connectors and Salesforce credentials
- [ ] Usage, quotas, invoices, payments and minute balances
- [ ] API keys, audit logs and security settings

Every unauthorized cross-tenant request must be rejected without revealing whether the target resource exists.

### Client Management/Admin Tests

- [ ] Master admin can search, filter and paginate 200 tenants.
- [ ] Master admin can open a tenant without loading unrelated tenant data.
- [ ] Create a new tenant and verify default plan, roles, quotas and settings.
- [ ] Edit tenant profile and subscription safely.
- [ ] Suspend a tenant and verify its users/campaigns cannot continue prohibited activity.
- [ ] Reactivate a tenant without corrupting historical data.
- [ ] Archive/cancel a tenant according to retention policy.
- [ ] Verify impersonation/support-access features, if present, are authorized, time-limited and audited.
- [ ] Verify bulk actions require confirmation and cannot silently affect the wrong clients.
- [ ] Confirm tenant list totals, status counts and pagination remain correct after changes.

### Attachments, Recordings and File Storage

- [ ] Upload permitted file types for all 200 tenants.
- [ ] Reject prohibited file types and oversized files.
- [ ] Validate MIME type/content rather than trusting the filename.
- [ ] Confirm object/storage keys contain safe tenant scoping.
- [ ] Confirm download URLs cannot be reused to access another tenant's file.
- [ ] Test attachment preview, download, replacement and deletion.
- [ ] Confirm deleting a database row does not leave sensitive files indefinitely without a cleanup policy.
- [ ] Confirm deleting/replacing a file does not break another tenant's file.
- [ ] Scan uploads for malware if the product accepts arbitrary attachments.
- [ ] Verify encryption, retention and backup/restore behavior.
- [ ] Check quotas for total storage, per-file size and file count.
- [ ] Verify recordings and transcripts remain linked to the correct call and tenant.

### Billing, Plans and Minute Balances

- [ ] Seed different plans and limits across the 200 tenants.
- [ ] Confirm every tenant sees only its own subscription, invoices and payments.
- [ ] Confirm plan features and quotas are enforced independently.
- [ ] Test minute deductions for inbound and outbound calls.
- [ ] Test top-up purchases and balance updates.
- [ ] Confirm duplicate payment webhooks do not duplicate minutes.
- [ ] Confirm failed, cancelled, refunded and disputed payments adjust access/balance correctly.
- [ ] Confirm one tenant's call cannot deduct another tenant's minutes.
- [ ] Reconcile call-duration records against billed minutes.
- [ ] Test zero balance, low balance, quota exceeded and unlimited/enterprise conditions.
- [ ] Confirm currency, taxes and invoice numbering follow the configured billing rules.
- [ ] Confirm billing administrators can see billing while unauthorized roles cannot.
- [ ] Confirm every balance change has an immutable ledger/audit event.

### Campaigns, Calls and Contacts

- [ ] Create outbound and inbound campaigns under multiple tenants.
- [ ] Confirm campaign lists, counts and dashboards are tenant-scoped.
- [ ] Import contacts for all tenants using expanded contact fields.
- [ ] Confirm duplicate detection runs only within the intended tenant scope.
- [ ] Confirm best-time-to-call and timezone rules are applied per contact.
- [ ] Confirm do-not-call rules block queueing and calling.
- [ ] Start campaigns for several tenants simultaneously.
- [ ] Confirm concurrency and quotas are enforced per tenant and globally.
- [ ] Confirm incoming numbers route to the correct tenant/campaign.
- [ ] Confirm prompts, voices, transfer numbers and connectors never cross tenants.
- [ ] Confirm call outcomes, interested-lead details and reviews attach to the correct tenant.

### Connector and Credential Isolation

- [ ] Connect different Salesforce/test integrations for selected tenants.
- [ ] Confirm each connector uses only its owning tenant's encrypted credentials.
- [ ] Confirm disconnecting Tenant A does not affect Tenant B.
- [ ] Test token refresh and expired/revoked credential behavior.
- [ ] Confirm connector jobs and retry queues preserve tenant ID.
- [ ] Confirm dead-letter/retry records contain no raw secrets.
- [ ] Confirm webhook events resolve the correct tenant before processing.

### Performance and Capacity

- [ ] Measure tenant-list page with 200 clients.
- [ ] Measure dashboard/API response time with seeded tenant data.
- [ ] Test 200 active user sessions with realistic navigation and API requests.
- [ ] Test concurrent contact imports, attachment uploads and report views.
- [ ] Test simultaneous campaign activity within the safe call-capacity limit.
- [ ] Monitor application CPU, memory, database connections, query latency, cache hit rate, queue depth and storage errors.
- [ ] Identify N+1 queries and missing indexes.
- [ ] Verify pagination is used instead of loading all tenants/calls/contacts into memory.
- [ ] Define and record P50, P95 and maximum response times for critical endpoints.
- [ ] Run a soak test for at least two hours to detect memory, connection or queue leaks.

### Failure and Recovery Tests

- [ ] Restart backend services while 200 sessions exist and verify safe recovery.
- [ ] Simulate database timeout and connection-pool exhaustion.
- [ ] Simulate Redis/cache unavailability.
- [ ] Simulate object-storage upload/download failure.
- [ ] Simulate payment-webhook retry and duplication.
- [ ] Simulate connector/API failure.
- [ ] Confirm failures do not mix tenants or corrupt balances.
- [ ] Confirm retry jobs remain idempotent and tenant-scoped.
- [ ] Confirm monitoring identifies the affected tenant without exposing another tenant's data.
- [ ] Test backup restore in a non-production environment and verify tenant/file relationships.

### Audit, Privacy and Data Lifecycle

- [ ] Audit tenant creation, suspension, deletion and subscription changes.
- [ ] Audit user invitations, role changes and sensitive access.
- [ ] Audit attachment, recording, billing and connector actions.
- [ ] Redact tokens, passwords, payment data and unnecessary personal information from logs.
- [ ] Verify retention and deletion rules for calls, recordings, transcripts, attachments and reviews.
- [ ] Verify tenant export contains only that tenant's data.
- [ ] Verify tenant deletion/anonymization does not delete shared platform configuration or another tenant's records.

### Required Evidence

- [ ] Test run ID, environment and timestamp
- [ ] Exact application/runtime version and configuration
- [ ] Synthetic tenant seed/manifest version
- [ ] Total tenants/users/sessions created
- [ ] Total checks passed, failed and skipped
- [ ] Cross-tenant access attempt results
- [ ] Billing reconciliation report
- [ ] Attachment/storage reconciliation report
- [ ] Performance P50/P95/max results
- [ ] CPU, memory, database, cache and queue graphs
- [ ] Every failed scenario with request/trace ID
- [ ] Retest evidence after fixes
- [ ] Cleanup confirmation for synthetic accounts and stored files

### Release Acceptance Criteria

- [ ] All 200 tenants can be created, authenticated and managed.
- [ ] Zero successful cross-tenant data-access attempts.
- [ ] Zero attachments, calls, contacts, credentials or billing records assigned to the wrong tenant.
- [ ] Zero incorrect minute deductions or duplicate top-up credits.
- [ ] All role restrictions behave as designed.
- [ ] No unresolved P0/P1 security or data-integrity defects.
- [ ] Critical dashboard/API P95 response time meets the agreed target under the 200-session test.
- [ ] No sustained memory, connection or queue leak during the soak test.
- [ ] Backup/restore and rollback procedures are proven in a safe environment.
- [ ] Synthetic test data is removed or clearly isolated after validation.

---

## 13. Delivery Calendar

### August 22–24 — Design and Contracts

**Developer A**

- [ ] Finalize database migrations and API contracts.
- [ ] Define prompt template schema/versioning.
- [ ] Define inbound routing and billing rules.
- [ ] Define review reward ledger and Salesforce MVP boundary.
- [ ] Define 200-tenant synthetic seed, tenant-isolation matrix and billing/file reconciliation tests.

**Developer B**

- [ ] Produce page/component wireframes.
- [ ] Define sidebar changes and routes.
- [ ] Define shared tooltip/popover and form components.
- [ ] Confirm frontend API payloads with Developer A.

**Joint gate**

- [ ] API contracts frozen before parallel implementation.
- [ ] Migration rollback plan reviewed.

### August 25–29 — Core P0 Build

**Developer A**

- [ ] Contact migration and agent-context mapping.
- [ ] Generic prompt runtime integration and logging.
- [ ] Conversation review APIs.
- [ ] Interested-lead structured capture.
- [ ] Inbound routing foundation.

**Developer B**

- [ ] Expanded contacts UI/import mapping.
- [ ] Conversation review UI.
- [ ] Interested-lead panel.
- [ ] Security sidebar/page move.
- [ ] Tooltip/popover component.

**Parallel QA**

- [ ] Establish baseline calls using the old prompt.
- [ ] Prepare 30-call test scripts and scoring rubric.
- [ ] Prepare the 200-tenant test environment and synthetic accounts.

### August 30–September 3 — Inbound, Billing and UX Completion

**Developer A**

- [ ] Complete inbound campaign MVP.
- [ ] Complete top-up order, webhook and ledger flow.
- [ ] Add reward idempotency and abuse controls.
- [ ] Add backend tests and audit events.

**Developer B**

- [ ] Complete inbound campaign pages.
- [ ] Complete minute top-up UI.
- [ ] Complete AI Options and AI Summary help content.
- [ ] Complete reward display and review analytics basics.

**Parallel QA**

- [ ] Run prompt experiment batch 1.
- [ ] Review failures and approve prompt version 1.1 only if evidence supports it.

### September 4–6 — Salesforce MVP and Integration Testing

**Developer A**

- [ ] Salesforce OAuth and one-way lead/contact push.
- [ ] Retry, deduplication and reconciliation handling.
- [ ] End-to-end tenant/security tests.

**Developer B**

- [ ] Salesforce connector and field-mapping UI.
- [ ] Integration status/error interface.
- [ ] Complete responsive and accessibility checks.

**Joint gate**

- [ ] If any P0 item is unstable, pause Salesforce and finish P0.

### September 7 — Integrated Release Candidate

- [ ] Merge only reviewed changes.
- [ ] Apply migrations in staging/canary.
- [ ] Run backend, frontend and C++ builds/tests.
- [ ] Verify runtime configuration and secrets.
- [ ] Verify inbound and outbound dashboard routing.
- [ ] Verify billing in payment-provider test mode.
- [ ] Seed and smoke-test the 200 synthetic tenant/client accounts.

### September 8 — Freeze and Controlled Validation

- [ ] Freeze code, prompt and runtime configuration.
- [ ] Run 30 controlled lead-generation calls.
- [ ] Run inbound call matrix.
- [ ] Run review/reward abuse tests.
- [ ] Run top-up duplicate-webhook tests.
- [ ] Run tenant isolation/security tests.
- [ ] Run Salesforce duplicate/retry tests if included.
- [ ] Run the complete 200-tenant isolation, billing, attachment and session test.
- [ ] Run the two-hour multi-tenant soak test.

### September 9 — Fix Only Release Blockers

- [ ] No new features.
- [ ] Fix only confirmed release-blocking defects.
- [ ] Repeat affected regression tests.
- [ ] Rehearse code, database and configuration rollback.
- [ ] Prepare release notes and known limitations.

### September 10 — Controlled Release

- [ ] Deploy exact approved commit/images.
- [ ] Apply verified migrations.
- [ ] Confirm effective prompt version and configuration.
- [ ] Run one inbound and one outbound smoke call.
- [ ] Verify billing and review submission.
- [ ] Monitor errors, call failures, latency and payment webhooks.
- [ ] Increase traffic gradually only if metrics remain healthy.

---

## 14. Definition of Done

A feature is not "done" merely because code is pushed.

- [ ] Product behavior matches acceptance criteria.
- [ ] Tenant authorization is enforced in backend tests.
- [ ] Database migration and rollback are tested.
- [ ] Frontend handles loading, empty, success and failure states.
- [ ] Audit and operational logs contain useful identifiers but no secrets.
- [ ] Automated tests pass in a reproducible environment.
- [ ] Feature is tested in staging/canary.
- [ ] Documentation and configuration are updated.
- [ ] Monitoring and error reporting exist.
- [ ] A rollback version and procedure are recorded.
- [ ] Product owner accepts the feature using a real end-to-end scenario.

---

## 15. Daily Management Checklist

- [ ] 15-minute morning stand-up.
- [ ] Each developer states yesterday's evidence, today's goal and blocker.
- [ ] No task remains "90% done" without a named missing acceptance item.
- [ ] Pull requests stay small enough to review.
- [ ] Database/API contract changes are communicated before frontend work continues.
- [ ] Production is not used as the development test environment.
- [ ] Prompt changes are versioned and tested separately from code changes.
- [ ] End-of-day tracker shows completed, blocked, failed-test and ready-for-review items.

## Final Delivery Decision

The September 10 release should prioritize reliable P0 behavior. If the team falls behind, defer Salesforce beyond the connector MVP and defer automatic AI training. Do not sacrifice inbound routing correctness, tenant security, billing integrity, prompt runtime correctness or contact-data safety to claim that every requested feature shipped.
