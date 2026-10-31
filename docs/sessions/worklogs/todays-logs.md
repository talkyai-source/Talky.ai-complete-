# Today's Call Logs — 2026-08-23

**Source:** `journalctl -u talky-api --since 'today'` on `144.76.17.150` (Blaze-VoIP-API)
**Window:** 2026-08-23 00:00 → 12:35 UTC
**Calls placed:** 26 — **all** on campaign `09b7ee9c` (company **Dojo**, agent Sarah)
**Turns measured:** 51
**Turns flagged `[SLOW]` by the system itself:** **49 of 51 — 96%**

---

## 1. Why the agent is lagging — the answer

**The lag is almost entirely LLM first-token time, and the cause is the model, not the prompt.**

Every turn today ran on:

```
llm_resilient_wrapper_active primary=groq/qwen/qwen3.6-27b secondary=gemini deadline_ms=2500
```

`qwen3.6-27b` on Groq takes **~1.1 seconds to produce its first token** against this
prompt. That single number is 85–90% of the total time before the caller hears anything.

### The controlled comparison

This is the strongest evidence, because both measurements come from the *same
system, same prompt template, same TTS provider*, twelve hours apart — with only
the LLM differing:

| | Last night (browser test) | Today (Dojo calls) |
|---|---|---|
| LLM | **cerebras / gpt-oss-120b** | **groq / qwen3.6-27b** |
| Prompt size | 37,028 chars | 38,070 chars (**+2.8%**) |
| **LLM first token** | **270 / 304 / 315 / 530 ms** | **865 – 1522 ms (mean 1108)** |
| TTS first chunk | 106–130 ms | 125.7 ms mean |
| **Total speech→audio** | **405 / 421 / 437 / 663 ms** | **1000 – 1624 ms (mean 1255)** |
| System verdict | `[OK]` | `[SLOW]` on 49/51 turns |

The prompt is essentially the same size in both cases (a 2.8% difference).
**The first-token time roughly tripled.** The variable that changed is the model.

### Why qwen is slow here specifically

From the measurements already recorded in this project:

- Groq caches **GPT-OSS only**. Measured warm TTFT: **102 ms for GPT-OSS vs 672 ms for qwen**.
- `qwen3.6-27b` returns **no `cached_tokens` field at all** — proven by direct API
  test in report 6. There is no prompt caching available on this model.

So every one of these turns re-processes **38,070 characters** of prompt from
cold, on a model that cannot cache it. The prompt size and the model choice
multiply together; on Cerebras the same prompt costs a third as much.

### The lever

Two options, in order of expected impact per unit of effort:

1. **Switch the Dojo campaign's primary LLM off `qwen3.6-27b`.** Cerebras
   `gpt-oss-120b` measured 270–530 ms first-token on a near-identical prompt on
   this same system last night. This is a configuration change in AI Options,
   not a code change.
