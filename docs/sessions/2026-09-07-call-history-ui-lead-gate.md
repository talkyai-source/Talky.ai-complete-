# 2026-09-07 — Call History UI fixes, lead-qualification gate, Salesforce credentials live

Owner requests (WhatsApp screenshots, 2026-09-07 17:03–17:09 PKT):

| # | Ask | Change |
|---|---|---|
| 1 | Warm/cold colours should also show on the phone icon in the Phone column | `LeadAccentIcon` wraps the status icon in a ring coloured by the row's lead type (same palette as the Lead type select: cold red, warm orange, hot green, follow-up sky). Both desktop and mobile layouts. |
| 2 | The post-call-form icon should show the filled information, as a green popup | Hovering/focusing the form button opens a green popover listing Contact / Need / Next step from the saved form; empty form → "No details captured yet". Click still opens the form. |
| 3 | The clock (time) column should sit right before AI summary, and hovering it should show the time | Column order is now Phone · Lead type · Outcome · Notes · **Time** · AI summary · AI script/Form · Actions; the time cell is a focusable element with a popover showing the full date/time, duration and the viewer's timezone. |
| 4 | "How is the lead qualified? we barely even talked" | Two fail-closed gates before a call can flag a lead and raise the Event-Stream alert: the summary must support the label (`qualification_status` must not contradict `outcome`; at least one commitment/action/next step) **and** the conversation must have substance (≥ `LEAD_MIN_CALLER_TURNS`=3 caller turns of ≥2 words, duration ≥ `LEAD_MIN_DURATION_S`=45 s). Suppressions are logged with the reason. |
| 5 | Add Contact form: "make a section and add a drop down and add option which someone wants to select" | New **More details** section on Add/Edit contact: a dropdown of the optional fields the API already accepts (Company, Job title, Mobile, Business number, Best time to call, Timezone, Preferred contact method, Notes for the agent). Picking one reveals its input; Remove drops it; Edit pre-selects the fields the contact already has. Empty optional fields are never sent. |
| 6 | Salesforce Consumer Key/Secret provided | Written to prod `.env` (backup taken first, plaintext script shredded afterwards), `talky-api` restarted, `SalesforceConnector.is_configured() == True`. The card is live on /connectors; the OAuth click is the owner's. |

## Premortem — what could make this wrong, and what I did about it

- **Is it a patch or a fix?** #4 is a fix at the decision point (the flag and the alert share one code path); nothing hides the symptom. #1–#3, #5 are presentation changes at the single place each element renders.
- **Exporting helpers from a Next `page.tsx` breaks `next build`** (only page fields may be exported; `tsc` alone does not catch it). Caught in review: helpers live in `src/lib/contact-fields.ts`.
- **Lead gate false negatives.** A real 30-second "yes, send it" call is now not auto-flagged. Trade-off chosen deliberately (a wrong "qualified" alert costs trust; a missed flag is fixable from the Contacts page). Thresholds are env-tunable, every suppression logs `lead_not_marked call=… reason=…`, and the manual lead-type control is unaffected.
- **Retroactive effect.** The gate only prevents new flags. Already-flagged contacts (the Mohammad J Malik example) stay flagged until cleared by hand — not undone automatically because that is data the owner may have acted on.
- **Editing a contact and removing a detail does not clear it on the server.** `contactPayload` never sends empty strings (so an untouched field cannot wipe a stored value). Clearing requires an explicit null/"" contract on PATCH that this UI does not yet implement — documented, not guessed.
- **Form popover data is per-browser.** The post-call form lives in the existing client-side workflow store (localStorage); the popover shows exactly that, no more, no less. Pre-existing scope.
- **Credential hygiene.** The `.env` backup was taken before the secret was appended, the install script (which contained the secret) was shredded on the server and deleted locally, no secret appears in logs or this document.
- **Tests re-stated, not loosened.** The lead-flag tests now feed a substance row before the lead row; two new tests encode the complaint (thin conversation ⇒ never a lead; summary must support the label).

## Verification

```text
backend/.venv/Scripts/python -m pytest tests/unit tests/security -q
8884 passed, 8 skipped in 584.12s (0:09:44)
ruff check app/ --select F --extend-ignore F401,F841 → All checks passed!

cd Talk-Leee && npm run typecheck && npm run lint && npm test && npm run build
typecheck exit=0 · lint 0 errors 0 warnings · tests 389, pass 387, fail 0, skipped 2 · next build exit=0
```
