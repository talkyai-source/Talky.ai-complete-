# Reliable lead capture: simplified plan and premortem

Status: design and release checklist. Implementation is underway in an isolated checkout; the complete flow is not implemented or production-verified. This revision replaces the earlier architecture in this file. It keeps the original collect/save/show scope and adds concrete failure checks while reducing moving parts.

## What must work

Every supported detail the caller provides should become either a saved lead field or a visible item needing review. Information must not disappear because a call ends, a phone is unconfirmed, or a sales summary labels the client unqualified. The system must keep the caller and referred people separate and distinguish requested follow-up from an executed action.

No system is failure-proof. The release standard is that the known failures below are prevented or become visible, recoverable states rather than silent success.

## Keep the design small

Use three application components:

1. **One capture service** to identify the person, validate fields, apply corrections, persist evidence, and update the existing lead record. Both live processing and post-call recovery call this service.
2. **One job handler in the existing worker infrastructure** for general detail extraction and final reconciliation. No new broker, independent microservice, or agent team.
3. **One shared Leads detail panel** used by the existing campaign, Contacts, and call views. No separate CRM or competing summary-only data store.

Reuse the contact field registry, campaign field definitions, phone/email state machine, `call_lead_details`, `leads`, existing actions/reminders, and authentication. Use a small allowlisted field-to-storage mapping, not a configurable workflow engine.

Add only the storage needed for reliable field evidence/history and typed follow-up requests. A referred person uses a stable call-scoped subject key on captured details; a universal person/organization identity graph is unnecessary. Retain a reference to a real lead only after explicit matching or conversion.

Persist capture work through existing durable jobs if they can be enqueued in the same transaction as the final transcript event. If that guarantee is absent, add one narrow database job/outbox table. Do not implement competing retry systems.

The initial release collects, verifies, saves, and shows details and follow-up requests. Connect existing supported scheduling actions to those requests; implementing a new WhatsApp provider, automatic referral outreach, or automatic historical-data promotion is outside this release. These limits do not prevent saving a WhatsApp preference or showing a manual handoff.

## Fields and statuses

### Supported fields

- Contact: name, company, role, phone, email.
- Business context: current provider, relationship status, stated need/issue, relevant campaign fields.
- Follow-up: original requested wording, day/date, time, timezone, preferred channel, purpose, owner, and permission evidence.
- Referral: separate subject, name, business/need, candidate phone/email, referring contact, and review/contactability status.

A campaign field is only offered as automatically captured when it has a supported type, validation, destination mapping, and test coverage. No field should appear configurable while its saving path is missing. Use existing custom fields for supported campaign-specific extras; no column per industry question.

### Minimum evidence

Each captured field keeps subject key, source call, final caller transcript event/quote, raw and normalized values, validation result, confirmation result, revision, and origin (caller, import, or manual edit). Small field revision records preserve corrections; this is an audit trail, not a general event-sourcing platform. Existing best-effort security logging is not a substitute for transactional field history.

The existing lead record holds accepted current values. Candidate details stay in call details/review storage. Evidence, current values, and the processing checkpoint update in one transaction through the capture service. UI and summary are readers of this state, not independent writers of contact truth.

Show simple labels: **Captured**, **Confirmed**, **Needs review**, **Declined**. Unknown has no value. Keep the specific review reason underneath: invalid format, missing country, unclear person, conflicting correction, or incomplete readback. Do not equate a parseable number with an agreed contact.

Follow-up labels: **Requested**, **Needs clarification**, **Scheduled**, **Completed**, **Failed**, **Cancelled**. Processing leases and provider retry states remain operational details, not extra labels for users.

## Runtime rules

### Collect without slowing normal conversation

1. Persist each finalized caller transcript event with a stable sequence ID and durable pending-capture marker. Provisional STT updates are not committed facts.
2. Handle critical phone/email parsing and readback state immediately. Use explicit person context and contextual intent recognition, not one exact phrase. General semantic extraction runs asynchronously in short coalesced batches: at most one in-flight extraction per call, then process newer pending events.
3. The extractor returns only supported fields with caller evidence and subject references. It cannot write the database, mark confirmation, grant permission, qualify a lead, or execute actions itself.
4. Validate/merge via the shared service. Late output is checked against field revision and source sequence, including across calls; worker completion time never decides which value is newer.
5. At hangup, process any remaining finalized events and reconcile against the persisted conversation. A periodic recovery sweep catches calls abandoned by a process crash as well as normal hangups.

Bound model timeouts and retries; back off on repeated failures and leave a visible processing/review state. An unavailable extractor must not block all call replies. If transcript persistence itself fails, do not claim a field was saved; expose the failure and retry. Recovery cannot recreate audio/transcripts that were never durably recorded.

