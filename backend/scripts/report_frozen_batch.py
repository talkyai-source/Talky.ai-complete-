#!/usr/bin/env python3
"""Report one frozen batch of live calls — every metric, per call, in one table.

    python scripts/report_frozen_batch.py --campaign 09b7ee9c --since '2 hours ago'
    python scripts/report_frozen_batch.py --campaign 09b7ee9c --since today --json

WHY THIS EXISTS
---------------
A "frozen batch" is only meaningful if every call in it is measured the same
way, from the same sources, with the prompt version pinned. Reading the journal
by hand produces a different subset of evidence every time, and the numbers that
matter here live in three places at once:

    journald   TTFT, speech-to-audio, cache hit ratio, STT/LLM failover
    calls      duration, status, prompt version + hash (the freeze proof)
    transcripts / conversation_reviews   what was said, and what a human scored it

WHAT IT REPORTS, AND WHY EACH ONE
----------------------------------
  prompt_cache    cached_tokens / prompt_tokens. The ONLY honest proof the cache
                  is working -- TTFT cannot distinguish a warm cache from a
                  quiet afternoon. Expect a cold first call, warm after.
  llm_ttft        Time to the model's first token. ~88% of what the caller waits.
  speech_to_audio Caller stops talking -> caller hears audio. The number the
                  human actually experiences.
  answer_ms       LLM-total: how long the agent kept talking. Prod hit 13.9s on
                  2026-08-23, which is a monologue, and it is invisible to the
                  [SLOW] flag because TTS streams sentence-by-sentence.
  stt_failover    Flux going silent mid-call. Ran ~11.5% on 2026-08-23, once
                  exhausting BOTH engines.
  llm_failover    Primary -> secondary. On a fault-injected call this is the
                  proof the Groq fallback actually answered.
  email_readback  Did the agent repeat a captured address back EXACTLY? A
                  mangled read-back is what silently puts a wrong email in the
                  CRM.
  score           conversation_reviews rating, if a human left one.

FREEZE PROOF
------------
Every row carries prompt_version and prompt_hash. If they are not identical
across the batch, it was not one experiment and the numbers should not be
pooled -- the script says so rather than averaging regardless.

A MISSING VALUE IS NOT AGREEMENT (2026-08-28)
---------------------------------------------
This check used to run on the display strings, where a NULL prompt identity
had already been rendered as "-". A batch in which EVERY call was missing its
identity therefore collapsed to one distinct pair, ("-", "-"), and the script
printed its strongest possible result -- "FROZEN: every call ran prompt - hash
-" -- at precisely the moment it had no evidence at all. Any row without a
real (version, hash) now fails the gate as NO EVIDENCE.

EXIT CODE
---------
0 only when the batch is proven frozen. Non-zero otherwise (no calls found,
identities missing, or more than one identity), so this can gate CI.

Read-only. Never prints an API key.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import subprocess
import sys
from collections import defaultdict

import asyncpg

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

PATTERNS = {
    "call_start":   re.compile(r"\[call=([0-9a-f-]{8,})\]"),
    "latency":      re.compile(
        r"\[call=([0-9a-f-]+)\].*?Turn (\d+) latency: (-?\d+)ms .*?"
        r"LLM-first-token: (\d+)ms.*?LLM-total: (-?\d+)ms"),
    "cache":        re.compile(
        r"cerebras_prompt_cache .*?prompt_tokens=(\d+) cached_tokens=(\d+) "
        r"hit_ratio=([\d.]+)"),
    "cache_call":   re.compile(r"\[call=([0-9a-f-]+)\].*?cerebras_prompt_cache"),
    "stt_failover": re.compile(r"\[call=([0-9a-f-]+)\].*?resilient_stt_failed_over_to"),
    "stt_both":     re.compile(r"\[call=([0-9a-f-]+)\].*?resilient_stt_secondary_also_silent"),
    "llm_failover": re.compile(r"\[call=([0-9a-f-]+)\].*?llm_failover outcome=(\w+)"),
    "fault_fired":  re.compile(r"\[call=([0-9a-f-]+)\].*?llm_fault_injection_firing"),
}


def journal(since: str) -> str:
    try:
        return subprocess.run(
            ["journalctl", "-u", "talky-api", "--since", since, "--no-pager"],
            capture_output=True, text=True, timeout=180,
        ).stdout
    except Exception as exc:  # noqa: BLE001
        print(f"journal read failed: {exc}", file=sys.stderr)
        return ""


def parse_journal(text: str) -> dict:
    per = defaultdict(lambda: {
        "ttft": [], "s2a": [], "answer": [], "cache": [],
        "stt_failover": 0, "stt_both": 0, "llm_failover": [], "fault_fired": 0,
    })
    for line in text.splitlines():
        m = PATTERNS["latency"].search(line)
        if m:
            cid, _turn, total, ttft, llm_total = m.groups()
            d = per[cid[:8]]
            d["ttft"].append(int(ttft))
            if int(total) >= 0:            # negative = the known metric bug
                d["s2a"].append(int(total))
            if int(llm_total) >= 0:
                d["answer"].append(int(llm_total))
            continue
        if "cerebras_prompt_cache" in line:
            cm, cc = PATTERNS["cache"].search(line), PATTERNS["cache_call"].search(line)
            if cm:
                cid = cc.group(1)[:8] if cc else "-"
                pt, ct, ratio = cm.groups()
                per[cid]["cache"].append((int(pt), int(ct), float(ratio)))
            continue
        for key in ("stt_failover", "stt_both", "fault_fired"):
            m = PATTERNS[key].search(line)
            if m:
                per[m.group(1)[:8]][key] += 1
        m = PATTERNS["llm_failover"].search(line)
        if m:
            per[m.group(1)[:8]]["llm_failover"].append(m.group(2))
    return per


# ── freeze gate ─────────────────────────────────────────────────────────────
# The placeholder main() substitutes for a NULL column when rendering the
# table. It must never be treated as a prompt identity.
MISSING = "-"


def _identity_missing(version, prompt_hash) -> bool:
    """True when this row carries no real prompt identity.

    Covers the rendered placeholder, NULL, and whitespace-only values — the
    row may reach here either pre- or post-render depending on the caller.
    """
    for value in (version, prompt_hash):
        if value is None:
            return True
        text = str(value).strip()
        if not text or text == MISSING:
            return True
    return False


def freeze_verdict(rows: list) -> tuple[bool, list]:
    """Decide whether this batch is one frozen experiment.

    Returns ``(frozen, lines)`` — ``lines`` are printed verbatim.

    Three ways to fail, and a missing identity is reported FIRST because it is
    the weakest evidence, not the strongest: "NOT FROZEN" is the existing
    vocabulary a reader acts on, so the missing case keeps that headline and
    adds "NO EVIDENCE" to say *why* it failed. A separate quieter state would
    let the batch that proves nothing read as less alarming than the batch
    that merely mixed two known prompts.
    """
    if not rows:
        return False, [
            "NOT FROZEN — NO EVIDENCE: the batch is empty; nothing was proven."
        ]

    missing = [r for r in rows if _identity_missing(r.get("version"), r.get("hash"))]
    identities = sorted(
        {
            (r["version"], r["hash"])
            for r in rows
            if not _identity_missing(r.get("version"), r.get("hash"))
        }
    )

    if missing:
        lines = [
            f"NOT FROZEN — NO EVIDENCE: {len(missing)}/{len(rows)} calls carry no "
            "prompt identity",
            "  (calls.prompt_version / calls.prompt_hash are NULL on those rows).",
            "  A missing value is not agreement. Do NOT pool these numbers.",
        ]
        if identities:
            lines.append(
                f"  {len(identities)} identity(ies) recorded on the remaining calls:"
            )
            lines += [f"    {v}/{h}" for v, h in identities]
        return False, lines

    if len(identities) == 1:
        v, h = identities[0]
        return True, [f"FROZEN: every call ran prompt {v} hash {h}"]

    lines = [f"NOT FROZEN — {len(identities)} distinct prompt identities in this batch:"]
    lines += [f"    {v}/{h}" for v, h in identities]
    lines.append("  Do NOT pool these numbers; they are not one experiment.")
    return False, lines


def exit_code(frozen: bool) -> int:
    """0 only on a proven freeze, so CI can gate on this script."""
    return 0 if frozen else 2


def dsn() -> str:
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        try:
            sys.path.insert(0, os.getcwd())
            from app.core.config import get_settings
            raw = get_settings().database_url
        except Exception:
            sys.exit("DATABASE_URL not set and app config unavailable")
    return raw.replace("postgresql+asyncpg", "postgresql")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", required=True, help="campaign id or 8-char prefix")
    ap.add_argument("--since", default="today")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    jrn = parse_journal(journal(args.since))
    conn = await asyncpg.connect(dsn())
    rows = await conn.fetch(
        """
        SELECT c.id, c.status, c.duration_seconds, c.created_at,
               c.prompt_version, c.prompt_hash, c.is_test,
               c.transcript,
               (SELECT round(avg(r.rating)::numeric, 1)
                  FROM conversation_reviews r WHERE r.call_id = c.id) AS score
          FROM calls c
         WHERE c.campaign_id::text LIKE $1
           AND c.created_at > now() - interval '3 days'
         ORDER BY c.created_at DESC
         LIMIT $2
        """,
        f"{args.campaign}%", args.limit,
    )
    await conn.close()

    if not rows:
        print(f"no calls found for campaign {args.campaign}")
        return 1

    out = []
    for r in rows:
        cid = str(r["id"])[:8]
        j = jrn.get(cid, {})
        cache = j.get("cache") or []
        txt = r["transcript"] or ""
        emails = EMAIL_RE.findall(txt)
        out.append({
            "call": cid,
            "status": r["status"],
            "secs": r["duration_seconds"] or 0,
            "version": r["prompt_version"] or "-",
            "hash": (r["prompt_hash"] or "-")[:8],
            "turns": len(j.get("ttft") or []),
            "ttft_p50": int(statistics.median(j["ttft"])) if j.get("ttft") else None,
            "s2a_p50": int(statistics.median(j["s2a"])) if j.get("s2a") else None,
            "answer_max": max(j["answer"]) if j.get("answer") else None,
            "cache_hit": round(statistics.mean(c[2] for c in cache), 2) if cache else None,
            "cached_tok": max((c[1] for c in cache), default=None),
            "stt_fo": j.get("stt_failover", 0),
            "stt_both": j.get("stt_both", 0),
            "llm_fo": ",".join(j.get("llm_failover") or []) or "-",
            "fault": j.get("fault_fired", 0),
            "emails": len(set(emails)),
            "score": float(r["score"]) if r["score"] is not None else None,
        })

    frozen, verdict_lines = freeze_verdict(out)

    if args.json:
        print(json.dumps(out, indent=2, default=str))
        # Shape of the JSON payload is unchanged (a list of call rows); the
        # verdict rides the exit code so `--json` gates CI identically.
        return exit_code(frozen)

    print(f"\ncampaign {args.campaign} — {len(out)} calls\n")
    hdr = (f"{'call':9}{'st':11}{'s':>4}{'turn':>5}{'ttft':>6}{'s2a':>6}"
           f"{'ansMax':>8}{'cache':>7}{'cTok':>7}{'sttFO':>6}{'llmFO':>18}"
           f"{'em':>4}{'score':>6}  version/hash")
    print(hdr)
    print("-" * len(hdr))
    for o in out:
        print(f"{o['call']:9}{o['status'][:10]:11}{o['secs']:>4}{o['turns']:>5}"
              f"{str(o['ttft_p50'] or '-'):>6}{str(o['s2a_p50'] or '-'):>6}"
              f"{str(o['answer_max'] or '-'):>8}{str(o['cache_hit'] or '-'):>7}"
              f"{str(o['cached_tok'] or '-'):>7}"
              f"{(str(o['stt_fo']) + ('!' if o['stt_both'] else '')):>6}"
              f"{o['llm_fo'][:17]:>18}{o['emails']:>4}"
              f"{str(o['score'] or '-'):>6}  {o['version']}/{o['hash']}")

    # ── freeze check ────────────────────────────────────────────────────────
    print()
    for line in verdict_lines:
        print(line)

    ttfts = [o["ttft_p50"] for o in out if o["ttft_p50"]]
    s2as = [o["s2a_p50"] for o in out if o["s2a_p50"]]
    hits = [o["cache_hit"] for o in out if o["cache_hit"] is not None]
    if ttfts:
        print(f"\nLLM TTFT      p50 {int(statistics.median(ttfts))}ms  "
              f"min {min(ttfts)}  max {max(ttfts)}")
    if s2as:
        print(f"speech->audio p50 {int(statistics.median(s2as))}ms  "
              f"min {min(s2as)}  max {max(s2as)}")
    if hits:
        print(f"cache hit     mean {statistics.mean(hits):.2f}  "
              f"cold calls (0.00): {sum(1 for h in hits if h == 0)}/{len(hits)}")
    else:
        print("cache hit     NO DATA — cerebras_prompt_cache never logged. "
              "Either the tenant is not on Cerebras, or include_usage is missing.")
    print(f"STT failover  {sum(o['stt_fo'] for o in out)} "
          f"(both-engines-silent: {sum(o['stt_both'] for o in out)})")
    print(f"LLM failover  {sum(1 for o in out if o['llm_fo'] != '-')} calls  "
          f"| fault injections fired: {sum(o['fault'] for o in out)}")
    scored = [o["score"] for o in out if o["score"] is not None]
    print(f"scored        {len(scored)}/{len(out)} calls"
          + (f", mean {statistics.mean(scored):.1f}" if scored else ""))
    return exit_code(frozen)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
