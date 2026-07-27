# STT Provider Parity — Deepgram Flux vs nova-3

**Ticket:** TKT-008 (second half) · **Date:** 2026-07-24

Two STT providers must be interchangeable here, because two things depend on it:

1. **Failover.** `ResilientSTTProvider` promotes the secondary **mid-call** when the primary fails —
   which is not hypothetical: on 2026-06-29 Deepgram's Flux beta began rejecting `numerals=true` with
   HTTP 400 and every live call went silent.
2. **Operator choice.** Tenants select their engine in AI Options.

The requirement is therefore precise: **downstream turn logic must not need to know which provider
produced a chunk.** This document records where that held and where it did not.

---

## Verdict

| Area | Result |
|---|---|
| Audio contract (sample rate, encoding) | ✅ **Parity.** Both driven from the same `VoiceSessionConfig` field |
| Turn-end signalling | ✅ Equivalent shape (text chunk + empty marker) via different mechanisms |
| **Barge-in** | 🔴 **Was broken — fixed in this ticket.** See divergence 8 |
| Text formatting | ⚠️ Diverges by default (`numerals`) — deliberate, documented |
| Vocabulary biasing / observability tags | ⚠️ Flux-only |
| Cross-provider equivalence tests | ⚠️ **None existed.** Three added for barge-in |

**The headline:** the ticket asked whether the *audio contract* diverges on failover. It does not —
that worry is resolved. What it found instead was a **behavioural** divergence in barge-in that was
silently degrading every call on the Nova path.

---

## 1 · The defect: barge-in was half-implemented on Nova

Barge-in requires two separate things to happen:

| # | Mechanism | Effect |
|---|---|---|
| 1 | the direct `on_barge_in` callback → `_on_barge_in_direct` | **stops TTS playback**, opener-echo suppression, latency bookkeeping |
| 2 | a `BargeInSignal` on the transcript stream → `TranscriptHandler` → `handle_barge_in()` | **cancels the in-flight LLM task**, rolls back speculative conversation history, annotates the last assistant turn `"[interrupted by caller]"` |

**Flux emitted both. Nova emitted only (1).**

So whenever Nova was the active engine — selected in AI Options, *or promoted mid-call by failover* —
a caller interrupting the agent produced:

- ✅ the agent going quiet (TTS stopped), but
- ❌ **the LLM continuing to generate a response nobody was waiting for**, and
- ❌ **conversation history retaining text the caller never heard**, unannotated.

That second point is the corrosive one. The stored transcript diverges from what actually happened on
the call, so summaries, dispositions and any later analysis are drawn from a record of a conversation
that did not occur. It logs nothing and raises nothing — the same invisible-failure shape as F-20.

### Why it was missed

The `BargeInSignal` model's own docstring already contemplates this case:

