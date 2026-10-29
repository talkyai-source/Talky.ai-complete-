# Customer support pre-mortem — 29 October 2026

This is the active support review within the frozen production-readiness plan. It covers both the AI handling callers and customers obtaining help with the SaaS. The coverage index includes all 45 recorded features, 22 scenarios and 15 release gates. Coverage means each item has a failure hypothesis and acceptance path; it does not mean every item has passed. The execution tracker remains authoritative.

The isolated candidate preserves the 28 October combined release and the 29 October main-branch public-site changes. Reseller operations and staff invitations remain the user-approved first-release exclusions. No new providers, helpdesk platform or generic conversation rewrite is introduced.

## Failure hypotheses and operating proof

| Support area / features | Failure to prevent | Response and acceptance | Current evidence boundary |
|---|---|---|---|
| Commercial promises (F01; CP01 OP08) | A buyer relies on an unsupported latency, scale or outcome promise. | Check visible offers against dated measurements; remove unsupported guarantees. | No new capacity or SLA acceptance. |
| Sales and support intake (F02; CP01) | A lost response or browser-storage failure creates duplicates or silently loses an enquiry. | Persist the idempotency reference before dispatch; success needs a durable receipt; keep unsent draft and email fallback. | Storage-failure reproduction and focused repair; actual mailbox delivery still unaccepted. |
| Account and access recovery (F03 F04 F05; CP08 CP05) | A legitimate customer cannot regain access, or recovery bypasses proof and identity ownership. | Test signup, password reset, TOTP, single-use recovery codes, passkeys, session cutoff, account switch and operator step-up. | Recovery-code UI and atomic reset limiter repaired; emergency proof/concurrency repair locally verified; actual recovery integration remains open. Staff invitations excluded from first release. |
| First useful call and saved settings (F06 F13; CP08 CP09 OP04 AG01) | Saved settings look valid but a fresh tenant cannot make an authorized call. | Create a genuine fresh tenant, save profile and trunk selection, prove routed call and visible receipt. | Fresh database missing active settings contracts reproduced; schema repair locally verified, complete release CI pending. Actual first call remains open. |
| Purchase, invoices and cancellation (F07 F08 F09 F10 F11 F12; CP02 CP03 CP04 CP06) | A charge has an ambiguous outcome, wrong interval, duplicate credit, missing invoice or ignored cancellation. | Keep purchase reference; reconcile authoritative provider events and immutable ledger; verify amount/currency/interval, refunds and renewal end. | Support reference rendering locally verified; complete provider lifecycle awaits final owned Stripe account and exact release acceptance. |
| Model and knowledge (F14 F15 F16; AG01 AG02 AG03 AG04) | Follow-up answers lose a qualifying condition, use another scope, or promise unavailable help. | Retain complete short sources; reread oversized sources; bind memory to tenant/campaign/catalog; evaluate exact deployed profile and capability set. | Three privacy/knowledge defects reproduced and repaired; Q43 unsupported handoff promise still fails retained provider evidence. |
| Voice conversation (F17 F18 F19 F20; AG04 AG05 OP02 OP03 OP08) | Recognition, silence, interruption or synthesis failure strands a caller or triggers premature hangup. | Evaluate complete calls including noisy audio, interruption, partial speech and provider outages, plus human-rated outcomes. | Captured Q43/H13 end markers replayed through turn finishing; actual listening/telephone quality gates remain open. |
| Call admission and routing (F21 F22 F24 F25 F27; OP01 OP02 OP04 OP05 OP07) | Wrong identity, stale trunk health, schedule or retry causes an unauthorized or duplicate call. | Use owned DID/trunk and destinations; prove tenant routing, caller ID, DST/pause, unknown origination and explicit retry. | Existing safeguards retained; designated live routes and controlled calls still required. |
| Human escalation (F23; OP06 AG06) | The assistant says a person is connected when no transfer can complete. | Prove availability, destination ownership, bridge receipt, timeout and return-to-caller behavior; claims must follow actual capability. | Unsupported Q43 wording remains a blocker; no new transfer provider or scripted dialogue introduced. |
| Privacy and data lifecycle (F26 F34 F45; OP05 OP07 OP10 OP11 CP01 CP08) | Opt-out, consent, retention, export or deletion is ignored; private content leaks to logs. | Test caller opt-out and consent, tenant isolation, protected export, expiry/deletion and access audit; verify approved public claims. | Raw malformed-model logging removed; knowledge argument logs bounded; historical emergency OTP cleanup locally verified. Live lifecycle acceptance remains open. |
| Contacts, evidence and external actions (F28 F29 F30 F31 F32; CP09 AG04 AG05 AG06) | An inferred lead fact becomes truth or an uncertain external effect is retried twice. | Preserve evidence and corrections; require authorization and durable receipts; reconcile uncertain email/calendar/CRM effects before replay. | Existing boundary/replay tests retained; actual owned connector delivery and operator reconciliation not established by unit tests. |
| Customer notices and on-call (F33 F41; CP06 CP07 OP09) | A receipt exists but no customer/operator is notified, or alerts flood the wrong recipient. | Verify tenant routing, preferences, email ownership, alert deduplication and delivered/acknowledged notification with a named rota. | No tested support mailbox, alert recipient or on-call rota inferred from source configuration. |
| Support diagnosis and reporting (F35 F36; CP04 CP09 AG06 RG03) | Support cannot correlate a complaint to the call/action/payment or mistakes missing data for zero. | Trace customer reference through authorized console and immutable receipts; resolve failed/unknown effects without guessed database edits. | Billing correlation improvement locally verified; operator end-to-end incident drill still required. |
| First-release exclusions (F37; CP05) | A future reseller feature is sold as available. | Verify deployed reseller and staff-invitation exclusions; retain cosmetic branding preview separately. | User-approved exclusions retained; these capabilities are not counted complete. |
| Accessible recovery paths (F38; CP01 CP02 CP06 CP08 CP09) | A keyboard/mobile user cannot recover or an error erases their work. | Check labels, focus, keyboard, narrow viewport, loading/error/empty states and screen reader instructions on supported browsers. | MFA recovery input and contact failure states covered in DOM tests; full hosted/browser accessibility acceptance remains open. |
| Quota, limits and capacity (F39 F40 F42; OP01 OP02 OP08 OP11 CP04) | Concurrent last-minute admission overspends, provider limits cascade or scaling exceeds database/process limits. | Measure bounded admission, dependency outage, sustained load and pool budget for the actual deployment; ratify capacity targets. | No new sustained capacity, HA or service-level claim. Process-local call state and pool multiplication remain constraints. |
| Backup, restore and release (F43 F44; OP10 OP12) | An upgrade starts from the wrong schema or a backup cannot restore after host loss. | Qualify fresh install and old-to-new forward migration with real PostgreSQL, data preservation, least privileges, reboot and failure refusal; restore on separate destination. | New four-table/tenant-column repair locally verified; exact-baseline Ubuntu rehearsal pending CI; off-host restore and live cutover remain open. |