### Correct the exact value, for the correct person

- Track original contact and referral independently. Pronouns such as “his” use established context; unclear identity gets one clarification rather than a guessed mapping.
- “Use this number for WhatsApp” can bind to a known dialed number only after the caller explicitly agrees to that destination/channel. A phone number existing on the contact is not WhatsApp permission.
- Readbacks are generated from the pending canonical value. Store which subject and field revision was actually read aloud.
- Caller affirmation confirms only that exact readback, and only if the complete readback was delivered. An interrupted or partly played number remains pending.
- Treat suffix corrections as replacements only when the target segment is clear. “Five five” without enough context needs a short clarification; never blindly append digits.
- An explicit correction revokes use of the disputed old contact while the replacement is unresolved. Retain the old value for history, but do not continue scheduling against it.
- Manual edits remain protected. Concurrent caller changes create a review conflict, not silent overwrite.
- A new call's accepted information refreshes an existing lead independently of `is_lead`. Older call extraction cannot overwrite a newer update.
- Normal caller-stated business facts do not require repetitive readbacks. Reserve confirmation for contact destinations, ambiguous identity/time, and consequential actions.

### Make success claims true before speaking

For contact confirmation and follow-up execution, choose the short status sentence from actual capture/action results before TTS. A guard that checks text after it has streamed is too late. Normal dialogue can stream; successful save/schedule/send claims cannot be left to prompt instructions alone.

Use “requested” for a saved handoff. “Scheduled” requires an action receipt. A provider timeout after submission is an unknown execution result: reconcile the existing action/idempotency key before retrying, because a blind retry can double-book. Provider acceptance of a message is not delivery; display only the status the provider proves.

### Handle time and changing minds

Preserve original wording. Resolve a timestamp using call time and known client/campaign timezone, including daylight-saving rules. The server or operator timezone is not an acceptable guess. If “Thursday at four” is ambiguous, show the request and ask a short clarification before scheduling.

Keep general availability separate from a one-time appointment request. Date/time/channel corrections replace the pending request. A later “don't contact me” cancels an unexecuted request and applies existing suppression rules; the earlier agreement must not survive as permission. Rescheduling an already scheduled action uses the existing service, and the UI retains the old schedule until the change is acknowledged.

## Premortem: assume the release failed

| Failure we would see | Likely cause | Small prevention/recovery | Required check |
| --- | --- | --- | --- |
| “His number” is missed again | Narrow phrase matching | Contextual capture intent and explicit subject state; unmatched contact-shaped input stays reviewable | Replay actual request, volunteered details, synonyms, and unrelated invoice numbers |
| Friend replaces original client | Subject guessed or shared phone slot | Separate subject keys; clarify ambiguous person; explicit conversion/dedup review | Two referrals, same surname, and switching back to caller |
| Number is heard correctly but repeated wrongly | Model invents its own readback | Canonical readback and value-bound confirmation before saving | Double/triple digits, suffix correction, no dropped final digit |
| “Yes” confirms an unheard/wrong value | Partial playback or stale confirmation state | Match subject/revision and full delivered readback; reject unrelated affirmations | Interrupt halfway, then say yes; change number before old result arrives |
| Wrong number remains usable after rejection | Old confirmed contact considered permanently valid | Mark explicitly disputed value unusable until resolved | Correct a previously confirmed number, hang up before confirming replacement |
| New correction gets overwritten | Slow old extraction or concurrent call/manual edit | Source ordering, revisions, protected manual edits, transactional compare-and-update | Finish old worker after newer call/manual correction |
| Hangup loses details | Untracked tasks die before saving | Durable transcript + capture job, retryable checkpoint, abandoned-call sweep | Kill worker/process at each persistence boundary |
| Same detail/referral/task appears twice | Retry without stable identity | Stable event/subject keys and idempotent writes/conversion/actions | Deliver job twice, crash after commit before acknowledgement |
| One bad phone loses all other details | Whole result rejected | Validate per field; save good fields and expose bad one | Valid time/provider with malformed phone |
| Agent slows down or calls pile up | Extraction on every interim transcript | Final events, coalesced batches, one in-flight job per call, bounded concurrency | Sustained calls plus slow/failing extraction provider |
| AI saves a suggestion as client fact | Agent text or KB instructions treated as evidence | Caller span references, trusted field allowlist, no action authority in extractor | Agent proposes a time; caller never agrees; injected extraction instructions |
| “Thursday 4 PM” becomes wrong instant | Missing timezone/date or DST ambiguity | Preserve wording; clarify before scheduling | UK DST boundary, midnight, past Thursday, AM/PM ambiguity |
| Declined follow-up is still scheduled | Earlier consent overwrites later refusal | Later explicit cancellation wins; recheck immediately before action | Agree then withdraw or change channel; opt-out before queued action runs |
| Agent says booked but no action exists | Generated promise mistaken for receipt | Pre-speech status gate; request and execution remain separate | Database failure, unavailable connector, ambiguous provider timeout |
| Client is hidden as “no interest” | Summary label controls capture/visibility | Facts and follow-up saved independently; contradictory labels flagged | Follow-up request plus unqualified summary; existing qualified contact |
| Old notes or misleading blanks remain in UI | Stale cache or failed worker shown as empty | Read saved state; refresh after commit; show processing/failure; update current note separately | Reload after job, repeat call, failed extraction, paginated Leads filters |
| Data leaks or deleted facts reappear | Missing tenant scope or late worker resurrection | Tenant predicates/FKs and permissions; job rechecks deletion/version before commit | Cross-tenant subject IDs, deleted contact while extraction is running |
| Rollout/backfill contacts people unexpectedly | Historical processing triggers actions | Shadow replay saves no canonical changes/actions; explicit later promotion | Replay old transcripts twice and assert zero external actions |