> *"`text` optionally carries the recognized transcript for the StartOfTurn that triggered this signal
> (Flux only — **Nova's SpeechStarted has no recognized text yet**)."*

The contract was designed for Nova to emit it with empty text, and `TranscriptHandler` already reads
it defensively (`getattr(transcript, "text", "") or ""`). Nova simply never emitted it. The design
anticipated the case; the implementation stopped at the callback.

### The fix

`deepgram_nova.py` now yields `BargeInSignal(text="")` on `SpeechStarted`, alongside the existing
callback. Empty text is correct rather than a placeholder — Nova's `SpeechStarted` is a pure VAD event
that fires *before* any transcript exists.

Covered by `tests/unit/test_nova_barge_in_parity.py`: the signal is emitted, its `text` is an empty
string rather than `None`, and the direct callback still fires alongside it — because the signal must
not have *replaced* the callback. Non-vacuous: before the fix, Nova yielded nothing and the first
assertion fails.

---

## 2 · Connection parameters

Both are built in `voice_orchestrator` (`_build_flux` / `_build_nova`) from the **same**
`VoiceSessionConfig` fields.

| Parameter | Flux | nova-3 | Match |
|---|---|---|---|
| **sample_rate** | 16000 | 16000 | ✅ same default, same config field |
| **encoding** | `linear16` | `linear16` | ✅ same default, same config field |
| model | `flux-general-en` | `nova-3` | expected — different products |
| language | **accepted then never used** — absent from the connection params | sent explicitly | ⚠️ diverges |
| `numerals` | **`false`** by default | **`true`** by default | ⚠️ diverges, deliberately |
| `smart_format` | not available | `true` always | Nova-only |
| `keyterm` biasing | one param per term, env or config | **not sent** | ⚠️ Flux-only |
| session `tag` (tenant/campaign/call) | sent | **not sent** | ⚠️ Flux-only |
| `mip_opt_out` | configurable, defaults on | hardcoded on | same effect today |
| endpointing / utterance_end_ms / vad_events | n/a | acoustic turn detection | protocol difference |
| eot_threshold / eot_timeout_ms / eager_eot | semantic turn detection | n/a | protocol difference |
| wire framing | rebatched to fixed 1280-byte / 40 ms frames | forwarded as received | Flux-specific optimisation |
| endpoint | `wss://…/v2/listen` | SDK `/v1/listen` | nova-3 is v1-only; v2 rejects it |

**The ticket's stated worry is resolved:** sample rate and encoding match and come from one shared
field, so a mid-call failover cannot change the audio contract underneath the pipeline.

---

## 3 · Output contract

Both emit `TranscriptChunk(text, is_final, confidence, metadata)`, and both end a turn with a text
chunk followed by an **empty-text `is_final=True` marker** — the signal that fires the LLM. That core
shape matches.

Differences worth knowing:

- **Nova emits a chunk type Flux does not** — "segment finalized, turn continues" (`is_final=True`,
  `speech_final=False`), surfaced as a non-final running update. nova-3 finalizes per segment; Flux
  does not have the concept.
- **Confidence semantics differ.** Flux sets `confidence=None` on *every* chunk, deliberately and with
  an inline comment: its `end_of_turn_confidence` is a *"speaker finished"* probability, **not** a word
  confidence. Nova reports a real recognition confidence.
- **Flux's eager path has no Nova equivalent** — `on_eager_end_of_turn` is never called by Nova. This
  is inherent: acoustic VAD has no speculative-turn concept.

### A prior finding, now confirmed fixed

An earlier audit flagged that Flux was stuffing `end_of_turn_confidence` into
`TranscriptChunk.confidence`, where the turn-0 rejection gate then misread it as a word confidence.
**That is fixed.** Every Flux emit site sets `confidence=None`, and
`tests/unit/test_flux_confidence_not_turn_boundary.py` plants a fake `end_of_turn_confidence=0.87` on
every event type and asserts it never surfaces. Recorded here because the concern appears in older
planning notes and should not be re-investigated.

---

## 4 · Divergences, classified

| # | Divergence | Verdict |
|---|---|---|
| 1 | Flux silently drops `language` | **UNDETERMINED**, leans deliberate — the Flux model id encodes locale (`flux-general-en`), but the parameter is accepted in the signature, implying it should do something. Settled by checking whether `/v2/listen` accepts `language` at all |
| 2 | `numerals` defaults are opposite | **DELIBERATE** — Flux's default is `false` because Deepgram's v2 beta began 400-ing it on 2026-06-29. A forced workaround. Consequence is real: number/email formatting changes on failover |
| 3 | `keyterms` Flux-only | **DELIBERATE** — biasing investment went to the primary |
| 4 | `tags` Flux-only | **LIKELY A BUG** — no protocol reason nova-3 sessions can't carry tenant/campaign tags. Deepgram-side attribution is lost whenever Nova is active |
| 5 | Nova's empty end-marker uses `confidence=1.0`, Flux uses `None` | **LIKELY A BUG**, cosmetic — an unexplained literal on a semantically identical control marker. Harmless today only because the turn-0 gate treats `1.0` as never-below-floor |
| 6 | Nova never calls `on_eager_end_of_turn` | **DELIBERATE** — acoustic VAD has no eager concept |
| 7 | Nova's barge-in is ungated (no backchannel/min-words filter) | **DELIBERATE**, protocol-forced — `SpeechStarted` fires before any transcript exists, so content-based gating is structurally impossible. Nova will interrupt on a cough where Flux would not. An inherent trade-off of the acoustic model |
| 8 | **Nova never emitted `BargeInSignal`** | 🔴 **BUG — FIXED in this ticket** |
| 9 | No capture-mode relaxation on Nova (spelled emails) | **DELIBERATE**, but *not equivalent*. Flux relaxes endpointing mid-stream; Nova relies on `compose_turn_text`'s overlap stitching instead. A slow speller can still produce several turns on Nova where Flux holds one |
| 10 | Wire framing differs | **DELIBERATE** — a documented Flux-specific optimisation |
| 11 | `mip_opt_out` configurable on Flux, hardcoded on Nova | **UNDETERMINED**, low impact — identical effect today |

---

## 5 · Test coverage

**Before this ticket, no test asserted output-shape equivalence between the two providers.** The one
file referencing both replaces each with a bare `MagicMock`, never calls `stream_transcribe`, and
asserts only wiring.

Added: `tests/unit/test_nova_barge_in_parity.py` — three tests covering divergence 8.

Still uncovered, and worth a follow-up ticket: a test that drives **both** providers over the same
scripted event sequence and asserts the emitted `TranscriptChunk` sequences are equivalent in shape.
That is the test that would have caught divergence 8 on the day it was introduced, and it would guard
the remaining divergences above.

---

## Findings raised

| ID | Sev | Finding |
|---|---|---|
| **F-31** | 🟠 High — **FIXED** | Nova never emitted `BargeInSignal`, so under Nova a caller interrupt stopped TTS but **left the LLM generating and the history un-rolled-back and unannotated**. The stored transcript diverged from the actual call. |
| **F-32** | 🟡 Med | No test asserts cross-provider output equivalence. Divergence 8 lived undetected because nothing compared the two. |
| **F-33** | 🟡 Med | `numerals` defaults are opposite, so number and email formatting changes if a call fails over mid-stream — on precisely the CORE fields that must be exact. |
| **F-34** | ⚪ Low | Deepgram session `tags` (tenant/campaign/call) are Flux-only; attribution is lost whenever Nova is active. |
| **F-35** | ⚪ Low | Nova's empty end-of-turn marker sets `confidence=1.0` where Flux sets `None`. Unexplained asymmetry on an identical control marker. |
| **F-36** | ⚪ Low | Flux accepts a `language` argument and never uses it. Either wire it or drop it from the signature. |
