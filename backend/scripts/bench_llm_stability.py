#!/usr/bin/env python3
"""Latency STABILITY for the finalists — p50 is not the number that hurts.

A caller does not experience the median. They experience the turn that took
1.4 seconds, and they experience it as the agent being slow or broken. Two
models with the same p50 and different spread are not equally good, and the
first benchmark could not tell them apart because it only reported p50 and min.

Runs N sequential turns per model against a production-sized prompt and reports
p50 / p90 / p95 / max / stdev. Sequential rather than concurrent on purpose:
concurrency measures the provider's queueing, not what one call feels like.

Never prints an API key.
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time

import httpx

try:
    from dotenv import load_dotenv
    load_dotenv("/opt/talky/backend/.env")
except Exception:
    pass

GROQ_BASE = "https://api.groq.com/openai/v1"
CEREBRAS_BASE = "https://api.cerebras.ai/v1"

FINALISTS = [
    ("groq", "openai/gpt-oss-120b"),
    ("cerebras", "gemma-4-31b"),
    ("cerebras", "gpt-oss-120b"),
    ("groq", "openai/gpt-oss-20b"),
    ("groq", "qwen/qwen3.6-27b"),
]

_FILLER = (
    "Allstate Estimation UK provides construction cost estimating for "
    "contractors across the United Kingdom, covering tenders, quantity takeoffs, "
    "bills of quantities, and budget estimates for residential, commercial and "
    "civil projects. The team works from drawings, specifications and schedules. "
)
SYSTEM = (
    "You are Michael, a phone agent for Allstate Estimation UK. Keep every "
    "reply under 25 words. Ask at most one question per reply.\n\nCONTEXT:\n"
    + _FILLER * 128
)

TURNS = [
    "Sorry, who is this again?",
    "What do you actually do?",
    "How long does an estimate usually take?",
    "We already use someone for that.",
    "Alright, what would you need from me?",
]


async def ttft(client, provider, model, key, msg) -> float | None:
    base = GROQ_BASE if provider == "groq" else CEREBRAS_BASE
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": msg}],
        "max_tokens": 120, "temperature": 0.5, "stream": True,
    }
    if "gpt-oss" in model:
        body["reasoning_effort"] = "low"
    elif model.startswith("qwen/qwen3") or provider == "cerebras":
        body["reasoning_effort"] = "none"

    t0 = time.perf_counter()
    try:
        async with client.stream("POST", f"{base}/chat/completions",
                                 headers={"Authorization": f"Bearer {key}"},
                                 json=body) as resp:
            if resp.status_code != 200:
                return None
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                p = line[6:].strip()
                if p == "[DONE]":
                    break
                try:
                    d = json.loads(p)["choices"][0].get("delta", {})
                except Exception:
                    continue
                if d.get("content"):
                    return (time.perf_counter() - t0) * 1000
    except Exception:
        return None
    return None


def pct(xs: list[float], p: float) -> float:
    s = sorted(xs)
    k = max(0, min(len(s) - 1, int(round((p / 100) * (len(s) - 1)))))
    return s[k]


async def main() -> int:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    keys = {"groq": os.getenv("GROQ_API_KEY"), "cerebras": os.getenv("CEREBRAS_API_KEY")}
    for p, k in keys.items():
        if not k:
            sys.exit(f"{p.upper()}_API_KEY is not set")

    n = rounds * len(TURNS)
    print(f"prompt {len(SYSTEM):,} chars — {n} sequential turns per model\n")
    print(f"{'model':30} {'p50':>6} {'p90':>6} {'p95':>6} {'max':>6} {'stdev':>7} {'n':>4}")

    async with httpx.AsyncClient(timeout=90) as client:
        for provider, model in FINALISTS:
            samples: list[float] = []
            for _ in range(rounds):
                for turn in TURNS:
                    v = await ttft(client, provider, model, keys[provider], turn)
                    if v:
                        samples.append(v)
            tag = f"{provider}/{model}"
            if not samples:
                print(f"{tag:30}  no samples")
                continue
            print(f"{tag:30} {round(pct(samples,50)):>6} {round(pct(samples,90)):>6} "
                  f"{round(pct(samples,95)):>6} {round(max(samples)):>6} "
                  f"{round(statistics.pstdev(samples)):>7} {len(samples):>4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
