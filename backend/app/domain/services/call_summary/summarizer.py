"""AI call summarizer — Groq GPT-OSS 20B → structured JSON.

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

from app.infrastructure.llm.structured_output import (
    response_format_for,
    strict_mode_active,
    summariser_model,
)

from groq import AsyncGroq

logger = logging.getLogger(__name__)

# Headline used when summarization fails (network/SDK error — including a Groq
# 429 rate-limit — or output that won't parse as JSON twice). This is a
# TRANSIENT-failure sentinel: store.generate_and_store must NOT persist a
# summary carrying this headline, so the call is retried on the next view /
# backfill instead of being permanently stuck showing "Summary unavailable".
SUMMARY_UNAVAILABLE_HEADLINE = "Summary unavailable"

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an expert call analyst summarizing a sales or support phone-call transcript.

Return ONLY a single JSON object — no markdown, no prose, no code fences.
The object MUST contain exactly these keys (and no others):

{
  "headline": "<1-line outcome + gist, e.g. Qualified — wants a demo next week>",
  "outcome": "<short outcome label + one-line why, e.g. qualified | disqualified | callback | no_interest | voicemail | error>",
  "qualification_status": "<qualified | nurture | unqualified | unknown>",
  "decision_maker_status": "<decision_maker | influencer | not_decision_maker | unknown>",
  "identified_need": "<the prospect's stated need or 'unknown'>",
  "timeline": "<the prospect's stated timing or 'unknown'>",
  "budget_information": "<only budget/commercial information actually stated, or 'unknown'>",
  "what_happened": "<2-4 sentence chronological gist of the call>",
  "key_points": ["<caller needs, questions, or context — one item per bullet>"],
  "objections": [{"objection": "<what they pushed back on>", "handled": "<how it was addressed or 'unresolved'>"}],
  "commitments": ["<what either side explicitly agreed to>"],
  "action_items": [{"item": "<specific task>", "owner": "<agent|caller|user>"}],
  "sentiment": "<positive|neutral|negative + one-word or short note>",
  "next_step": "<single concrete next action>",
  "follow_up_tips": ["<actionable tip to follow up effectively — what to say or send, the best timing/channel, which objection to address, how to move them forward>"],
  "notable_quotes": ["<short verbatim line(s) that best capture the call>"]
}

Rules:
- Every key MUST be present. If a dimension has nothing (no objections, no commitments, etc.) use an empty list [] or "none" — NEVER omit the key.
- Be comprehensive: capture every objection, commitment, action item, number, name, and date mentioned. Skip nothing material.
- next_step is the ONE immediate action; follow_up_tips are 2-4 concrete, practical suggestions for how to actually win the follow-up (timing, what to send, which concern to lead with). Make them specific to THIS call, not generic.
- qualification_status is qualified only when a relevant need/fit and a concrete next step are supported by the transcript; nurture means possible need but later timing, missing information, or another decision-maker; unqualified means a clearly unsuitable fit or no relevant need; otherwise use unknown.
- Never infer authority, need, timing, or budget from tone, politeness, industry stereotypes, or the agent's pitch. Use unknown when the prospect did not reliably confirm it.
- Keep each string tight (no waffle, no filler).
- owner in action_items must be one of: agent, caller, user.
- Respond ONLY with the JSON object. Any extra text will break parsing."""

# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

EMPTY_SUMMARY: dict = {
    "headline": "",
    "outcome": "",
    "qualification_status": "unknown",
    "decision_maker_status": "unknown",
    "identified_need": "unknown",
    "timeline": "unknown",
    "budget_information": "unknown",
    "what_happened": "",
    "key_points": [],
    "objections": [],
    "commitments": [],
    "action_items": [],
    "sentiment": "",
    "next_step": "",
    "follow_up_tips": [],
    "notable_quotes": [],
}

_SCHEMA_KEYS = set(EMPTY_SUMMARY.keys())

# Two of the list keys are asked for in _SYSTEM_PROMPT as arrays of OBJECTS,
# not strings. Deriving every list key as an array of strings contradicted the
# prompt, so the model obeyed the prompt and the provider's structured-output
# validator rejected the entire summary:
#
#   400 json_validate_failed — '/action_items/0' does not validate with
#   /properties/action_items/items/type: expected string, but got object
#
# Nothing was stored on those calls. Worse, lead qualification reads the
# summary (store._summary_supports_lead), so a rejected summary also meant no
# lead decision was ever made. 6 failures in 7 days; only ~26 of 84 calls in
# 30 days had a summary at all (2026-09-22).
#
# The shapes below are the prompt's, spelled out. test_call_summary_schema.py
# asserts the two stay in step, which is what "cannot drift" needed to mean.
_OBJECT_LIST_ITEMS = {
    "objections": {
        "type": "object",
        "properties": {
            "objection": {"type": "string"},
            "handled": {"type": "string"},
        },
        "required": ["objection", "handled"],
        "additionalProperties": False,
    },
    "action_items": {
        "type": "object",
        "properties": {
            "item": {"type": "string"},
            "owner": {"type": "string"},
        },
        "required": ["item", "owner"],
        "additionalProperties": False,
    },
}

