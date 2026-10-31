# AG06 bounded independent frontend review

Read-only review of the working candidate after baseline `71cdeefa59a0f786f236d3a3d25ba592470ddf05`. This is source-contract evidence, not a browser, provider, or deployment test. The parent session owns final source binding and verification after corrections.

Reviewed the saved-actions page/component, API endpoint mapping and response schemas, shared proposal result/card, text and voice dashboard handlers, and Leads CRM delivery references against their actual backend routes/projections. No unrelated frontend baseline audit was performed.

## Findings passed to the owners

1. **Voice dashboard receipt recovery was incomplete.** `voice-mode.tsx` connects to `/assistant/voice` and had added `proposal_status`, but `assistant_voice_ws.py` had no matching branch. Its apply handler still emitted the old applied/error-only envelope, so Check status was ignored and uncertain outcomes lost durable status/identity. The existing shared receipt projector and read-only recovery helper are the bounded correction; the backend owner accepted this finding. No new effect route is needed.
2. **Receipt fields were being discarded.** `public_action_receipt` emits `provider_status` and `child_action_id`; the draft `ActionReceiptSchema` instead declared `status` and omitted both actual fields. Zod consequently removed saved effect status and the inner receipt reference. The frontend owner is aligning the schema and visible references.
3. **A queue acknowledgment could look complete.** Callback drain records a completed outbox action with provider status `queued`, which proves queue handoff, not completion of a telephone call. The draft history rendered plain Completed. Completed non-email rows with `confirmationAllowed=false` also lacked an unverified-outcome qualification. The frontend owner is adding queue-specific and unverified wording with regression tests.

These are observations of the reviewed draft. This note does not assert that later corrections passed; their owner’s final tests and source binding provide that evidence.

## Contracts that matched in the reviewed draft

- The live list uses `/assistant/actions`, the actual `actions` pagination envelope, and the backend's filter/sort names.
- The proposal mapper preserves unknown, pending/running, scheduled, and cancelled rather than collapsing them into success.
- The new history controls read, filter, inspect, and export saved receipts. No hidden retry or fabricated queued mutation was found there.
- Text dashboard Check status sends the read-only `proposal_status` message and retains the proposal identity.
- Leads CRM references match the tenant-bound latest-call projection; raw historical provider errors are replaced with bounded status text by the backend. Displayed references do not claim that unknown effects were reconciled.

Remaining scope limits: no live email/calendar/call effect, no actual remote-account verification, and no operator reconciliation mutation was tested or introduced by this review. Unknown effects must remain held. The removal of an unsupported retry acknowledgment is containment, not evidence that automatic retry is complete.
