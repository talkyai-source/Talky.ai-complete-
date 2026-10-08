# Voice agent standards

The standards Talky's phone agent is held to, where each one is enforced, and the test that proves it. Researched 2026-10-08 from primary vendor documentation and regulator guidance (sources at the end). Line numbers are for `backend/` at the time of writing.

## How this is meant to work

- **The model owns the conversation.** Wording, order, rapport, objections, clarification and choosing what to look up are the model's job. No state machine and no scripted lines.
- **Behaviour guidance is Markdown, not code.** Generic standards live in `backend/app/services/scripts/prompts/policies/*.md`. Each file declares a purpose, a token budget and its placeholders, and tests hold it to them. Business specifics never go there; they come from the campaign (script, brief, knowledge). `PROMPT_POLICY_DIR` can point at an override folder; an override that changes placeholders is rejected and the packaged file is used.
- **Code enforces only what has a hard consequence:** opt-out persistence, recording disclosure, money figures, completed-action claims, tenant scoping and hang-up authorisation. Each guard is a cheap check on evidence (microseconds, no extra model call), never a second model judging the first.
- **Model judgement plus verified evidence.** When the model decides something with an external effect (a contact detail, an opt-out), it must quote the caller's words, and the server checks the quote is real. The model understands paraphrases and other languages; the quote check stops it inventing what nobody said.

Status key: **MEETS** · **PARTIAL** · **GAP** (open) · **DEVIATION** (deliberate, with reason).

## LAT — Latency

| ID | Standard | Target | Where | Status |
|---|---|---|---|---|
| LAT-1 | Voice-to-voice latency (caller end of speech to first agent audio) | p50 ≤ 800 ms, p95 ≤ 1,200 ms; tool turns p95 ≤ 2,000 ms | `latency_tracker.py:117-143` measures it per turn | PARTIAL: measured; the p95 alert fires at 1,500 ms (`latency_alerter.py:35`), above target |
| LAT-2 | Per-stage budget at p95 | Flux end-of-turn ≤ 300 ms · LLM first token ≤ 400 ms · TTS first byte ≤ 250 ms · gateway ≤ 100 ms | `voice_metrics.py:102` (TTFT histogram), `latency_tracker.py` | PARTIAL: measured, no per-stage alert |
| LAT-3 | Prompt size is the main lever on first-token time | Always-on guidance ≤ 1,100 tokens; whole turn prompt for a standard campaign ≤ 2,600 tokens before tool schemas | `test_prompt_policies.py`, `test_voice_standards_conformance.py` | MEETS: about 2,040 tokens measured for a 6-section campaign |
| LAT-4 | Bounded tool rounds; the last round must answer | ≤ 3 lookup rounds; the final call carries no tools | `streaming.py:177-245`, `turn_streamer.py` (`max_tool_rounds=3`) | MEETS |
| LAT-5 | Connections warm before the callee answers | STT, TTS and LLM pre-connected | `telephony/prewarm.py:40-65` (refuses to ring if warm-up fails) | MEETS |
| LAT-6 | Stream end to end; first sentence to TTS as soon as it completes | sentence or clause chunking | `turn_streamer.py` per-sentence flush | MEETS |
| LAT-7 | Knowledge reachable in one lookup | whole catalog outline in the prompt when ≤ 6,000 chars | `knowledge_tool.py` `section_outline` | MEETS for running campaigns; 8 stopped campaigns exceed it |

## TT — Turn-taking (Deepgram Flux)