Extra notes on semantic extraction: a cited sentence is necessary but does not prove the model interpreted it correctly. Keep deterministic validation for contact/time formats, preserve uncertainty for unsupported interpretations, and verify extraction against varied examples. Post-call recovery may propose missed details; it must never retroactively invent live confirmation.

## Knowledge premortem: a successful search can still produce a wrong answer

Use one retrieval and source-rendering implementation for automatic injection, model tools, realtime calls, and the dashboard's test question. Provider adapters may invoke it differently; they must not implement different ranking rules. Keep lookup independent of a second model request on ordinary voice turns.

| Failure we would see | Small prevention/recovery | Required check |
| --- | --- | --- |
| Lookup is fast but returns the wrong topic | Search relevant source passages; use previous-turn context only when the current question needs it | Rewards followed by machine rental; compare the actual text supplied to the model, not merely hit counts |
| Upload appears ready but most facts are unreachable | Validate section parsing and bounded passages before publishing; expose processing/failure status | Missing spaces in headings, long sections, empty sections, tables, and interrupted ingestion |
| Price survives trimming but its restriction disappears | Keep source qualifications attached to the selected fact; suppress exact quotes when the required conditions cannot fit | Put eligibility, historical status, currency, and exclusions before and after a price, including across passage boundaries |
| Old or conflicting documents become a confident current answer | Preserve source identity/version and explicit restrictions; report unresolved conflicts; never infer current approval from upload time | Two sources with different figures; a newer upload containing historical information |
| Dashboard test works but live call does not | Test the shared lookup AND each provider's actual context/tool delivery, with its real budgets and fallback behavior | Automatic injection, supported tools, realtime path, and provider fallback all retain the expected evidence |
| Edited or disabled knowledge remains usable unexpectedly | Define snapshot behavior explicitly: active calls use their admitted version; new admissions and dashboard tests see committed changes; cache keys must respect that version | Edit/disable/delete between calls and across worker processes; verify no unauthorized tenant or campaign reuse |
| Retrieval failure makes the agent invent an answer | Distinguish no matching evidence from timeout/error; give the response layer an explicit unavailable state and bounded fallback | Database timeout, ranking failure, empty source, unrelated question, and unsupported commercial terms |
| Concurrent calls make retrieval stall voice | Bound source size, CPU work, and concurrency; keep ranking off the voice event loop; measure cold and warm lookups | Representative simultaneous calls with different questions, cache misses, oversized input, and cancelled lookups |

An admission snapshot is intentional consistency during a call, not permission to keep serving withdrawn material indefinitely. Normal edits apply to new calls; urgent withdrawal must use a supported call-stop or snapshot-revocation mechanism before it is advertised as immediate. Do not silently mix mutable and pinned knowledge during one call.

For the actual call, retrieval should explain the rewards product and the relevant rental conditions. It must not invent approved current cashback or rental figures: the inspected sources themselves mark some terms as historical or requiring verification. Correct retrieval cannot repair incorrect source facts.

The acceptance fixture should contain redacted questions and representative source passages, with the expected relevant evidence and required caveats. Avoid tests that assert only a particular numerical score or hard-code one brand's ranking. Also test unrelated domains so fixes generalize beyond this call.

## What Leads shows

Use the existing list with a few useful columns: identity, current need/provider, latest call result, next follow-up time/channel, and Needs review indicator. Fetch with server-side pagination and filters; no per-row call-history queries.

Clicking a lead opens the same shared panel everywhere:

- Contact/business and current accepted facts.
- Need, current provider, and relevant campaign fields.
- Follow-up purpose, time/timezone, channel, owner, and actual status.
- Separate referrals and reviewable contact details.
- Source call/quote and correction history, with manual correction controls.

