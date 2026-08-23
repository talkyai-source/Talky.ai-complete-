# MVP Model Selection — measured, not read off a datasheet

**Decided:** 2026-08-24
**Method:** every number below was measured on **our own accounts**, against a
**37–38k character prompt** (the size production actually composes), with the
**same `reasoning_effort` settings production sends**.
**Scripts:** `backend/scripts/bench_llm_candidates.py` (correctness + TTFT),
`backend/scripts/bench_llm_stability.py` (latency spread)

---

## The decision

| Role | Provider | Model | p50 TTFT | p95 | max | Checks |
|---|---|---|---|---|---|---|
| **Primary** | Cerebras | `gpt-oss-120b` | **269 ms** | 566 ms | 701 ms | 10/10 |
| **Fallback** | Groq | `openai/gpt-oss-120b` | 360 ms | **462 ms** | **464 ms** | 10/10 |

Everything else is removed from the MVP menu.

**These are the same weights on two independent providers, and that is
deliberate.** See §5 — it is the one place this recommendation argues against
the brief, and the reasoning is laid out so it can be overruled.

---

## 1. Why measure at all

This project has been wrong twice by trusting published numbers:

- Gemini's documented latency ordering **inverted** under measurement —
  `2.5-flash` came out fastest at 186 ms, `3.6-flash` slowest at 980 ms.
- Two Llama ids sat on the menu after the account had **lost access to them**.
  Selecting one produced a 404 on the first turn, and with failover enabled the
  call silently ran a model the tenant had not chosen.

So the first step was a live `/v1/models` call on both accounts.

**Groq — 13 models, 4 conversational:**
`openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `openai/gpt-oss-safeguard-20b`,
`qwen/qwen3.6-27b`. (The rest are Whisper, Orpheus TTS, prompt-guard classifiers
and the `groq/compound` agentic systems.)

**Cerebras — 2 models:** `gemma-4-31b`, `gpt-oss-120b`.

### Finding: a third dead id was already on our menu

`CerebrasModel.ZAI_GLM_4_7 = "zai-glm-4.7"` is offered in AI Options and **the
account does not serve it.** Same failure class as the dead Llama entries, and
it had gone unnoticed. Removed as part of this change.

---

## 2. What was tested, and why each check exists

Speed is easy to measure and the least decisive thing. A voice agent fails in
specific, boring ways — every check below is a failure **this project has
already seen**:

| Check | Why it is here |
|---|---|
| `brevity` | Prod calls on 2026-08-23 hit `LLM-total` of 13.9 s — the agent monologuing at a caller |
| `one_question` | Two questions in one breath: one gets answered, the other is lost. The June 2026 audit removed models for this |
| `disclosure` | "Are you a robot?" must get an honest yes. `qwen3-32b` was removed for dodging exactly this |
| `no_invention` | `qwen3-32b` hallucinated prices and leaked a card number in the weakness audit |
| `spelling` | NATO-spelling an email ("S for Sierra") is voice-unsafe. `gemini-3.5-flash` did it 3/3 **even after a guardrail fix** — a model-level quirk prompt rules do not beat |
| `digits_email` | A *mangled* read-back is the failure — that is what puts a wrong address in the CRM |
| `pivot` | Wrong person → stop selling. Not "sorry", then keep pitching |

---

## 3. Round 1 — correctness and first-token latency

Prompt 38,128 chars, 3 runs per scenario, production `reasoning_effort`.

| Model | p50 TTFT | Checks |
|---|---|---|
| `groq/openai/gpt-oss-120b` | 172 ms | 10/10 |
| `cerebras/gemma-4-31b` | 274 ms | 10/10 |
| `cerebras/gpt-oss-120b` | 292 ms | 10/10 |
| `groq/openai/gpt-oss-20b` | 412 ms | 10/10 |
| `groq/qwen/qwen3.6-27b` | 617 ms | 10/10 |

**All five passed every correctness check.** Correctness does not separate them,
so latency and stability decide.

### The June 2026 finding did not reproduce

GPT-OSS models were removed from the menu in June as *"agentic task-completion
reasoners that misbehave on conversational voice — stack questions,
NATO-spell"*. Against the current prompt, **both GPT-OSS entries scored 10/10,
including the NATO-spelling and question-stacking checks.** The prompt has been
substantially reworked since that judgement; on this evidence it no longer
holds.

### A correction I had to make mid-test

My first run scored `qwen3.6-27b` at **5/10** with every reply beginning
`<think>\nHere's a thinking process:`. That was **my test's fault, not the
model's** — I passed `reasoning_effort` only for Cerebras, while `groq.py`
forces `reasoning_effort="none"` for the whole qwen3 family. Corrected, qwen
scores 10/10. Left in this document because a benchmark that quietly
misconfigures one candidate produces a confident, wrong recommendation.

---

## 4. Round 2 — stability, which changed the answer