## Support case handling

1. Capture UTC time, customer-visible support reference, tenant/account ID and relevant enquiry, purchase, call, action or invoice ID. Establish identity through the existing authenticated process. Never request passwords, OTPs, recovery codes, API keys, card numbers or unredacted provider payloads. Preserve only the evidence needed to resolve the case.
2. Triage by observed impact. Treat suspected cross-tenant disclosure, account compromise, unauthorized calling or ambiguous financial effects as urgent incidents. Use the existing scoped pause/hold controls and retain the decision trail. A named operator, backup and response target must be assigned before a managed-support promise is sold; this document does not invent an SLA.
3. Read the authoritative receipt/state before changing anything. A timeout means uncertain delivery, not a failed effect. Keep the original idempotency reference. Do not start a second payment, call or connector action merely to clear an error.
4. Route account recovery to proof-preserving recovery controls; billing to purchase/provider/ledger reconciliation; call issues to tenant route and call lifecycle receipts; AI answer issues to the exact saved model/profile/capability/source versions and complete conversation; privacy requests to the scoped export/deletion/retention workflow.
5. Use authorized console actions with their audit receipts. Emergency access requires fresh verified proof and independent approvals; it is not a substitute for missing recovery infrastructure. Unknown external effects require reviewed reconciliation. Avoid guessed database edits and broad runtime privileges.
6. Tell the customer what is confirmed, what remains uncertain and the next actual action. Confirm resolution from a receipt or reproduced successful journey, retain an owner and follow-up record, and check for recurrence. A log line or email send attempt is not proof of customer receipt.

