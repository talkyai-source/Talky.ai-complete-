# 2026-09-09 — An inbound campaign owns its own AI agent

Owner (verbatim intent): "it must be a separate entity, able to create separately and have
everything of its own — the same behaviour and mechanism as outbound; keep the knowledge of
each thing its own, don't mix."

## Before

An inbound campaign row (`inbound_campaign_configs`) held only number, trunk, hours and
routing. The agent — persona, names, voice, guidance, knowledge, tools — lived on a
`campaigns` row that the create form made the user **select**: an inbound campaign or an
unused outbound draft (the service converted the draft). A tenant whose campaigns had all
dialled out saw every option greyed out. Yesterday's "Create a new AI campaign" round trip
still made the inbound campaign a pointer at something created elsewhere.

## Design (approved 2026-09-09)

The runtime already needs a `campaigns` row per agent (script_config, voice, knowledge keyed
by campaign id, direction). So the change is ownership, not plumbing: **the inbound campaign
creates and owns that row**, born `direction='inbound'`, inside its own transaction. The user
defines the agent in the inbound form with the same fields and components the outbound wizard
uses, uploads the agent's knowledge there, and edits it later on the inbound page. Nothing is
selected from, converted from, or shared with outbound.

| Layer | Change | Where |
|---|---|---|
| Contract | `InboundCampaignCreateRequest.agent: InboundAgentDefinition` (company name, 1–3 agent names + genders, persona style, voice, TTS provider, guidance, slots, brief, knowledge_driven). `campaign_id` is now optional; **exactly one** of the two must be present. | `schemas/inbound_campaigns.py` |
| Endpoint | `prepare_inbound_agent_row`: same voice-per-provider check and the same production prompt validation `POST /campaigns` applies, same 400s. Produces the row the service inserts. | `endpoints/inbound_campaigns.py` |
| Service | `_create_owned_campaign` inserts `campaigns (…, status='draft', direction='inbound')` before the direction lock; the rest of `create_campaign` is unchanged and runs on the new id. Legacy `campaign_id` path kept for the existing configs. | `inbound_campaign_service.py` |
| Form (create) | Sections: Number & routing → **AI agent** (brand, names + gender hints, style cards, voice picker, guidance) → **Knowledge** (.md/.txt up to 10 MB, uploaded to the owned campaign after create) → opening → hours → safety. The base-campaign select and the override fields appear only in edit mode / legacy path. Save no longer waits for an "eligible campaign". | `inbound-campaign-form.tsx`, new `inbound-agent-section.tsx` |
| Payload | `agent` block is sent instead of `campaign_id` when the agent is owned | `inbound-api.ts::cleanInput` |
| Detail page | The knowledge panel is embedded on the inbound page (read-only without edit permission); the "configured on the base campaign" link is gone | `inbound-campaigns/[id]/page.tsx` |
| Removed | Yesterday's round trip (`/campaigns/new?for=inbound`, `campaign-create-return.ts`) — superseded | |

Existing inbound campaigns (AllState's two) keep their base campaign and behave exactly as
before. Outbound lists already filter `direction='inbound'` rows out, so an owned agent never
shows up as an outbound campaign.

## Premortem

- **Is it a patch or the fix?** The fix: the dependency on a pre-existing campaign is gone
  from the user's model; the row the runtime needs is created where it belongs.
- **Two creators drifting.** The inbound path calls the *same* prompt builder and voice check
  the outbound endpoint uses (imported, not copied), so validation cannot diverge silently.
- **Idempotency.** The agent row is part of the claimed request payload; a replayed create
  returns the original result and does not insert a second campaign.
- **Knowledge upload after create.** Create commits first, then the upload; an upload failure
  lands on the inbound page with `?knowledge_error=1` (same contract the outbound wizard uses)
  and the file can be re-added from the embedded panel.
- **Permissions.** Knowledge on an inbound campaign is already gated by `inbound:read` /
  `inbound:manage` (`campaign_knowledge_access.py`), so embedding the panel adds no new access.
- **Not done.** Editing the owned agent's persona/names/voice after creation still goes
  through the existing override fields on the edit page; a full agent editor on the inbound
  edit page is the natural next step if the owner wants it.

## Verification

```text
backend targeted: test_inbound_owned_agent (9) + test_inbound_campaign_service + test_inbound_conversion_boundaries → 79 passed
ruff check app/ --select F --extend-ignore F401,F841 → All checks passed!
Talk-Leee: typecheck exit=0 · lint exit=0 · tests 467, pass 465, fail 0, skipped 2 · next build exit=0
```

```text
backend/.venv/Scripts/python -m pytest tests/unit tests/security -q
8912 passed, 8 skipped in 678.48s (0:11:18)
```
