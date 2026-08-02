# Voice quality + hardening — 2026-07-29 → 2026-08-03

**Production HEAD `1d279c4d`** · gate **4,702 passed / 0 failed** · 5 services active,
`/healthz/deep` `{"ready":true,"db":"ok","redis":"ok"}`, workers 200.

Rollback target: `ba98c505`.

---

## 1. The spoken opener never said why we were calling

**Symptom.** Every outbound call opened with
`"Hey, this is Sarah from All-state. Got a quick second?"` — no reason, and a
permission-to-proceed ask.

**Cause.** The `lead_gen` prompt has always instructed the model to lead with the
reason. It never got the chance: on an agent-first call the *pre-synthesised*
greeting speaks first and flips `_has_introduced`, so the model's reason-first
opener never ran. `call_reason` is a **required campaign slot** — the data was
always there, it simply never reached the spoken template.

**Fix.** `AgentConfig` carries `call_reason`; `build_persona_greeting` uses it.

### Structure came from measured data, not taste

| opener | success |
|---|---|
| "Did I catch you at a bad time?" | **2.15%** (worst) |
| "How's your day going?" | 7.6% |
| context → own the cold call → permission | **11.18%** |
| context-first | **11.24%** (best) |
| *stating the reason for calling* | **2.1× lift** |

Prospects decide in **8–12 seconds**; the opener's job is to earn the next
thirty, not to pitch.

**My first draft was rejected and deserved to be.** It ended *"tell me to get
lost if it's a bad moment"* — the 2.15% bad-time out in friendlier words — ran
~25 words (~9s, straight through the decision window), and its "reason" was a
value proposition rather than a reason for ringing.

> ⚠️ **The persona prompt is half-wrong here.** It says prefer
> "permission-to-decline", but the 11.18% winner is *not* a bad-time question —
> it is context **first**, then owning the cold call, then asking.

**Now:**
```
lead_gen          Hi, it's Sarah at Allstate. Straight up — cold call, about
                  {reason}. Thirty seconds?                        [18w ~6.4s]
customer_support  Hi, it's Sarah from Allstate support — following up on your
                  enquiry. Have you got a minute?                  [17w ~6.1s]
```

Support and receptionist do **not** claim "this is a cold call" — those follow an
existing enquiry, so it would be false.

Reason cap is **60 chars** (not 120): ~16 template words at ~2.8 words/sec means
anything longer pushes past the window. Beyond it, fall back to the generic
opener.

**Activation is by data, never a flag.** A campaign with no reason gets the
reworked generic opener.

> 🔴 **Action required:** your test campaign `50847cc9` has empty
> `campaign_slots`, so it keeps the generic opener until a `call_reason` is set.
> That sentence is your pitch to write.

---

## 2. An internal action envelope was read aloud to a caller

**Symptom (production, 2026-07-08).** The agent spoke this verbatim:

```json
{"action":"endsession","reason":"conversationcomplete",
 "farewell:"Message left, I'll try again another time. Cheers."}
```

**Two defects had to line up.**

**(a) Malformed JSON.** `farewell:"` is missing the key's closing quote, and
`endsession`/`conversationcomplete` dropped their underscores. `json.loads`
raised → parser returned `None` → treated as speech → TTS. One dropped
character turned a clean hangup into machine noise.

**(b) The streaming guard only checked whether the buffer *started* with `{`.**
The contract says "no spoken text outside JSON", but small models routinely emit
a sentence and *then* the envelope, which sailed past.

**Fix.** Action names normalise on letters only. A narrow repair runs **only
after** strict parsing fails (missing key quote, single-quoted key, trailing
comma). The guard now finds an envelope anywhere and splits — prose spoken,
envelope swallowed.

Deliberately **not** a general JSON fixer: a false positive swallows real speech
and the agent goes silent mid-call. Fully single-quoted JSON is **not** repaired
(there is a test saying so) — converting quoted *values* would rewrite
apostrophes, and the real leak contained *"I'll try again another time"*.

---

## 3. Strict structured outputs — where they belong

Groq's `strict: true` json_schema constrains the model at the **token level**:
the reply cannot miss a key, use a wrong type, or be invalid JSON. Far stronger
than `{"type":"json_object"}`, which only guarantees valid *syntax*.