| ID | Standard | Target | Where | Status |
|---|---|---|---|---|
| TT-1 | Thresholds inside Deepgram's ranges | eot 0.5-1.0 · eager 0.3-0.9 and ≤ eot · timeout 500-60,000 ms | `voice_tuning.py:78-80` (0.85 / 0.7 / 500), validated `deepgram_flux.py:248-268` | MEETS |
| TT-2 | Patience while the caller spells or reads digits | timeout ≥ 7,000 ms, eot ≥ 0.85 during capture, restored after | `deepgram_flux.py:86-87, 443-468` (8,000 ms / 0.9) | MEETS |
| TT-3 | One source of truth for turn timing, logged per call | the per-tenant resolver; the values in force logged once per call | `voice_tuning.py` (DB, env, defaults); `prewarm.py` `callee_first_eot_timeout` and the `voice_policy_wiring` log line | PARTIAL: callee-first calls now keep a tenant's longer timeout (they used to be forced to 1,000 ms). `providers.yaml` and `telephony_settings.py` still declare values the phone path never uses |
| TT-4 | Speculative replies never reach the caller before end of turn | draft cancelled on TurnResumed | `transcript_handler.py:360` | DEVIATION: eager drafts are disabled entirely (safety containment), so the 150-250 ms head start is not taken. Deepgram calls EndOfTurn-only "ideal for the majority" |
| TT-5 | Audio frame size | 20-80 ms (Deepgram recommends 80) | `deepgram_flux.py:74-75` (40 ms) | MEETS (measured choice; comment says 80 ms in one place) |
| TT-6 | Campaign keyterms | company, agent and product names, within Deepgram's 500-token cap | `telephony_session_config._build_call_keyterms` | MEETS |

Turn timing is aggressive by design: a 500 ms timeout ends a turn quickly but can split a hesitant caller's sentence. That trade is tuned per tenant (`voice_tuning`), not changed here.

## BI — Barge-in

| ID | Standard | Target | Where | Status |
|---|---|---|---|---|
| BI-1 | Agent stops when the caller starts speaking | TTS cancelled and gateway buffer cleared, target < 60 ms | `interrupt.py:15, 189, 368`; `voice_metrics.py:115-125` | MEETS |
| BI-2 | Backchannels ("uh-huh") and echo don't interrupt | text-level backchannel and disfluency filter, echo gate | `backchannel.py:25-60`, `deepgram_flux.py:835`, `audio_ingest.py:396` | PARTIAL: no minimum voice-duration guard; no acoustic echo cancellation |
| BI-3 | History holds only what was actually spoken | interrupted replies keep the submitted sentences plus a marker | `turn_streamer.py` (`_spoken_sentences`) | MEETS (sentence granularity) |
| BI-4 | Resume after a false interruption | resume the cut reply | `voice_pipeline_service.py` `_resume_after_false_barge_in` | MEETS |

## IC — Information collection

| ID | Standard | Target | Where | Status |
|---|---|---|---|---|
| IC-1 | Fields to collect come from the campaign | `required_lead_fields` per campaign | `schemas/campaigns.py:41` | PARTIAL: key and label only, no type or validator |
| IC-2 | The model extracts; the server validates | typed `record_contact` tool; email syntax check; E.164 through the one phone normaliser; the caller quote must be in the caller's turn | `contact_recording.py:39-51, 138-181` | MEETS |
| IC-3 | Caller-stated values stay pending until confirmed by read-back | `AWAITING_CONFIRMATION` until a later caller turn confirms | `contact_recording.py:207-233` | MEETS |
| IC-4 | Read-back style and a bounded number of attempts | numbers in small groups, unclear email parts spelled; stop after two failed tries | `policies/contact_and_privacy.md` | MEETS (guidance; no code loop by design) |
| IC-5 | Collect only what is needed; never take payment or secret data | data minimisation | `policies/contact_and_privacy.md` | MEETS |

## HAL — Hallucination and grounding

| ID | Standard | Target | Where | Status |
|---|---|---|---|---|
| HAL-1 | Facts come only from the knowledge or the prompt; otherwise say you can't confirm | grounding rule in every prompt | `policies/company_knowledge.md` | MEETS |
| HAL-2 | Money and percentages spoken must appear in what the agent was given or heard | per-sentence value check before TTS; derived figures withheld; bare numbers logged only | `figure_grounding.py`, wired in `turn_streamer.py` | MEETS |
| HAL-3 | No claim that an action happened without a result that allows it | sentence replaced unless `confirmation_allowed` | `speech_guard.py` | MEETS |
| HAL-4 | Internal identifiers are never spoken | section ids stripped | `speech_guard.py` `_SECTION_ID` | MEETS |
| HAL-5 | Every knowledge lookup is traceable | one log line: arguments, status, passage count, size (never source text) | `knowledge_tool.py` `run_knowledge_lookup` | MEETS |

## REL — Relevance

