# 2026-09-09 — Inbound campaigns are created and edited with the outbound campaign options, identically

Owner, three messages in a row: "it must be a separate entity … everything of its own" →
"the same behaviour and mechanism as outbound; keep the knowledge of each its own, don't mix"
→ "its campaign options must mimic the outbound type of options, no differ".

## What was wrong

An inbound campaign row (`inbound_campaign_configs`) held only number, trunk, hours and
routing; the agent lived on a `campaigns` row the create form made you **pick** (an inbound
campaign or an unused outbound draft, converted on save). A tenant whose campaigns had all
run saw every option greyed out. A first attempt (same day, commit `b2a84a69`) gave the inbound
form its own reduced "AI agent" section — rejected: it *differed* from the outbound options.

## Final design (built)

**An inbound campaign is created with the very same creator as an outbound one and edited with
the very same editor.** The campaign is born `direction='inbound'`; the inbound-only facts (number,
trunk, opening, hours, safety routing) are a second step. Nothing is picked from, converted
from, or shared with outbound campaigns; outbound lists already hide inbound rows.

| Layer | Change | Where |
|---|---|---|
| Backend | `CampaignCreateRequest.direction` (`outbound` default, `inbound` allowed); the insert writes it. The existing inbound create path then binds this already-inbound campaign — no conversion branch runs. | `schemas/campaigns.py`, `endpoints/campaigns.py` |
| Create, step 1 | `/inbound-campaigns/new` hosts the **same** `CampaignWizard` (and the "Prefer the detailed form?" classic `CampaignForm`) with `direction="inbound"`: persona, agent names + gender hints, brief fields, guidance budget, voice/provider picker, knowledge upload, contact fields, review with prompt-layer preview. Only the outbound dialling schedule is omitted (inbound hours are step 2). On create it continues to step 2 with the new campaign. | `campaign-wizard.tsx`, `campaign-form.tsx`, `inbound-campaigns/new/page.tsx`, `campaign-create-return.ts` |
| Create, step 2 | The inbound form with the step-1 campaign shown as a **locked** field (no picker); number, trunk, opening, hours, safety as before. The old override block is retitled "Inbound-only tweaks (optional)". | `inbound-campaign-form.tsx` (`lockCampaign`) |
| Edit | The campaign editor (`/campaigns/{id}/edit`) now accepts inbound campaigns instead of redirecting away — identical options — and its back link returns to the inbound page. | `campaigns/[id]/edit/page.tsx` |
| Inbound page | Header gains **Edit campaign & agent** (the shared editor) and **Test agent** (same button as outbound); **Knowledge** panel is embedded on the page; the number/routing editor is labelled as such. | `inbound-campaigns/[id]/page.tsx` |
| Legacy | Existing inbound configs (AllState's two) and the picker path keep working. | |

The `b2a84a69` "agent block" (schema/service/`inbound-agent-section.tsx`) is removed: one
creator, not two.

## Premortem

- **Patch or fix?** Fix at the model level: the only difference between an inbound and an
  outbound campaign is now the `direction` value and the routing step that follows.
- **Two creators drifting** cannot happen — there is one.
- **Detail page** for campaigns (`/campaigns/{id}`) still redirects inbound rows to the inbound
  page on purpose: it carries outbound-only controls (start, contacts, dialler). The editor,
  test agent and knowledge are reachable from the inbound page.
- **Knowledge upload in step 1** goes to the inbound-born campaign; a failure lands on the
  routing step's page via the wizard's existing `?knowledge_error=1` contract → then on the
  inbound page's embedded panel.
- **Not done:** the outbound wizard's "calling schedule" is intentionally absent for inbound;
  inbound business hours live in step 2 as before.

## Verification

```text
backend targeted: test_campaign_create_direction (4) + assistant/test_campaign_create → 19 passed
ruff check app/ --select F --extend-ignore F401,F841 → All checks passed!
Talk-Leee: typecheck exit=0 · lint exit=0 · tests 466, pass 464, fail 0, skipped 2 · next build exit=0
```

```text
backend/.venv/Scripts/python -m pytest tests/unit tests/security -q
8907 passed, 8 skipped in 394.75s (0:06:34)
```
