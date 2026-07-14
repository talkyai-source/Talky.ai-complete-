---
description: STAY CURRENT — re-check 2026 provider capabilities (OpenAI Realtime VAD, Deepgram Flux endpointing, TTS, model IDs) and reconcile Talky's detection thresholds with what the platforms now support.
argument-hint: "[provider/topic] e.g. 'openai realtime turn detection', 'deepgram flux endpointing', 'latest model IDs', 'all'"
allowed-tools: Read, Grep, Glob, WebSearch, WebFetch, Edit, Write
---
Detection thresholds decay: providers ship new VAD modes, endpointing knobs,
models, and voices, and our hard-coded defaults drift from best practice. This
skill checks what's true *now* and reconciles our code with it. Scope via
$ARGUMENTS (default = all).

## What to check, and against which code
| Provider / topic | Look up (current) | Reconcile against |
|---|---|---|
| **OpenAI Realtime turn detection** | semantic_vad eagerness levels, server_vad params (threshold/prefix_padding_ms/silence_duration_ms), manual commit/interrupt levers, barge-in | `backend/app/infrastructure/realtime/openai_realtime.py` `_DEFAULT_TURN_DETECTION` + the td mapping; `/detect-turn-taking` |
| **Realtime models / voices** | current gpt-realtime model IDs, available voices, expressiveness controls | `openai_realtime.py` session config; `backend/app/services/scripts/realtime_instructions.py` |
| **Deepgram Flux (STT) endpointing** | eager/speculative endpointing, EndOfTurn/StartOfTurn semantics, known hallucination bugs | `is_repetitive_transcript` + turn-0 floor in `turn_ender.py`; the silence monitor |
| **TTS (ElevenLabs / Cartesia)** | latency, streaming, voice options | `voice_pipeline/tts_playback.py` |
| **LLM menu** | current best models + IDs, deprecations | `tenant_ai_configs`, the curated menu (memory `llm-menu-and-compliance-floor`) |
| **Model IDs generally** | latest Claude/OpenAI/Gemini/Groq IDs | any place a model ID is hard-coded |

## Recipe
1. **Search for the current spec** — `WebSearch` + `WebFetch` the official docs for
   the topic in $ARGUMENTS (prefer the provider's own docs/changelog; note the
   date). For a deep, multi-source, fact-checked pass use the `deep-research`
   workflow instead.
2. **Diff against our code** — grep the reconcile target above; list where our
   defaults/thresholds differ from the current recommendation and whether the
   provider added a knob we don't yet expose.
3. **Propose, don't auto-apply.** For each drift: is it a *bug* (deprecated ID,
   removed param), a *missed capability* (new VAD knob worth exposing), or
   *fine as-is*? Recommend the smallest change; route tuning changes through
   `/detect-turn-taking` / `/detect-quality` with their tests.
4. **Verify version claims before acting** — a doc can be stale; confirm the model
   ID/param actually exists (the app's provider list, a probe on the overlay).
   Per the memory rules, if a recalled memory names a model/flag, re-verify it
   still exists before recommending it.
5. **Report** a dated summary: what changed upstream, what we should update, what's
   already current. Persist durable findings to the relevant memory with the
   absolute date. Deploy code changes **only when asked**.

Ground truth wins over any single article — cross-check ≥2 sources for a
capability claim before recommending a change to a live detector.