Follow-up/referral prospects are visible even if not sales-qualified. A captured weekday alone does not imply sales interest. Needs review does not mean confirmed, and “nothing provided” is distinct from “still processing” or “processing failed”.

Do not duplicate facts into a separate UI-only store. Manual edits use expected revisions. Show unresolved caller and referral details in place, rather than hiding them until validation succeeds. Initial refresh can use existing polling; add realtime transport only if the existing mechanism cannot meet the measured UI requirement.

## Qualification and summaries

Retain sales qualification as a separate decision. A request for information or follow-up is not automatically a qualified sale. Equally, it must not vanish because the outcome is no_interest.

Build summaries from the transcript, saved facts, unresolved candidates, and executed-action results. Check obvious contradictions such as known requested timing reported as unknown, or an active follow-up request paired with no-interest wording. Apply later withdrawal/cancellation first; never force a callback label just because a date was mentioned earlier. Ambiguous qualification remains unknown/reviewable.

Update latest accepted needs and follow-up notes for existing leads while preserving historical notes. Do not reuse the first-time `is_lead=false` qualification update to maintain current client information.

## Implementation in four slices

1. **Storage + read path:** verify current deployed baseline in an isolated checkout, preserve existing uncommitted work, add evidence/status/revision support, follow-up request fields, safe migration, and shared API contract. Render a saved example correctly in the shared Leads panel.
2. **Collection + correction:** connect live canonical phone/email capture, contextual subject handling, general extraction, one shared writer, and durable retries. Verify original caller/referral separation and record updates after a second call.
3. **Recovery + follow-up:** reconcile missed events at hangup/crash, enforce receipt-based action status and summary consistency, and expose processing/review states and filters.
4. **Failure tests + staged release:** replay the actual call plus adversarial cases, test worker/network failures, run backend/API/UI checks, and validate a new end-to-end call on the selected test campaign through the supported release procedure.

No new scheduler, generalized identity platform, pluggable merge-rule language, custom message bus, parallel summarizer source of truth, or automatic historical outreach. A necessary additive table is preferable to clever reuse that hides typed follow-up data in unvalidated notes.

## Release gates

All gates must pass; unit tests alone are insufficient:

1. The actual call produces Worldpay, the stated need, Thursday 4 PM wording, WhatsApp preference, and a separate Mr Smith referral in Leads. The example number may be invalid: review status is correct; forcing it into a confirmed phone is not.
2. A separate valid-number scenario completes correct readback, explicit confirmation, durable save, reload, and use of the saved value on a later call.
3. Replaying jobs after a crash does not duplicate fields, subjects, requests, or actions. No later worker overwrites a newer correction/manual edit.
4. Disconnect before confirmation leaves the candidate visible and unusable for outreach. Disconnect after commit retains the saved facts.
5. A follow-up remains visible on an already-qualified lead and an unqualified prospect. Cancellation wins over earlier agreement. No action is labeled scheduled/completed without the corresponding receipt.
6. Calls, Contacts, and campaign Leads show the same values and review status. Pagination, permissions, tenant isolation, deletion, and manual conflicts are tested.
7. Baseline-versus-enabled testing shows no new extractor model round trip on ordinary voice turns. Worker backlog, failure rate, persistence latency, and extraction lag are measured; overload degrades to visible pending work rather than blocking voice.
8. A new end-to-end test call proves speech → capture → database → Leads, including at least one correction and one referral. A supported action is tested separately with its real receipt; unavailable channels remain manual requests.
9. Exact-call retrieval replay and cross-provider delivery tests preserve relevant source facts and restrictions. Missing or conflicting approved terms produce an honest limitation, not a fabricated quote. Source changes follow the documented snapshot behavior.
10. Recovery is demonstrated by interrupting a running worker, restarting it, and reloading Leads. Mocked unit tests alone do not prove database atomicity, migration compatibility, or durable recovery.

Start behind a per-tenant/campaign flag. Shadow-test old calls without contact writes or external actions, review differences, then enable the test campaign. Rollback disables new capture/action processing while preserving stored evidence and compatibility. Do not treat shadow-mode output as caller-confirmed truth.

Diagnostics should expose capture-job age, last persisted event sequence, retry/failure reason, pending review fields, and action receipt state using IDs/statuses rather than logging raw contact values. Choose fixed alerts and performance budgets after measuring the release baseline; avoid arbitrary promises about universal accuracy or zero data loss.

## Key decision

Reliability comes from one owner for each saved fact, explicit person identity, value-bound confirmation, durable retry, and visible uncertainty. Keep those guarantees; simplify everything that does not protect them.
