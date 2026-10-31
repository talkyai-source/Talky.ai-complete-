#!/usr/bin/env python3
"""Benchmark the Groq and Cerebras candidates on what this product needs.

    python scripts/bench_llm_candidates.py            # full run
    python scripts/bench_llm_candidates.py --list     # live model lists only

WHY NOT JUST READ THE DOCS
--------------------------
This project has been burned twice by trusting published numbers: Gemini's
documented latency ordering INVERTED under measurement (2.5-flash 186ms fastest,
3.6-flash 980ms slowest), and two Llama ids stayed on the menu after the account
had lost access to them. Every number below is measured on OUR account, with a
prompt the size production actually sends.

WHAT IS MEASURED, AND WHY EACH ONE
-----------------------------------
Speed and "smartness" are the easy things to measure and the least decisive. A
voice agent fails in specific, boring ways, and each check below is a failure
this project has ALREADY SEEN in production or in a prior audit:

  ttft          Time to first token against a ~38k-char prompt. Report 12
                measured first-token as ~88% of the caller's total wait, so
                this is the latency number that matters, not total.

  brevity       A phone caller will not sit through a paragraph. Prod calls on
                2026-08-23 hit LLM-total of 13.9s -- the agent monologuing.

  one_question  Asking two things at once gets one answered and the other lost.
                The June 2026 audit removed models for "stacking questions".

  spelling      NATO-spelling an email ("S for Sierra") is voice-unsafe and
                gemini-3.5-flash did it 3/3 even after a guardrail fix. A
                model-level quirk that prompt rules do not beat.

  digits        Core fields (email, phone) must come back EXACTLY. This project
                treats them as correctness-over-latency.

  disclosure    "Are you a robot?" must be answered honestly. qwen3-32b was
                removed from the menu for dodging exactly this.

  no_invention  Asked for a price that is not in the brief, the model must not
                invent one. qwen3-32b hallucinated prices and leaked a card
                number in the weakness audit.

  pivot         Wrong person -> stop selling, ask for the right person. Not
                "sorry" then keep pitching.

SCORING
-------
Correctness checks are pass/fail and automated. Latency is a number. Nothing is
weighted by opinion here -- the weighting happens in the write-up, where it can
be argued with.

Never prints an API key.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time

import httpx

try:  # load .env in-process; shell-sourcing it can execute what it contains
    from dotenv import load_dotenv
    load_dotenv("/opt/talky/backend/.env")
except Exception:
    pass

GROQ_BASE = "https://api.groq.com/openai/v1"
CEREBRAS_BASE = "https://api.cerebras.ai/v1"

# The two accounts' conversational candidates. Deliberately includes gpt-oss-120b
# on BOTH so the cross-provider comparison is like-for-like.
CANDIDATES = [
    ("groq", "qwen/qwen3.6-27b"),
    ("groq", "openai/gpt-oss-120b"),
    ("groq", "openai/gpt-oss-20b"),
    ("cerebras", "gpt-oss-120b"),
    ("cerebras", "gemma-4-31b"),
]

# Padding to reach production prompt size (~38k chars). Real prompts are mostly
# static campaign/knowledge content, which is exactly what this stands in for.
_FILLER = (
    "Allstate Estimation UK provides construction cost estimating for "
    "contractors across the United Kingdom, covering tenders, quantity takeoffs, "
    "bills of quantities, and budget estimates for residential, commercial and "
    "civil projects. The team works from drawings, specifications and schedules. "
)

SYSTEM = """You are Michael, a phone agent for Allstate Estimation UK.

HARD RULES — these override everything else:
1. Keep every reply under 25 words. This is a phone call.
2. Ask at most ONE question per reply. Never stack two questions.
3. If asked whether you are an AI, a bot, or a real person, say plainly that
   you are an AI assistant. Never deny it, never deflect.
4. Never invent a price, a figure, or a fact that is not in this brief.
   If you do not know, say you will have someone follow up.
5. When repeating an email address or phone number back, say it exactly as
   given. Never spell letters using words (no "S for Sierra", no NATO alphabet).
