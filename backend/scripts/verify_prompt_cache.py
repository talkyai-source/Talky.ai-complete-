#!/usr/bin/env python3
"""Prove the prompt cache engages — and that a leading name destroys it.

    python scripts/verify_prompt_cache.py

Runs the SAME conversation twice against each provider, once with the per-lead
block at the FRONT (how it used to be) and once at the BACK (how it is now),
and reports `cached_tokens` for each.

WHY THIS EXISTS
---------------
TTFT is not proof. A fast reply can be fast because the provider had a quiet
minute, and a cache that silently stopped working looks exactly like a slow
afternoon. `cached_tokens` is the provider telling us directly how much of the
prompt it did not have to re-read, and it is the only number that settles it.

WHAT TO EXPECT
--------------
  call 1 (cold)     low or zero cached_tokens — the prefix has to be seen once
  call 2 SUFFIX     high — the static prefix is byte-identical, so it is reused
  call 2 PREFIX     ~zero — a different name in the first tokens voids every
                    block behind it, which is the bug this run demonstrates

Never prints an API key.
"""
from __future__ import annotations

import asyncio
import json
import os
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

TARGETS = [
    ("cerebras", "gpt-oss-120b", CEREBRAS_BASE),
    ("groq", "openai/gpt-oss-20b", GROQ_BASE),
]

_FILLER = (
    "Allstate Estimation UK provides construction cost estimating for "
    "contractors across the United Kingdom, covering tenders, quantity "
    "takeoffs, bills of quantities, and budget estimates. "
)
STATIC = (
    "You are Michael, a phone agent for Allstate Estimation UK. Keep every "
    "reply under 25 words. Ask at most one question per reply.\n\nCONTEXT:\n"
    + _FILLER * 128
)


def lead_block(name: str) -> str:
    return (
        f"\n---\nPERSON YOU'RE CALLING: {name}. Greet them by first name and "
        "confirm you have the right person before launching in.\n"
    )


async def ask(client, base, key, model, system, user, cache_key):
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": 60, "temperature": 0.5, "stream": False,
        "reasoning_effort": "low",
    }
    if cache_key:
        body["prompt_cache_key"] = cache_key
    r = await client.post(f"{base}/chat/completions",
                          headers={"Authorization": f"Bearer {key}"}, json=body)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:120]}"
    data = r.json()
    usage = data.get("usage", {}) or {}
    details = usage.get("prompt_tokens_details") or {}
    return {
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "cached_tokens": details.get("cached_tokens", 0),
    }, None


async def main() -> int:
    keys = {"groq": os.getenv("GROQ_API_KEY"), "cerebras": os.getenv("CEREBRAS_API_KEY")}
    for p, k in keys.items():
        if not k:
            sys.exit(f"{p.upper()}_API_KEY is not set")

    names = ["Rory O'Connell", "Sian Whitfield", "Dermot Fahey"]
    print(f"static prefix: {len(STATIC):,} chars\n")

    async with httpx.AsyncClient(timeout=90) as client:
        for provider, model, base in TARGETS:
            print(f"=== {provider}/{model} ===")
            for layout in ("SUFFIX (fixed)", "PREFIX (the old bug)"):
                print(f"  {layout}")
                for i, name in enumerate(names):
                    system = (
                        STATIC + lead_block(name)
                        if layout.startswith("SUFFIX")
                        else lead_block(name) + STATIC
                    )
                    out, err = await ask(
                        client, base, keys[provider], model, system,
                        "What do you do?",
                        # Cerebras ONLY. Groq rejects this property outright
                        # with HTTP 400 -- sending it to both would take the
                        # fallback offline entirely.
                        cache_key="verify-cache-demo" if provider == "cerebras" else None,
                    )
                    if err:
                        print(f"    call {i+1}: {err}")
                        continue
                    pt, ct = out["prompt_tokens"], out["cached_tokens"]
                    ratio = (ct / pt * 100) if pt else 0
                    print(f"    call {i+1} ({name:16}): "
                          f"prompt={pt:>6}  cached={ct:>6}  hit={ratio:5.1f}%")
                    await asyncio.sleep(0.4)
                print()
    print("A high hit% on SUFFIX calls 2-3 and ~0% on PREFIX is the whole point.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