# JSON-Schema property types, derived from EMPTY_SUMMARY so the two cannot
# drift: a list default means an array (of objects where the prompt asks for
# objects, of strings otherwise), anything else a string.
_SUMMARY_SCHEMA_PROPERTIES = {
    key: (
        {"type": "array", "items": _OBJECT_LIST_ITEMS[key]}
        if key in _OBJECT_LIST_ITEMS
        else {"type": "array", "items": {"type": "string"}}
        if isinstance(default, list)
        else {"type": "string"}
    )
    for key, default in EMPTY_SUMMARY.items()
}

# Keys whose values must be lists (coerce scalars → single-element list)
_LIST_KEYS = {"key_points", "objections", "commitments", "action_items", "follow_up_tips", "notable_quotes"}

# Output budget. This was a flat 1500 tokens however long the call was, and on
# a 243-second call with 353 transcript rows the model ran out mid-document:
#
#   400 json_validate_failed - 'max completion tokens reached before
#   generating a valid document'
#
# Under constrained decoding that is a hard error rather than a short answer:
# the whole summary is discarded, and because lead qualification reads the
# summary the call gets no lead decision either (call 2427af7e, 2026-09-22).
#
# The summary is a fixed-shape document whose six list fields grow with how
# much HAPPENED on a call rather than linearly with its length, so the budget
# grows sub-linearly and is capped. The ceiling is what a retry uses.
_SUMMARY_MIN_TOKENS = 2400
_SUMMARY_MAX_TOKENS = 8000

# Groq reports an exhausted output budget in the message rather than through a
# distinct error code, so the retry has to match on it.
_TOKENS_EXHAUSTED = "max completion tokens reached"


def _summary_token_budget(transcript_text: str) -> int:
    """Output tokens to allow for a transcript of this size."""
    transcript_tokens = (len(transcript_text or "") + 3) // 4
    budget = _SUMMARY_MIN_TOKENS + transcript_tokens // 4
    return max(_SUMMARY_MIN_TOKENS, min(_SUMMARY_MAX_TOKENS, budget))

# Keys whose values must be strings
_STR_KEYS = {
    "headline",
    "outcome",
    "qualification_status",
    "decision_maker_status",
    "identified_need",
    "timeline",
    "budget_information",
    "what_happened",
    "sentiment",
    "next_step",
}


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

        # Strongest response_format this model can honour. On a strict-capable
        # model (gpt-oss) the decoder is constrained at the TOKEN level, so the
        # reply cannot be invalid JSON or miss a key; on every other model this
        # is the json_object form that was here before. The call site does not
        # branch — see infrastructure/llm/structured_output.py for why.
        _model = summariser_model()
        _response_format = response_format_for(
            _model, properties=_SUMMARY_SCHEMA_PROPERTIES, name="call_summary"
        )
        _strict = strict_mode_active(_model)

        async def _call(user_content: str, max_tokens: int) -> str:
            resp = await client.chat.completions.create(
                model=_model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.3,
                max_tokens=max_tokens,
                response_format=_response_format,
            )
            return resp.choices[0].message.content or ""

        _budget = _summary_token_budget(transcript_text)
        try:
            raw_content = await _call(transcript_text, _budget)
        except Exception as exc:  # noqa: BLE001 - re-raised below unless it is the budget
            if _TOKENS_EXHAUSTED not in str(exc) or _budget >= _SUMMARY_MAX_TOKENS:
                raise
            # Losing an entire summary because the estimate was low is not worth
            # it; one retry at the ceiling costs a few seconds after the call
            # has already ended.
            logger.warning(
                "call_summarizer: %d output tokens were not enough for a "
                "%d-char transcript — retrying at %d",
                _budget,
                len(transcript_text),
                _SUMMARY_MAX_TOKENS,
            )
            raw_content = await _call(transcript_text, _SUMMARY_MAX_TOKENS)

        try:
            parsed = json.loads(raw_content)
        except json.JSONDecodeError:
            if _strict:
                # Unreachable by construction: constrained decoding cannot emit
                # invalid JSON. If it ever fires, the assumption is wrong and a
                # silent retry would hide that — so say so and take the
                # fallback rather than pretending the guarantee held.
                logger.error(
                    "call_summarizer: STRICT schema model %s returned invalid "
                    "JSON — the constrained-decoding guarantee did not hold",
                    _model,
                )
                result = deepcopy(EMPTY_SUMMARY)
                result["headline"] = SUMMARY_UNAVAILABLE_HEADLINE
                return result
            logger.warning(
                "call_summarizer: first JSON parse failed — retrying with explicit instruction"
            )
            retry_content = (
                transcript_text
                + "\n\nReturn ONLY valid JSON matching the schema. No prose, no markdown."
            )
            raw_content2 = await _call(retry_content, _SUMMARY_MAX_TOKENS)
            try:
                parsed = json.loads(raw_content2)
            except json.JSONDecodeError:
                logger.warning(
                    "call_summarizer: second JSON parse failed — returning fallback"
                )
                result = deepcopy(EMPTY_SUMMARY)
                result["headline"] = SUMMARY_UNAVAILABLE_HEADLINE
                return result

        return _coerce(parsed)

    except Exception as exc:
        logger.warning("call_summarizer: unexpected error — %s", exc)
        result = deepcopy(EMPTY_SUMMARY)
        result["headline"] = SUMMARY_UNAVAILABLE_HEADLINE
        return result
