"""AI call summarizer — Groq llama-3.3-70b-versatile → structured JSON.

Every field in the schema is ALWAYS present in the returned dict. Missing
dimensions are filled from EMPTY_SUMMARY so downstream code can read any
key unconditionally. This module never raises; all network errors are
caught and produce a fail-soft fallback.
"""
from __future__ import annotations

import json
import logging
import os
from copy import deepcopy

from groq import AsyncGroq

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an expert call analyst summarizing a sales or support phone-call transcript.

Return ONLY a single JSON object — no markdown, no prose, no code fences.
The object MUST contain exactly these keys (and no others):

{
  "headline": "<1-line outcome + gist, e.g. Qualified — wants a demo next week>",
  "outcome": "<short outcome label + one-line why, e.g. qualified | disqualified | callback | no_interest | voicemail | error>",
  "what_happened": "<2-4 sentence chronological gist of the call>",
  "key_points": ["<caller needs, questions, or context — one item per bullet>"],
  "objections": [{"objection": "<what they pushed back on>", "handled": "<how it was addressed or 'unresolved'>"}],
  "commitments": ["<what either side explicitly agreed to>"],
  "action_items": [{"item": "<specific task>", "owner": "<agent|caller|user>"}],
  "sentiment": "<positive|neutral|negative + one-word or short note>",
  "next_step": "<single concrete next action>",
  "notable_quotes": ["<short verbatim line(s) that best capture the call>"]
}

Rules:
- Every key MUST be present. If a dimension has nothing (no objections, no commitments, etc.) use an empty list [] or "none" — NEVER omit the key.
- Be comprehensive: capture every objection, commitment, action item, number, name, and date mentioned. Skip nothing material.
- Keep each string tight (no waffle, no filler).
- owner in action_items must be one of: agent, caller, user.
- Respond ONLY with the JSON object. Any extra text will break parsing."""

# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

EMPTY_SUMMARY: dict = {
    "headline": "",
    "outcome": "",
    "what_happened": "",
    "key_points": [],
    "objections": [],
    "commitments": [],
    "action_items": [],
    "sentiment": "",
    "next_step": "",
    "notable_quotes": [],
}

_SCHEMA_KEYS = set(EMPTY_SUMMARY.keys())

# Keys whose values must be lists (coerce scalars → single-element list)
_LIST_KEYS = {"key_points", "objections", "commitments", "action_items", "notable_quotes"}

# Keys whose values must be strings
_STR_KEYS = {"headline", "outcome", "what_happened", "sentiment", "next_step"}


# ---------------------------------------------------------------------------
# Coercion
# ---------------------------------------------------------------------------

def _coerce(raw: dict) -> dict:
    """Return a dict with EXACTLY the schema keys.

    - Missing keys → filled from EMPTY_SUMMARY (deep copy).
    - Extra keys → dropped.
    - Type mismatches → best-effort coercion (str stays str, list stays list).
    """
    out: dict = {}
    for key in _SCHEMA_KEYS:
        value = raw.get(key, deepcopy(EMPTY_SUMMARY[key]))
        if key in _STR_KEYS:
            if not isinstance(value, str):
                value = str(value) if value is not None else ""
        elif key in _LIST_KEYS:
            if not isinstance(value, list):
                # Scalar or None → wrap in list (better than losing data)
                value = [value] if value not in (None, "", []) else []
        out[key] = value
    return out


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def summarize_transcript(transcript_text: str) -> dict:
    """Summarize *transcript_text* into a structured dict.

    Always returns a dict with all schema keys. Never raises.

    Edge cases:
    - Empty / whitespace-only transcript → EMPTY_SUMMARY copy with
      headline "No conversation recorded".
    - JSON parse failure → one retry with an explicit JSON instruction;
      if still bad → EMPTY_SUMMARY copy with headline "Summary unavailable".
    - Any network / SDK exception → same fail-soft fallback.
    """
    if not transcript_text or not transcript_text.strip():
        result = deepcopy(EMPTY_SUMMARY)
        result["headline"] = "No conversation recorded"
        return result

    try:
        client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))

        async def _call(user_content: str) -> str:
            resp = await client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.3,
                max_tokens=1500,
                response_format={"type": "json_object"},
            )
            return resp.choices[0].message.content or ""

        raw_content = await _call(transcript_text)

        try:
            parsed = json.loads(raw_content)
        except json.JSONDecodeError:
            logger.warning(
                "call_summarizer: first JSON parse failed — retrying with explicit instruction"
            )
            retry_content = (
                transcript_text
                + "\n\nReturn ONLY valid JSON matching the schema. No prose, no markdown."
            )
            raw_content2 = await _call(retry_content)
            try:
                parsed = json.loads(raw_content2)
            except json.JSONDecodeError:
                logger.warning(
                    "call_summarizer: second JSON parse failed — returning fallback"
                )
                result = deepcopy(EMPTY_SUMMARY)
                result["headline"] = "Summary unavailable"
                return result

        return _coerce(parsed)

    except Exception as exc:
        logger.warning("call_summarizer: unexpected error — %s", exc)
        result = deepcopy(EMPTY_SUMMARY)
        result["headline"] = "Summary unavailable"
        return result