## Required scenario acceptance

All scenario statuses below are the original end-to-end statuses at review time. Targeted repairs and retained unit evidence do not silently close them.

| Scenario | Situation | Packages | End-to-end status |
|---|---|---|---|
| S01 | Caller is not an existing customer and has no card-payment setup | AG03, AG04 | not_run |
| S02 | Caller says no to email but continues asking questions | AG04 | not_run |
| S03 | Caller asks what the previous question meant | AG04 | not_run |
| S04 | Unclear name, email or phone digit | AG05, AG04 | not_run |
| S05 | Caller interrupts the opening or corrects an answer | AG04, OP03, OP08 | not_run |
| S06 | Background speech, echo, silence or hold music | OP03, OP08 | not_run |
| S07 | Knowledge is missing, stale, weakly matched or contradictory | AG02 | not_run |
| S08 | Caller requests a person when transfer is unavailable | OP06, AG06 | not_run |
| S09 | Caller requests no more calls | OP07, OP05 | not_run |
| S10 | Caller requests an unrelated or sensitive action | AG02, AG06 | not_run |
| S11 | Provider fails before any speech, or after partial speech | OP03, AG04 | not_run |
| S12 | Email/calendar/CRM request times out after sending | AG06 | not_run |
| S13 | Connector account is changed or authorization revoked | AG06 | not_run |
| S14 | Selected SIP trunk is down, or health is stale | OP04 | not_run |
| S15 | IP-auth trunk ignores OPTIONS but can accept inbound | OP04 | not_run |
| S16 | Metering is unavailable or the last available minute is being used concurrently | OP01, OP02 | not_run |
| S17 | Worker/Redis/network fails during origination or queue handoff | OP05 | not_run |
| S18 | Payment succeeds but activation handler fails | CP03 | not_run |
| S19 | Customer cancels, disputes an invoice or requests deletion | CP04, CP06, OP07 | not_run |
| S20 | Two different customers use the same browser | CP07, CP08 | not_run |
| S21 | Call scheduling crosses timezones, DST or a campaign pause | OP05 | not_run |
| S22 | A recording is requested, expired, missing or being erased | OP07 | not_run |

## Release gates

| Gate | Required area | Recorded status |
|---|---|---|
| GT01 | Commercial truth | not_run |
| GT02 | Core scenario quality | not_run |
| GT03 | Real telephone acceptance | not_run |
| GT04 | Conversation delay | not_run |
| GT05 | Interruption | not_run |
| GT06 | Perceived voice quality | not_run |
| GT07 | Useful lead evidence | not_run |
| GT08 | Actions | not_run |
| GT09 | Telephony identity | not_run |
| GT10 | Paid lifecycle | not_run |
| GT11 | Quota and capacity | not_run |
| GT12 | Failure recovery | not_run |
| GT13 | Monitoring | not_run |
| GT14 | Recovery and release | not_run |
| GT15 | Usability and exit | not_run |

## Inputs that source changes cannot supply

- Secure administrative access for the existing Ubuntu host and a designated separate production PostgreSQL destination, including protected credential references. Do not put secrets in this report.
- An owned off-host backup/restore destination, independent restore observer and measured recovery objective.
- Owned controlled calling destinations, operational trunk/DID route, connector test accounts and approved caller/recording profile.
- A functioning support mailbox, named primary/backup operator, alert recipient, approved escalation policy and actual delivered/acknowledged alert drill.
- Exact-release human telephone evaluation, measured latency/interruption/quality/capacity targets and complete paid/customer lifecycle acceptance.
- Final Stripe account integration remains intended last; the other open gates above must be completed first.

See [execution tracker](execution-tracker.json), [frozen plan](../production%20ready.md), and the [repair and validation record](../sessions/2026-10-29-customer-support-premortem.md).
