# Lead-Gen Voice Agent — Research + Gap Audit (2026-06-09)

Scope: the outbound **lead generation** persona only (`app/services/scripts/prompts/personas/lead_gen.py`), the composer (`composer.py`), and per-turn knowledge injection (`voice_pipeline/turn_streamer.py`). Goal: identify why the agent feels static/unprofessional and what professional voice-agent design actually looks like.

## Part 1 — How professional voice agents are built (researched)

### A. Prompt STRUCTURE (six-part) — Vapi
Distinct labelled sections, not one prose blob:
1. **Identity & Personality** (+ identity lock)
2. **Response Guidelines** (brevity, pacing, formatting)
3. **Guardrails** (hard constraints that override everything)
4. **Context** (runtime: caller data, time, company)
5. **Workflow / Use-Cases** — *step-by-step playbooks with branching*
6. **Examples** (few-shot transcripts)

### B. DYNAMIC flow = a state machine, not a script
Workflow written as numbered steps with explicit branches:
```
## 1. Greeting & intent
## 2. Discovery  → branch on answer
## 3. Qualify    → if disqualified → polite close
## 4. Offer/booking → tool call; on 0/1/many results, do X
## 5. Close
```
Plus: intent routing at the top, error-recovery paths (tool fail → retry → escalate), a turn budget (~7–9 turns), and "end each turn with one question."

### C. Sales methodology
- **Josh Braun** 4-part cold-call: **Permission → Problem → Poke-the-bear → Promise**; *prevent* objections by opening on the prospect's problem, not your pitch. Objection move: "is it that you're happy with what you have, you've got a lot on your plate, or you just hate cold calls as much as I hate making them?"
- **Chris Voss tactical empathy**: **labeling** ("it sounds like…"), **mirroring** (repeat last 2-3 words), **calibrated questions** (open "how/what" — give control, can't be deflected).
- **Frameworks for WHAT to ask** (BANT / MEDDIC / SPIN) + tactical empathy for HOW to ask, so discovery never feels like an interrogation.

### D. Knowledge / RAG grounding
- Ground every answer in retrieved KB; positive constraint "information must be grounded in trusted knowledge" beats "don't make things up."
- Explicit **"I don't know / I'll follow up"** path; detect **low-evidence retrieval** and refuse rather than fill gaps from training data.

### E. Realism + edge cases
- **Barge-in** happens ~1 in 5 calls and is the single biggest human-vs-robot factor — must be acknowledged in the prompt (stop, listen, respond).
- **Silence**: wait ~10-15s, check in once, then end.
- **ASR errors / mishearing**: clarify gracefully.
- **Voicemail / AMD** + **IVR**: detect + branch.
- **"Are you a robot?"**, hostile/skeptical, emotional, off-topic, repeated/looping.
- **Escalation triggers**: explicit "I want a person" (immediate), out-of-scope, sentiment/emotional spike, repeated failures → **warm transfer with full context**.
- **Number/date/email/phone**: spell phonetically for TTS.

### F. Guardrails
- Prefer short positive principles over long "never say X/Y/Z" banlists.
- Pre-response silent safety check; prompt-extraction protection; abuse warn-then-end.
- Outbound compliance: identify, honor opt-out ("take me off your list"), recording disclosure where required, no deception.

## Part 2 — Current `lead_gen` persona: the gaps

| Area | Current state | Gap vs best practice |
|---|---|---|
| **Structure** | ~200-line PROSE body; everything mixed together | No labelled sections, **no state machine / numbered stages with branch points**, no turn budget, no pre-response check. This is the core "static" feeling. |
| **Opener** | Permission-based ("reason I'm calling is {call_reason}, is now a decent time?") | Decent but leads with US, not the prospect's *problem*; no Braun "poke the bear"; opener is a fixed line. |
| **Discovery** | "discovery before pitch" + a qualification map | Good intent, but no **labeling/mirroring/calibrated-question** technique — questions are listed, not taught as tactical empathy. |
| **Objection handling** | A few "hesitation patterns" (busy/price/think) + "two declines" | **Thin + reactive.** No framework (Braun prevention, Voss labeling). Missing: "send me an email", "we already have someone", "how did you get my number", "is this a sales call", "not interested". |
| **Knowledge use** | Slot block "WHAT YOU KNOW" + per-turn RAG inject ("authoritative"); knowledge-driven suffix grounds well | Two sources can conflict; full template's grounding is softer; **no low-evidence handling** ("if retrieval is weak, say I don't know"); no explicit "never fill from training." |
| **Barge-in** | Handled in the *pipeline*, but **not in the prompt** | The #1 human-feel factor is absent from the model's instructions. |
| **Silence / no-input** | None | No "if quiet, check in once then end." |
| **Mishearing / ASR** | None | No graceful clarification. |
| **Voicemail / AMD** | None | No branch for "this is a voicemail." |
| **"Are you AI?"** | Inbound prompt *denies* being AI; lead_gen unspecified | Identity-deny is brittle + compliance-risky; needs a deliberate, honest, on-brand line. |
| **Escalation / transfer** | None | No "I want a person" → transfer; no tool. |
| **Tools / booking** | Booking described as prose | No function-calling (calendar check, book, transfer) — booking is "say someone will confirm." |
| **Compliance (mid-call)** | call_guard does pre-call DNC | No mid-call opt-out ("stop calling me"), no recording disclosure in the flow. |
| **Number/email TTS** | Email read-back rule only | No general phonetic spelling for numbers/dates/phones. |

## Part 3 — Recommendations (prioritized)

**P0 — restructure to a state machine** (kills the "static" feel): rewrite lead_gen as labelled sections + a numbered **STAGES** playbook (Open → Discover → Qualify → Offer → Objections → Close) with explicit branch points and a turn budget.

**P0 — objection + discovery as frameworks**: bake in Braun prevention + Voss label/mirror/calibrated questions + a real objection table (email/competitor/number/sales-call/not-interested).

**P1 — knowledge grounding**: unify slot vs RAG, strengthen "ground strictly; if weak/missing → 'I'll follow up,' never invent."

**P1 — realism/edge-cases block**: barge-in, silence, mishearing, voicemail, "are you AI", hostile, escalation/transfer.

**P1 — number/email/phone phonetic spelling** rule.

**P2 — tools**: calendar check / book / warm-transfer function-calling.

**P2 — compliance**: mid-call opt-out + (where required) recording disclosure.

## Sources
- Vapi — Voice AI Prompting Guide: https://docs.vapi.ai/prompting-guide
- Retell — hardest parts of a real call (IVR/voicemail/barge-in): https://www.retellai.com/blog/how-voice-ai-handles-hardest-parts-real-call
- Poly.ai — barge-in handling: https://poly.ai/blog/barge-in-voice-ai-interruption-handling
- JustCall — AI voice agent escalation frameworks: https://justcall.io/blog/ai-voice-agent-escalation.html
- Josh Braun — cold call framework / opening lines / objections: https://joshbraun.com/coldcall/ , https://joshbraun.com/learn/objections/
- Chris Voss tactical empathy in sales: https://justcall.io/blog/best-sales-negotiation-chris-voss.html
- Gladia — voice AI safety/hallucinations/guardrails: https://www.gladia.io/blog/safety-voice-ai-hallucinations
- Vonage — Knowledge AI to avoid hallucinations: https://developer.vonage.com/en/blog/smarter-voice-agents-use-knowledge-ai-to-avoid-ai-hallucinations
