# Inbound Campaign — Draft Retention Fix

**Date:** Wednesday, 16 September 2026
**Project:** Talk-Leee
**Scope:** Frontend only
**Status:** Complete — implementation and manual testing both done

## Original Issue

In the inbound campaign creation flow, if creating a campaign fails on any page (API error, validation failure, timeout), returning to the previous page must retain all previously entered form data and the multi-step page history — nothing should be cleared or reset. The user should be able to move back, correct input, and resume.

## Verification Finding

Two different things were being asked for, and only one was in place.

- **Already working:** in-place failure retry. On both step 1 (`CampaignWizard` / `CampaignForm`) and step 2 (`InboundCampaignForm`), a failed create call only set an error message — it never cleared the fields the user had typed. Retrying without leaving the page always worked.
- **Broken:** navigation between the two real pages of the flow. Step 1 and step 2 are two separate mounts of the same route, switched by a `campaign_id` URL param. All step-1 field values lived only in local React state with no persistence anywhere (no context, store, URL, or storage). Pressing the browser Back button from step 2 dropped `campaign_id`, remounted a blank step 1, and lost everything. There was also no in-flow link to go back from step 2 to step 1 at all — only an exit link to the campaigns list.

## Work Completed

### Round 1 — Draft persistence across all four files

Added sessionStorage-backed draft retention so a real navigation away and back no longer loses data, without touching the existing in-place retry behavior.

- **`app/inbound-campaigns/new/page.tsx`** — made the owner of the key scheme. Generates a `draft` id once per fresh visit, reflects it into the URL (so browser Back restores it) and mirrors it to a sessionStorage pointer (so the new "Back to campaign details" link, which doesn't carry the id in its URL, still finds it). Passes the resulting key down to the step-1 form. Clears both drafts once step 2's create call actually succeeds.
- **`campaign-wizard.tsx`** — persists name, company, persona, agent names/genders, voice fields, goal, schedule, brief, lead-field selections, and the current wizard step. Restores on mount. The uploaded knowledge file can't be serialized, so only whether one was attached is remembered, driving a "please re-attach" notice.
- **`campaign-form.tsx`** — same treatment for the classic form's equivalent fields (create mode only; edit mode is unaffected).
- **`inbound-campaign-form.tsx`** — persists the entire step-2 form object, keyed by campaign id (create mode only). Added a new "Back to campaign details" link alongside the existing "Back to inbound campaigns" link, so there's finally an in-flow way back to step 1.

### Round 2 — createdCampaignId persistence to prevent duplicate campaigns

Flagged as a follow-on risk: if step 1's `createCampaign` call succeeded but a later call (saving lead fields, uploading knowledge) failed, the code already retried against the same campaign id in memory to avoid a duplicate — but that id wasn't part of the persisted draft. Once "Back to campaign details" made real navigation common, a resubmit after navigating away and back would have created a second campaign. Added `createdCampaignId` to both step-1 draft types, seeded it from the restored draft, and included it in the persistence write. The create/retry logic itself was not changed — only made durable across navigation.

### Round 3 — Clearing drafts on exit-to-list links (option b)

Raised as a separate, deliberate decision: an abandoned draft would otherwise silently reappear if the user came back to "New inbound campaign" later in the same tab. Chose option (b) — clear on explicit exit — over leaving it as-is or adding a new "Start fresh" button. Added `onClick` handlers to the two "Back to inbound campaigns" exit links (step 1's header link, and the existing link inside `InboundCampaignForm`, gated to create mode) that clear the pointer and both draft keys before navigating. The "Back to campaign details" link deliberately does not clear anything — it's a return within the flow, not an exit from it.

### Round 4 — Moving DRAFT_POINTER_KEY to a shared module

The sessionStorage pointer key had been duplicated as a literal string in both `new/page.tsx` and `inbound-campaign-form.tsx`, documented with a comment that the two copies had to be kept in sync. Extracted it into a new file, `lib/inbound-campaign-draft.ts`, and imported it in both places. The value itself and all surrounding logic were left untouched — this was a pure move.

## Files Changed

- `Talk-Leee/src/app/inbound-campaigns/new/page.tsx`
- `Talk-Leee/src/components/campaigns/campaign-wizard.tsx`
- `Talk-Leee/src/components/campaigns/campaign-form.tsx`
- `Talk-Leee/src/components/inbound/inbound-campaign-form.tsx`
- `Talk-Leee/src/lib/inbound-campaign-draft.ts` — **new file**

## Automated Checks

Run after every round, same results each time:

- `npm run typecheck` → clean, no errors
- `npm run lint` → clean, no errors
- `npm run test` → **475 tests, 473 pass, 0 fail, 2 skipped** (skips are pre-existing, DB-only tests unrelated to this work)

## Constraints Respected

- No git commands run (no add, commit, push, branch, checkout, stash, reset) — only read-only `git status` / `git diff` for verification.
- No backend, API route, database, or server-side logic touched — frontend only, across all four rounds.
- Campaign creation and save logic unchanged throughout — every round added browser-side draft retention only; no round modified the create/retry request logic itself.
- No refactors, renames, or reformatting beyond what each round's stated task required.
- No new dependencies added.
- Existing in-place failure behavior preserved in every round.
- Existing success-path navigation preserved in every round.
- Nothing committed at any point.

## Manual Testing — COMPLETED

| # | Test case | Result |
|---|---|---|
| 1 | Fill step 1 → forward → browser Back → fields intact | Pass |
| 2 | Step 2 → "Back to campaign details" → fields intact | Pass |
| 3 | Step 2 fails on bad input → fields stay, retry works | Pass |
| 4 | Complete a campaign → New inbound campaign → blank form | Pass |
| 5 | Upload a file → forward → back → re-attach banner, no crash | Pass |
| 6 | Two tabs open → drafts don't bleed across | Pass |
| 7 | Step 1 partially fails → navigate away → come back → resubmit → one campaign, not two | Pass |
| 8 | Half-fill → "Back to inbound campaigns" → New inbound campaign → blank form | Pass |

## Known Limitations

- **File re-attach required.** A `File` object cannot be serialized into sessionStorage, so a knowledge-file upload does not survive a real navigation — the user sees a clear notice and must re-attach it. This is a hard constraint of the storage mechanism, not an oversight.
- **Tab-scoped drafts.** sessionStorage is per-tab by design; drafts do not survive a closed tab and are never shared across tabs or devices. This was an accepted tradeoff from the start, not a bug.
- **Per-file duplicated key logic.** The step-1 and step-2 draft key formats are small duplicated literals across the three files that need them (only the pointer key was consolidated into a shared module). This keeps the change minimal and scoped rather than introducing a larger shared abstraction.
- **Wizard/classic toggle shares one draft key.** Both `CampaignWizard` and `CampaignForm` read and write the same step-1 draft key, since the page can render either one. Toggling between them mid-draft carries over overlapping fields (name, company, persona, agent names, brief, lead fields) but each form's own "goal"-shaped field is labeled slightly differently between the two, so a mid-session toggle could repopulate a field with content typed under a different label. Low-risk, since switching form styles mid-draft is rare.

## Ready to Ship

Cleared for commit and merge.