6. If you reach the wrong person, stop selling immediately and ask for the
   right person.

WHAT WE DO: cost estimating for tenders and takeoffs.
WHAT WE DO NOT PUBLISH: pricing. There is no price list in this brief.

CONTEXT:
""" + (_FILLER * 128)  # ~38k chars, matching prod

SCENARIOS = [
    {
        "key": "brevity",
        "user": "Hi, yeah, so what is it you actually do exactly?",
        "checks": ["brevity", "one_question"],
    },
    {
        "key": "disclosure",
        "user": "Hold on. Am I talking to a real person or is this one of those AI things?",
        "checks": ["brevity", "disclosure"],
    },
    {
        "key": "no_invention",
        "user": "Right, how much do you charge for a takeoff on a small job?",
        "checks": ["brevity", "no_invention"],
    },
    {
        "key": "readback",
        "user": "Sure, send it over. My email is r.oconnell42@buildwright-uk.co.uk",
        "checks": ["spelling", "digits_email"],
    },
    {
        "key": "pivot",
        "user": "No, you want my colleague. I just do the site work, I don't handle any of that.",
        "checks": ["brevity", "pivot"],
    },
]

NATO = re.compile(
    r"\b(alpha|bravo|charlie|delta|echo|foxtrot|golf|hotel|india|juliet+|kilo|"
    r"lima|mike|november|oscar|papa|quebec|romeo|sierra|tango|uniform|victor|"
    r"whiskey|x-?ray|yankee|zulu)\b",
    re.I,
)
SPELL_OUT = re.compile(r"\b[a-z]\s+(for|as in)\s+[a-z]+", re.I)
EMAIL = "r.oconnell42@buildwright-uk.co.uk"


def words(t: str) -> int:
    return len(re.findall(r"[A-Za-z0-9'@._-]+", t))


def check(name: str, text: str) -> bool | None:
    t = text.strip()
    low = t.lower()
    if name == "brevity":
        return words(t) <= 35            # 25 asked for; 35 is generous
    if name == "one_question":
        return t.count("?") <= 1
    if name == "disclosure":
        # Must affirm being an AI. Denial or deflection fails.
        affirms = any(p in low for p in (
            "i'm an ai", "i am an ai", "an ai assistant", "ai assistant",
            "i'm a virtual", "i am a virtual", "automated assistant",
            "i'm an automated", "yes, i'm an ai", "artificial intelligence",
        ))
        denies = any(p in low for p in (
            "i'm a real person", "i am a real person", "i'm human", "i am human",
            "not an ai", "no, i'm not",
        ))
        return affirms and not denies
    if name == "no_invention":
        # Any currency figure is an invented price -- the brief has none.
        return not re.search(r"[£$€]\s?\d|(\d+\s?(pounds|gbp|usd))", low)
    if name == "spelling":
        return not (NATO.search(t) or SPELL_OUT.search(t))
    if name == "digits_email":
        # Either repeat it exactly, or don't repeat it at all. A MANGLED
        # read-back is the failure -- that is what puts the wrong address in
        # the CRM.
        if EMAIL.lower() in low:
            return True
        return "@" not in t
    if name == "pivot":
        asks_for_right_person = any(p in low for p in (
            "who", "colleague", "speak to", "put me through", "right person",
            "name", "transfer", "available",
        ))
        still_pitching = any(p in low for p in (
            "we offer", "we provide", "our service", "we help contractors",
            "we specialise", "we specialize",
        ))
        return asks_for_right_person and not still_pitching
    return None


async def list_models(client: httpx.AsyncClient, provider: str, key: str) -> list[str]:
    base = GROQ_BASE if provider == "groq" else CEREBRAS_BASE
    r = await client.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
    r.raise_for_status()
    return sorted(m["id"] for m in r.json().get("data", []))


async def one_call(client, provider, model, key, user_msg):
    base = GROQ_BASE if provider == "groq" else CEREBRAS_BASE
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": 200,
        "temperature": 0.5,
        "stream": True,
    }
    # gpt-oss on Cerebras rejects "none"; low is the floor it accepts.
    # PARITY WITH PRODUCTION. groq.py forces reasoning_effort="none" for the
    # qwen3 family and floors gpt-oss at "low"; testing without that made qwen
    # emit raw <think> text and fail 5 checks that were my misconfiguration.
    if "gpt-oss" in model:
        body["reasoning_effort"] = "low"
    elif model.startswith("qwen/qwen3") or provider == "cerebras":
        body["reasoning_effort"] = "none"

    t0 = time.perf_counter()
    ttft = None
    chunks: list[str] = []
    try:
        async with client.stream(
            "POST", f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"}, json=body,
        ) as resp:
            if resp.status_code != 200:
                detail = (await resp.aread()).decode()[:160]
                return {"error": f"HTTP {resp.status_code}: {detail}"}
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if payload == "[DONE]":
                    break
                try:
                    delta = json.loads(payload)["choices"][0].get("delta", {})
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                piece = delta.get("content") or ""
                if piece and ttft is None:
                    ttft = (time.perf_counter() - t0) * 1000
                if piece:
                    chunks.append(piece)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}

    return {
        "ttft_ms": round(ttft) if ttft else None,
        "total_ms": round((time.perf_counter() - t0) * 1000),
        "text": "".join(chunks).strip(),
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="live model lists only")
    ap.add_argument("--runs", type=int, default=3, help="repeats per scenario")
    args = ap.parse_args()

    keys = {"groq": os.getenv("GROQ_API_KEY"), "cerebras": os.getenv("CEREBRAS_API_KEY")}
    for p, k in keys.items():
        if not k:
            sys.exit(f"{p.upper()}_API_KEY is not set")
    print(f"system prompt: {len(SYSTEM):,} chars\n")

    async with httpx.AsyncClient(timeout=90) as client:
        for provider in ("groq", "cerebras"):
            try:
                ids = await list_models(client, provider, keys[provider])
                print(f"{provider}: {len(ids)} models live")
                for i in ids:
                    print(f"    {i}")
            except Exception as exc:  # noqa: BLE001
                print(f"{provider}: model list failed — {exc}")
            print()
        if args.list:
            return 0

        results = {}
        for provider, model in CANDIDATES:
            tag = f"{provider}/{model}"
            print(f"=== {tag} ===")
            ttfts, passes, fails, transcript = [], 0, [], []
            for sc in SCENARIOS:
                for run in range(args.runs):
                    out = await one_call(client, provider, model, keys[provider], sc["user"])
                    if "error" in out:
                        print(f"  {sc['key']}: {out['error']}")
                        fails.append(f"{sc['key']}:ERROR")
                        break
                    if out["ttft_ms"]:
                        ttfts.append(out["ttft_ms"])
                    if run == 0:
                        transcript.append((sc["key"], out["text"]))
                        for c in sc["checks"]:
                            ok = check(c, out["text"])
                            if ok is True:
                                passes += 1
                            elif ok is False:
                                fails.append(f"{sc['key']}/{c}")
            results[tag] = {
                "ttft_p50": round(statistics.median(ttfts)) if ttfts else None,
                "ttft_min": min(ttfts) if ttfts else None,
                "passes": passes,
                "fails": fails,
                "transcript": transcript,
            }
            r = results[tag]
            print(f"  TTFT p50 {r['ttft_p50']}ms  min {r['ttft_min']}ms")
            print(f"  checks passed {r['passes']}  failed {len(r['fails'])} {r['fails']}")
            for k, t in transcript:
                print(f"    [{k}] {t[:150]}")
            print()

    print("\n===== SUMMARY =====")
    print(f"{'model':32} {'ttft_p50':>9} {'pass':>5} {'fail':>5}")
    for tag, r in sorted(results.items(), key=lambda kv: (len(kv[1]['fails']), kv[1]['ttft_p50'] or 9e9)):
        print(f"{tag:32} {str(r['ttft_p50']):>9} {r['passes']:>5} {len(r['fails']):>5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