### The decision that shaped everything

Strict mode forces the **entire response** to match the schema.

- ✅ **Discrete extraction** (summarise this transcript) — one object in, one out.
- ❌ **A conversational turn** — the model must be free to speak. Forcing a schema
  makes the agent emit JSON at the caller.

So the obvious move — "use structured outputs to fix the envelope leak" — is
**wrong**, and was not done. A test fails if anyone imports the helper into
`turn_streamer` or `turn_runner`.

### Capability-gated, because the failure mode is hard

A strict request to an unsupported model is an **API error**, not a soft
degradation. Groq supports it on `gpt-oss-20b`/`120b` only.

```
llama-3.3-70b-versatile  ->  json_object
openai/gpt-oss-120b      ->  json_schema  strict=True
```

The call site **never branches** — adding a model later is one line in a
frozenset.

### Why the summariser first

gpt-oss is poor at *conversation* (documented in `groq.py`; excluded from the KB
tool path). The summariser is **offline extraction** — precisely where that
weakness doesn't apply and strict-mode support does. It also already had a retry
costing a *second full LLM round trip* on parse failure, then silently degraded
to "summary unavailable". Under strict mode that retry is unreachable; if it
ever fires we log `ERROR` and take the fallback rather than hiding it.

Schema is **derived from `EMPTY_SUMMARY`**, so the two cannot drift. Default
model **unchanged** — `CALL_SUMMARY_MODEL` is overridable, because switching the
model that writes customer-visible summaries is an operator decision.

---

## 4. Two bugs found while chasing the above

**Block reason mixed two clocks.** `next_eligible_at` came from the injected
`now`; `retry_after_seconds` from `datetime.now()`. A card could read *"Next
window: Friday 14:00"* while reporting a delay measured from another moment. In
production `now` is normally `None`, so they agreed **by luck**.

**The gate was red every weekend.** Six `TestSchedulingRules` tests opened the
calling *hours* but inherited the Mon–Fri default for *days*, so `can_make_call`
short-circuited on `calling_not_allowed_on_Sat/Sun` before reaching the
assertion. Six phantom failures two days in seven is exactly the noise a real
regression hides in.

---

## 5. Tenant that could not dial — resolved

```
tenant 5e666d8a   verified caller ID: +17789249977   route: refused=False
                  leads: +16478471491  failed ×3     dialable: 0
```

The caller-ID registration worked. The leads had been burned to `failed` by the
earlier `caller_id_not_verified` rejections, and only `pending`/`calling` get
enqueued.

**It self-heals:** `_reset_leads_for_restart` revives `failed` → `pending` when a
campaign starts with nothing pending. **Press Start.** Q3 Dojo needs nothing —
7,236 dialable leads, verified ID, just stopped.

---

## Still open

| item | why deferred |
|---|---|
| **Prompt prefix caching** | `build.py:77` prepends a per-turn block at position 0, so byte 0 changes every turn and prefix caching can never hit — your Groq client already reads `cached_tokens` and it is structurally 0. Biggest latency lever (turns run 946–2708ms against a ~600ms benchmark) but it changes attention ordering on every call; wants the A/B you already have instrumentation for. |
| **LLM-authored opener** | The right end state — the ring window gives 5–8s of free budget. Not done unattended after getting the wording wrong twice. |
| **C++ gateway lifetime counters** | `/stats` sums only live sessions, so a finished call's packet-loss figures vanish. Needs a `g++` rebuild + gateway restart. |
| **Reapers bypass the outcome pipeline** | Can re-dial someone who just had a full conversation. |
| **Per-lead timezone** | `resolve_lead_timezone` has zero callers; calls are windowed against *your* timezone, not the callee's. A TCPA judgement that is yours. |
| **Auto-DNC never reaches `dnc_entries`** | A suppressed number is still dialable from another campaign. |
| **`CLEANUP_DRY_RUN=true`** | 90-day retention promised, never enforced; 985MB and growing. |

---

## Verification checklist for your next call

1. `This call may be recorded` must **not** appear as a `User:` line.
2. `agent_name_voice_gender_mismatch` must be **absent** from the logs.
3. The opener should state the reason — **only** once `call_reason` is set.