| ID | Standard | Target | Where | Status |
|---|---|---|---|---|
| REL-1 | Answer the caller's question first | usually a sentence or two, then move forward | `policies/how_to_speak.md` | MEETS |
| REL-2 | One question at a time | | `policies/how_to_speak.md` | MEETS |
| REL-3 | Off-topic: brief, kind, then back to how you can help | | `policies/how_to_speak.md` | MEETS |
| REL-4 | Knowledge reaches the model only for sections it chose | model-selected section reads | `knowledge_tool.py` | MEETS |

## CMP — Compliance (deterministic)

| ID | Standard | Target | Where | Status |
|---|---|---|---|---|
| CMP-1 | An opt-out in any wording is persisted in the same call, before the goodbye can claim it | phrase floor or model `do_not_call` with a verified quote of the caller; DNC written through the shared normaliser | `end_session_action.verified_opt_out`, `action_tools.run_voice_action` (end_call), `turn_runner.py`, `dialer/opt_out.py` | MEETS (since 2026-10-08; paraphrases were previously dropped) |
| CMP-2 | Recording disclosure before recording where policy requires it | one-party or two-party per tenant policy | `recording_policy_service.py`, `agent_first.py:181` | MEETS |
| CMP-3 | Honest AI disclosure | the agent never denies being an AI | `policies/conversation_guide.md`, `policies/non_negotiables.md` | PARTIAL: prompt-level only, no spoken notice at call start |
| CMP-4 | Calling hours in the callee's local time | | `call_guard.py:1053-1136` | MEETS |
| CMP-5 | One log line per call showing which policies applied | recording mode, disclosure, DNC check, timezone, turn timing, figure grounding | `voice_policy_wiring` (turn timing, figure grounding, policy overrides); `recording_disclosure_delivered`; the `call_guard_decisions` table (DNC, hours) | PARTIAL: the facts are logged, but in three places, not one line |

## ARC — Architecture

| ID | Standard | Target | Where | Status |
|---|---|---|---|---|
| ARC-1 | Generic behaviour in versioned Markdown policies | `policies/*.md` with purpose, budget and placeholders | `prompts/policies/__init__.py` | MEETS |
| ARC-2 | Layering: platform policy, then persona, then campaign; compliance floor last and not overridable | composer order; `non_negotiables` appended last | `composer.py`, `build.py:112` | MEETS |
| ARC-3 | Static prefix first, dynamic content last | cache-friendly order | `build.py:131-157` | MEETS (the current models report no cache hits, so the gain is latent) |
| ARC-4 | No business or market specifics in code, and no invented defaults | empty campaign slots stay empty | `composer.py` receptionist defaults (no more "24 hours" notice, "See website" or "patient") | PARTIAL: a Europe/London fallback timezone remains in `turn_streamer.py:261` |
| ARC-5 | Every standard has a test | | `tests/unit/test_voice_standards_conformance.py` and the tests named above | MEETS |

## Sources

- Deepgram Flux configuration, eager end of turn and the agent guide: developers.deepgram.com/docs/flux/configuration, /flux/voice-agent-eager-eot, /flux/agent, /flux/quickstart
- Human turn-taking timing: Stivers et al. 2009, PNAS (mean gap about 200 ms)
- Vapi speech configuration and prompting guide: docs.vapi.ai/customization/speech-configuration, docs.vapi.ai/prompting-guide
- LiveKit turn detection, workflows and testing: docs.livekit.io/agents/build/turns, /workflows, /testing
- OpenAI Realtime prompting guide and Agents SDK guardrails: developers.openai.com/api/docs/guides/realtime-models-prompting, openai.github.io/openai-agents-python/guardrails
- Anthropic tool definitions, hallucination reduction and Agent Skills: platform.claude.com/docs
- Google Dialogflow CX parameters (bounded reprompts, webhook validation): docs.cloud.google.com/dialogflow/cx/docs/concept/parameter
- FCC Declaratory Ruling FCC 24-17 (AI voices are "artificial" under the TCPA) and the April 2025 consent-revocation rules (law-firm summaries; confirm against the FCC text)
- ICO guidance on live and automated marketing calls (PECR, TPS)
- EU AI Act Article 50 transparency obligations (applicable 2 August 2026)

Items in these sources that were not confirmed against a primary page (several state recording laws, Ofcom CLI rules, the ICO position on conversational AI calls) need legal review before anyone claims compliance with them.