p50 hides the turn that hurts. A caller does not experience the median; they
experience the 1.4-second reply, and they experience it as the agent being
broken. 20 sequential turns per model, 5 varied prompts × 4 rounds.

| Model | p50 | p90 | **p95** | **max** | **stdev** |
|---|---|---|---|---|---|
| `cerebras/gpt-oss-120b` | **269** | 427 | 566 | 701 | 118 |
| `groq/openai/gpt-oss-120b` | 360 | 441 | **462** | **464** | 137 |
| `cerebras/gemma-4-31b` | 317 | 1068 | **1133** | **1671** | **368** |
| `groq/openai/gpt-oss-20b` | 416 | 444 | 449 | 465 | 142 |
| `groq/qwen/qwen3.6-27b` | 640 | 671 | 717 | 792 | 43 |

**`gemma-4-31b` is disqualified here, and only here.** On p50 alone (317 ms) it
looked like the obvious Cerebras choice — it beat `gpt-oss-120b`'s 292 ms in
round 1. But its **p95 is 1133 ms and its worst turn was 1671 ms**, a 5×
spread, stdev 368. Roughly one turn in ten would feel broken. Judged on p50
alone we would have shipped it.

**`groq/openai/gpt-oss-120b` has the tightest tail of any candidate** — max
464 ms, only 104 ms above its own p50. Under load that predictability is worth
more than a lower median.

**`qwen3.6-27b` is the most *consistent* model tested** (stdev 43) — and
consistently the slowest, at 640 ms p50. It is predictably bad rather than
unpredictably good. It is also what all 26 production calls ran on 2026-08-23,
which is the measured cause of that day's `[SLOW]` tag on 49 of 51 turns.

---

## 5. Why both picks are GPT-OSS-120B — and the argument against

The brief asked for two *opposite* models. This recommends the same weights on
two independent providers instead. The reasoning:

**What failover is actually defending against.** The realistic failure is a
**provider** incident — an outage, a rate limit, a regional slowdown. Cerebras
and Groq are separate companies on separate infrastructure, so that risk is
already fully diversified by the provider split.

**What model diversity would cost.** Failover happens **mid-call**, on a
2500 ms deadline. With different models the caller hears the agent's phrasing,
length and manner change halfway through a sentence. Worse, the prompt is tuned
against one model's quirks; the fallback runs the same prompt with different
quirks, unmeasured, at the exact moment something is already going wrong.

**What model diversity would buy.** Cover against a flaw in GPT-OSS-120B
itself. Real, but this is the risk the correctness battery in §2 exists to
measure — and it scored 10/10 on both, including the two checks that previously
disqualified this family.

**If you want genuine model diversity anyway**, the honest second choice is
`groq/qwen/qwen3.6-27b`: a different family, the steadiest latency of anything
tested (stdev 43) — at 640 ms p50, roughly **2.4× the primary's first-word
latency**. That is the trade, stated plainly. `gemma-4-31b` is not a candidate
for this role at any priority, because of §4.

---

## 6. Configuration

| Setting | Value | Why |
|---|---|---|
| Primary | `cerebras/gpt-oss-120b` | best p50 (269 ms), tight tail, 10/10 |
| Fallback | `groq/openai/gpt-oss-120b` | tightest tail measured, 10/10, independent provider |
| `reasoning_effort` | `"low"` | GPT-OSS does **not** accept `"none"` on either provider; `"low"` is the floor. Sending `"none"` is rejected |
| Deadline to fallback | 2500 ms | unchanged; both models' p95 sits far inside it |

**On prompt caching:** Groq caches the GPT-OSS family and **not** qwen — qwen
returns no `cached_tokens` field at all. Our prompt already puts static content
first, as Groq's caching docs require, so selecting GPT-OSS activates a saving
that was previously unreachable. This is a second, independent reason the qwen
default was costing latency.

---

## 7. What this does NOT establish

Stated so nobody over-reads the table:

- **Single-session measurement.** One machine, one time of day. Provider
  latency varies with regional load; these numbers are a snapshot, not an SLA.
- **20 samples per model** in the stability run. Enough to expose gemma's
  spread, not enough for a trustworthy p99.
- **Correctness was scored on one reply per scenario**, by automated string
  checks. They catch the specific failures listed in §2; they do not measure
  conversational quality in general.
- **No live phone call was made on either finalist.** Everything here is API
  measurement. The agent's behaviour over a real 40-turn call with barge-in,
  STT errors and a real caller is not covered.
- **The June GPT-OSS finding is contradicted, not disproven.** It was a
  behavioural judgement made against a different prompt. If question-stacking
  reappears in production, this decision should be revisited.

---

## 8. Reproducing

```bash
cd /opt/talky/backend
venv/bin/python scripts/bench_llm_candidates.py --list      # live model lists
venv/bin/python scripts/bench_llm_candidates.py --runs 3    # correctness + TTFT
venv/bin/python scripts/bench_llm_stability.py 4            # latency spread
```

Both scripts read credentials from the environment and never print them.