2. **Reduce the 38,070-character prompt** (task #43). Helps on any model, but it
   is the slower path and today's data shows the model is the larger term.

---

## 2. Latency statistics — all 51 turns

### Speech-to-audio (what the caller actually waits)

| Metric | Value |
|---|---|
| Mean | **1255 ms** |
| Minimum | 1000 ms |
| Maximum | 1624 ms |
| Turns under 1000 ms | **0** |
| Turns flagged `[SLOW]` | 49 / 51 (96%) |

*(Two turns are excluded from these figures — see §5, negative latency bug.)*

### LLM first token — the dominant term

| Metric | Value |
|---|---|
| Mean | **1108 ms** |
| Median | 1080 ms |
| Minimum | 865 ms |
| Maximum | 1522 ms |
| p95 | ~1336 ms |
| **Share of total latency** | **~88%** |

### TTS first chunk — not the problem

| Metric | Value |
|---|---|
| Mean | **126 ms** |
| Typical range | 92–147 ms |
| Outliers | 216, 258, 321, 333 ms (4 of 51) |

ElevenLabs `eleven_flash_v2_5` is performing well. TTS is roughly 10% of the
budget and needs no attention.

### STT first — not the problem

Mostly 0–350 ms, frequently 0 ms (the caller's speech was already transcribed
when the turn began). Two outliers of 1970 ms and 4260 ms coincide with the STT
failures in §4.

---

## 3. A second, separate problem: the agent talks for too long

Distinct from first-word lag. `LLM-total` is the time to finish generating the
whole reply, and it grew dramatically as the day went on:

| Time | LLM-total range | Interpretation |
|---|---|---|
| 11:05 (call `f94d4d05`) | 1191 – 1401 ms | short, crisp replies |
| 12:02 onward | **3695 – 13883 ms** | long monologues |

`TTS-total` follows it, reaching **10,371 ms** on one turn — over ten seconds of
continuous agent speech in a single turn.

The caller still hears the first word at ~1.2 s because TTS streams
sentence-by-sentence, so this does not show up in the headline latency number.
But a ten-second uninterrupted agent monologue on a cold outbound call is its
own problem, and it is invisible to the `[SLOW]` flag.

Worth noting the Gemini fallback is configured `max_tokens=350`, but the primary
(qwen) is the one producing these lengths.

---

## 4. STT failures — 3 failovers in 26 calls (~11.5%)

```
11:06:37 ERROR resilient_stt_secondary_also_silent provider=deepgram-nova:nova-3
         voiced_s=6.0 — both STT engines accepted caller speech without
         answering; no further failover exists
```

This one is the serious variant. On call `27446141`, Deepgram Flux went silent,
the system failed over to Nova, and **Nova went silent too**. There is no third
engine. That call had no working speech recognition at all.

Note the corresponding latency line for that call:

```
11:06:27 [OK] Turn 1 latency: -5626ms (STT-first: 4260ms, ...)
```

STT-first of 4260 ms — the transcript arrived over four seconds late, right
before both engines stopped answering.

This is task **#63** ("STT failover is still ~21% of calls"). Today's rate is
11.5% (3 of 26), and it now includes a double-failure case that the ~21% figure
did not capture.

---

## 5. A measurement bug: negative latencies

Two turns reported impossible values:

```
11:06:27 [OK] Turn 1 latency: -5626ms (STT-first: 4260ms, LLM-first-token: 962ms,
              TTS-first-chunk: 101ms, LLM-total: 1273ms, TTS-total: -6586ms)
11:59:04 [OK] Turn 0 latency: -1290ms (STT-first: 2218ms, LLM-first-token: 1030ms,
              TTS-first-chunk: 107ms, LLM-total: 2187ms, TTS-total: -1942ms)
```

A negative duration is impossible. `TTS-total` went negative in both cases,
which dragged the computed total negative. Both occurred on turns where STT was
badly delayed (4260 ms and 2218 ms), so the likely cause is a start timestamp
being recorded after an end timestamp when the turn is reconstructed out of order.

**Consequence:** these two turns were tagged `[OK]` instead of `[SLOW]`. So the
"49 of 51 slow" figure is if anything *under*-counting — both `[OK]` turns in
today's data are artefacts, not successes. **In truth, 51 of 51 turns were slow.**

---

## 6. Other observations

**A warning that fires on every single call:**

```
WARNING prompt_identity_persist_matched_no_row talklee=tlk_0f73fe46
        — the calls row was not found by talklee_call_id
```

Appears once per call, 26 times today. Harmless — the prompt identity is written
by other means — but it is noise that makes real problems harder to spot.

**Prompt identity is consistent and correct:**

```
telephony_prompt_identity campaign=09b7ee9c persona=lead_gen
    template=generic_lead_generation version=lead_gen@3
    hash=77aa8a7a5678ee5f kd=True prompt_chars=38070
```

Every call today ran `lead_gen@3` with hash `77aa8a7a5678ee5f`. Configuration is
reaching the agent correctly — that is not a source of the lag.

**No LLM failovers.** The resilient wrapper has `deadline_ms=2500`, and qwen's
first token at ~1100 ms sits comfortably under it. So the system never escalates
to Gemini. **The slowness is entirely within budget, which is exactly why nothing
alarms.** The `[SLOW]` tag is the only signal, and it is only in the log.

---

## 7. Recommended order of action

1. **Move campaign `09b7ee9c` (Dojo) off `qwen3.6-27b`.** Expected: ~1255 ms →
   ~450 ms first-word, based on last night's measurement of the same prompt on
   Cerebras. Configuration change, no deploy.
2. **Constrain reply length.** `LLM-total` of 8–14 s means the agent is
   monologuing. Cap output tokens on the primary, as the Gemini fallback already is.
3. **Fix the negative-latency computation** so `[SLOW]` counts are trustworthy.
4. **Task #63 — STT.** Now includes a both-engines-silent case with no fallback.
5. **Silence the per-call `prompt_identity_persist_matched_no_row` warning.**

---

## Appendix A — All 51 turn-latency lines, verbatim

```
11:05:03 [call=f94d4d05] [SLOW] Turn 0  latency: 1103ms (STT-first: 153ms, LLM-first-token: 956ms,  TTS-first-chunk: 128ms, LLM-total: 1191ms,  TTS-total: 215ms)
11:05:14 [call=f94d4d05] [SLOW] Turn 1  latency: 1318ms (STT-first: 0ms,   LLM-first-token: 1151ms, TTS-first-chunk: 135ms, LLM-total: 1401ms,  TTS-total: 218ms)
11:05:32 [call=f94d4d05] [SLOW] Turn 2  latency: 1175ms (STT-first: 249ms, LLM-first-token: 989ms,  TTS-first-chunk: 137ms, LLM-total: 1283ms,  TTS-total: 243ms)
11:05:53 [call=f94d4d05] [SLOW] Turn 3  latency: 1111ms (STT-first: 1970ms,LLM-first-token: 953ms,  TTS-first-chunk: 126ms, LLM-total: 1238ms,  TTS-total: 251ms)
11:06:05 [call=27446141] [SLOW] Turn 0  latency: 1000ms (STT-first: 0ms,   LLM-first-token: 865ms,  TTS-first-chunk: 123ms, LLM-total: 1125ms,  TTS-total: 133ms)
11:06:27 [call=27446141] [OK]   Turn 1  latency: -5626ms (STT-first: 4260ms,LLM-first-token: 962ms, TTS-first-chunk: 101ms, LLM-total: 1273ms,  TTS-total: -6586ms)   ← measurement bug
11:59:04 [call=d2af5b98] [OK]   Turn 0  latency: -1290ms (STT-first: 2218ms,LLM-first-token: 1030ms,TTS-first-chunk: 107ms, LLM-total: 2187ms,  TTS-total: -1942ms)   ← measurement bug
12:02:19 [call=1ef4804f] [SLOW] Turn 0  latency: 1257ms (STT-first: 266ms, LLM-first-token: 1153ms, TTS-first-chunk: 98ms,  LLM-total: 2201ms,  TTS-total: 1040ms)
12:02:28 [call=1ef4804f] [SLOW] Turn 1  latency: 1200ms (STT-first: 238ms, LLM-first-token: 1080ms, TTS-first-chunk: 103ms, LLM-total: 4463ms,  TTS-total: 2026ms)
12:02:44 [call=1ef4804f] [SLOW] Turn 3  latency: 1624ms (STT-first: 276ms, LLM-first-token: 1522ms, TTS-first-chunk: 97ms,  LLM-total: 3809ms,  TTS-total: 1039ms)
12:02:58 [call=1ef4804f] [SLOW] Turn 4  latency: 1189ms (STT-first: 230ms, LLM-first-token: 1074ms, TTS-first-chunk: 108ms, LLM-total: 8294ms,  TTS-total: 1430ms)
12:03:23 [call=1ef4804f] [SLOW] Turn 6  latency: 1176ms (STT-first: 98ms,  LLM-first-token: 1068ms, TTS-first-chunk: 100ms, LLM-total: 8179ms,  TTS-total: 903ms)
12:03:40 [call=1ef4804f] [SLOW] Turn 7  latency: 1163ms (STT-first: 0ms,   LLM-first-token: 1055ms, TTS-first-chunk: 102ms, LLM-total: 5265ms,  TTS-total: 1143ms)
12:03:50 [call=1ef4804f] [SLOW] Turn 8  latency: 1511ms (STT-first: 0ms,   LLM-first-token: 1188ms, TTS-first-chunk: 321ms, LLM-total: 4074ms,  TTS-total: 804ms)
12:04:07 [call=6486dd8e] [SLOW] Turn 0  latency: 1262ms (STT-first: 257ms, LLM-first-token: 1145ms, TTS-first-chunk: 110ms, LLM-total: 5607ms,  TTS-total: 733ms)
12:04:20 [call=6486dd8e] [SLOW] Turn 1  latency: 1120ms (STT-first: 0ms,   LLM-first-token: 1008ms, TTS-first-chunk: 104ms, LLM-total: 9822ms,  TTS-total: 1145ms)
12:04:33 [call=6486dd8e] [SLOW] Turn 2  latency: 1164ms (STT-first: 238ms, LLM-first-token: 1023ms, TTS-first-chunk: 118ms, LLM-total: 8626ms,  TTS-total: 2919ms)
12:05:04 [call=6486dd8e] [SLOW] Turn 4  latency: 1298ms (STT-first: 265ms, LLM-first-token: 1071ms, TTS-first-chunk: 216ms, LLM-total: 3860ms,  TTS-total: 1479ms)
12:05:15 [call=6486dd8e] [SLOW] Turn 5  latency: 1158ms (STT-first: 248ms, LLM-first-token: 1018ms, TTS-first-chunk: 131ms, LLM-total: 6042ms,  TTS-total: 1813ms)
12:05:20 [call=6486dd8e] [SLOW] Turn 6  latency: 1385ms (STT-first: 0ms,   LLM-first-token: 1031ms, TTS-first-chunk: 333ms, LLM-total: 3768ms,  TTS-total: 2715ms)
12:05:35 [call=6486dd8e] [SLOW] Turn 7  latency: 1359ms (STT-first: 92ms,  LLM-first-token: 1214ms, TTS-first-chunk: 127ms, LLM-total: 6662ms,  TTS-total: 2892ms)
12:05:52 [call=eaf6b018] [SLOW] Turn 0  latency: 1256ms (STT-first: 248ms, LLM-first-token: 1117ms, TTS-first-chunk: 114ms, LLM-total: 4579ms,  TTS-total: 3437ms)
12:06:06 [call=eaf6b018] [SLOW] Turn 1  latency: 1128ms (STT-first: 251ms, LLM-first-token: 1031ms, TTS-first-chunk: 96ms,  LLM-total: 9672ms,  TTS-total: 619ms)
12:06:14 [call=eaf6b018] [SLOW] Turn 2  latency: 1063ms (STT-first: 260ms, LLM-first-token: 962ms,  TTS-first-chunk: 95ms,  LLM-total: 5525ms,  TTS-total: 576ms)
12:06:25 [call=eaf6b018] [SLOW] Turn 3  latency: 1284ms (STT-first: 267ms, LLM-first-token: 1172ms, TTS-first-chunk: 102ms, LLM-total: 8097ms,  TTS-total: 1424ms)
12:06:37 [call=eaf6b018] [SLOW] Turn 4  latency: 1256ms (STT-first: 187ms, LLM-first-token: 1135ms, TTS-first-chunk: 108ms, LLM-total: 4659ms,  TTS-total: 1110ms)
12:06:57 [call=eaf6b018] [SLOW] Turn 5  latency: 1179ms (STT-first: 268ms, LLM-first-token: 1069ms, TTS-first-chunk: 103ms, LLM-total: 9285ms,  TTS-total: 906ms)
12:07:12 [call=eaf6b018] [SLOW] Turn 6  latency: 1170ms (STT-first: 0ms,   LLM-first-token: 1065ms, TTS-first-chunk: 99ms,  LLM-total: 5133ms,  TTS-total: 581ms)
12:07:20 [call=eaf6b018] [SLOW] Turn 7  latency: 1202ms (STT-first: 231ms, LLM-first-token: 1092ms, TTS-first-chunk: 101ms, LLM-total: 4508ms,  TTS-total: 1643ms)
12:07:27 [call=eaf6b018] [SLOW] Turn 8  latency: 1352ms (STT-first: 0ms,   LLM-first-token: 1260ms, TTS-first-chunk: 92ms,  LLM-total: 4115ms,  TTS-total: 534ms)
12:07:39 [call=eaf6b018] [SLOW] Turn 9  latency: 1623ms (STT-first: 0ms,   LLM-first-token: 1336ms, TTS-first-chunk: 258ms, LLM-total: 9805ms,  TTS-total: 2560ms)
12:07:49 [call=eaf6b018] [SLOW] Turn 10 latency: 1418ms (STT-first: 0ms,   LLM-first-token: 1310ms, TTS-first-chunk: 105ms, LLM-total: 5560ms,  TTS-total: 1508ms)
12:08:00 [call=eaf6b018] [SLOW] Turn 11 latency: 1299ms (STT-first: 0ms,   LLM-first-token: 1159ms, TTS-first-chunk: 109ms, LLM-total: 5122ms,  TTS-total: 3931ms)
12:08:10 [call=eaf6b018] [SLOW] Turn 12 latency: 1362ms (STT-first: 0ms,   LLM-first-token: 1211ms, TTS-first-chunk: 111ms, LLM-total: 6625ms,  TTS-total: 5373ms)
12:08:55 [call=eaf6b018] [SLOW] Turn 15 latency: 1194ms (STT-first: 0ms,   LLM-first-token: 1063ms, TTS-first-chunk: 107ms, LLM-total: 9919ms,  TTS-total: 3790ms)
12:09:08 [call=eaf6b018] [SLOW] Turn 16 latency: 1263ms (STT-first: 0ms,   LLM-first-token: 1090ms, TTS-first-chunk: 127ms, LLM-total: 8185ms,  TTS-total: 5488ms)
12:09:25 [call=1d2f28ff] [SLOW] Turn 0  latency: 1253ms (STT-first: 0ms,   LLM-first-token: 1115ms, TTS-first-chunk: 119ms, LLM-total: 3695ms,  TTS-total: 2560ms)
12:09:41 [call=1d2f28ff] [SLOW] Turn 1  latency: 1160ms (STT-first: 349ms, LLM-first-token: 1041ms, TTS-first-chunk: 113ms, LLM-total: 10604ms, TTS-total: 836ms)
12:09:53 [call=1d2f28ff] [SLOW] Turn 2  latency: 1173ms (STT-first: 263ms, LLM-first-token: 1048ms, TTS-first-chunk: 119ms, LLM-total: 5655ms,  TTS-total: 1123ms)
12:10:19 [call=1d2f28ff] [SLOW] Turn 3  latency: 1156ms (STT-first: 0ms,   LLM-first-token: 1051ms, TTS-first-chunk: 105ms, LLM-total: 4519ms,  TTS-total: 408ms)
12:10:31 [call=1d2f28ff] [SLOW] Turn 4  latency: 1169ms (STT-first: 0ms,   LLM-first-token: 1045ms, TTS-first-chunk: 111ms, LLM-total: 7214ms,  TTS-total: 1575ms)
12:10:52 [call=1d2f28ff] [SLOW] Turn 5  latency: 1420ms (STT-first: 295ms, LLM-first-token: 1234ms, TTS-first-chunk: 128ms, LLM-total: 13883ms, TTS-total: 10371ms)  ← longest
12:11:04 [call=1d2f28ff] [SLOW] Turn 6  latency: 1272ms (STT-first: 0ms,   LLM-first-token: 1106ms, TTS-first-chunk: 125ms, LLM-total: 8935ms,  TTS-total: 5708ms)
12:11:18 [call=1d2f28ff] [SLOW] Turn 7  latency: 1224ms (STT-first: 237ms, LLM-first-token: 1067ms, TTS-first-chunk: 124ms, LLM-total: 9866ms,  TTS-total: 4827ms)
12:12:02 [call=1d2f28ff] [SLOW] Turn 9  latency: 1227ms (STT-first: 240ms, LLM-first-token: 1072ms, TTS-first-chunk: 147ms, LLM-total: 7070ms,  TTS-total: 1410ms)
12:12:19 [call=1d2f28ff] [SLOW] Turn 10 latency: 1225ms (STT-first: 0ms,   LLM-first-token: 1104ms, TTS-first-chunk: 112ms, LLM-total: 9469ms,  TTS-total: 1114ms)
12:12:33 [call=1d2f28ff] [SLOW] Turn 11 latency: 1373ms (STT-first: 0ms,   LLM-first-token: 1249ms, TTS-first-chunk: 111ms, LLM-total: 10216ms, TTS-total: 1654ms)
12:12:48 [call=1d2f28ff] [SLOW] Turn 12 latency: 1233ms (STT-first: 0ms,   LLM-first-token: 1124ms, TTS-first-chunk: 108ms, LLM-total: 7838ms,  TTS-total: 550ms)
12:13:07 [call=1d2f28ff] [SLOW] Turn 13 latency: 1479ms (STT-first: 239ms, LLM-first-token: 1357ms, TTS-first-chunk: 116ms, LLM-total: 6447ms,  TTS-total: 918ms)
12:13:21 [call=1d2f28ff] [SLOW] Turn 14 latency: 1237ms (STT-first: 0ms,   LLM-first-token: 1110ms, TTS-first-chunk: 114ms, LLM-total: 7860ms,  TTS-total: 1057ms)
12:13:38 [call=1d2f28ff] [SLOW] Turn 15 latency: 1281ms (STT-first: 286ms, LLM-first-token: 1166ms, TTS-first-chunk: 107ms, LLM-total: 9362ms,  TTS-total: 634ms)
```

---

## Appendix B — Session configuration, verbatim

Identical on all 26 calls:

```
telephony_prompt_composed persona=lead_gen agent=Sarah company=Dojo
    campaign=09b7ee9c-bd76-4b83-a04f-311aff4b4871 kd=True prompt_chars=38070

telephony_prompt_identity campaign=09b7ee9c-bd76-4b83-a04f-311aff4b4871
    persona=lead_gen template=generic_lead_generation
    version=lead_gen@3 hash=77aa8a7a5678ee5f kd=True prompt_chars=38070

Creating voice session call_id=eaef9901 talklee=tlk_62714b73e233 type=telephony

GeminiLLMProvider initialized: model=gemini-2.5-flash, temperature=0.5,
    max_tokens=350, thinking_budget=0

llm_resilient_wrapper_active primary=groq/qwen/qwen3.6-27b
    secondary=gemini deadline_ms=2500

WARNING prompt_identity_persist_matched_no_row talklee=tlk_62714b73
    — the calls row was not found by talklee_call_id
```

---

## Appendix C — Last night's browser-test calls, for comparison

Same system, twelve hours earlier, **Cerebras** instead of Groq/qwen:

```
23:06:51 CerebrasLLMProvider initialized: model=gpt-oss-120b temperature=0.6
         max_tokens=1050 reasoning_effort=low
23:06:51 llm_resilient_wrapper_active primary=cerebras/gpt-oss-120b
         secondary=gemini deadline_ms=2500
23:06:51 telephony_prompt_composed persona=lead_gen agent=Michael
         company=All-state-estimation kd=True prompt_chars=37028

23:06:59 [OK] Turn 0 latency: 663ms (STT-first: 1ms,   LLM-first-token: 530ms, TTS-first-chunk: 129ms, LLM-total: 802ms, TTS-total: 268ms)
23:07:08 [OK] Turn 1 latency: 405ms (STT-first: 77ms,  LLM-first-token: 270ms, TTS-first-chunk: 125ms, LLM-total: 699ms, TTS-total: 223ms)
23:07:56 [OK] Turn 0 latency: 437ms (STT-first: 0ms,   LLM-first-token: 304ms, TTS-first-chunk: 130ms, LLM-total: 591ms, TTS-total: 284ms)
23:08:06 [OK] Turn 1 latency: 421ms (STT-first: 226ms, LLM-first-token: 315ms, TTS-first-chunk: 106ms, LLM-total: 870ms, TTS-total: 116ms)
```

Note the `[OK]` tags — the same threshold that flags today's turns as `[SLOW]`.
Note also `LLM-total` of 591–870 ms versus today's 3,695–13,883 ms: the replies
were short as well as fast.

---

## Appendix D — STT failure detail

```
11:06:37 ERROR [resilient_stt] [call=27446141-3aef-4a63-856c-561c81bc029c]
         resilient_stt_secondary_also_silent provider=deepgram-nova:nova-3
         voiced_s=6.0 — both STT engines accepted caller speech without
         answering; no further failover exists
```

Related, from last night's browser test (same failure class, single engine):

```
23:07:25 ERROR resilient_stt_stream_silent provider=deepgram-flux voiced_s=6.0
         — 6.0s of caller speech went in and no transcript event came back;
         treating the stream as dead and failing over
         (suppressed_ms=456 of agent audio was correctly not counted)
23:07:25 resilient_stt_audit provider=deepgram-flux outcome=failover
         counted_voiced_ms=6000 suppressed_ms=456 probe=installed probe_errors=0
23:07:25 resilient_stt_failed_over_to=deepgram-nova:nova-3 buffered_chunks=20
```

Today's rate: **3 failovers in 26 calls (11.5%)**, one of which exhausted both
engines.

---

## Appendix E — Method

Commands used to produce this file:

```bash
journalctl -u talky-api --since 'today' --no-pager | grep -E 'Turn [0-9]+ latency'
journalctl -u talky-api --since 'today' --no-pager | grep -E 'llm_resilient_wrapper_active|LLMProvider initialized|telephony_prompt_composed|prompt_identity'
journalctl -u talky-api --since 'today' --no-pager | grep -c 'Creating voice session'
journalctl -u talky-api --since 'today' --no-pager | grep -c 'resilient_stt_failed_over_to'
journalctl -u talky-api --since 'today' --no-pager | grep -oE 'company=[A-Za-z-]+ campaign=[0-9a-f]{8}' | sort | uniq -c
```

Statistics in §2 were computed from the 51 lines in Appendix A. The two
negative-latency turns are excluded from the total-latency figures and noted
separately in §5.

**Note on the Dojo campaign:** these numbers dial the team's own numbers for
testing purposes. `one_party` recording is correct for this campaign and is not
a consent issue.
