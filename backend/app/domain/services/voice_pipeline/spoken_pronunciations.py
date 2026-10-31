"""Campaign pronunciations applied to what the voice says (2026-10-20).

The campaign's ``pronunciations`` slot used to reach only the AI prompt
(composer._format_pronunciations), so the voice could still misread a name the
AI wrote correctly. ElevenLabs and Cartesia apply pronunciations at the voice
layer; LiveKit's ``text_transforms.replace()`` rewrites the text sent to the
voice. Same idea here, with two differences: whole-word matching (LiveKit
matches inside words, so "API" would also change "rapid"), and no streaming
hold-back, because the turn streamer only ever speaks complete sentences or
clauses, so a word is never split between two pieces.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

# Only spellings a voice can read as written. IPA ("/ŋwɪən/", "ɛks") would be
# read out symbol by symbol, so those entries stay in the AI prompt only.
_SAYABLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 '’.,\-]*")


def tts_pronunciation_pairs(value: Any) -> list[tuple[str, str]]:
    """``{written: spoken}`` or ``[{"name"/"word"/"written", "say"/"spoken"}]``,
    the shapes the campaign slot accepts, reduced to sayable pairs."""
    raw: list[tuple[Any, Any]] = []
    if isinstance(value, dict):
        raw = list(value.items())
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                raw.append((
                    item.get("name") or item.get("word") or item.get("written"),
                    item.get("say") or item.get("spoken"),
                ))
    pairs = []
    for written, spoken in raw:
        written_s, spoken_s = str(written or "").strip(), str(spoken or "").strip()
        if written_s and spoken_s and _SAYABLE.fullmatch(spoken_s):
            pairs.append((written_s, spoken_s))
    return pairs


def apply_pronunciations(text: str, pairs: Optional[Iterable[tuple[str, str]]]) -> str:
    """Swap each written form for its spoken form: whole words only,
    case-insensitive, longest first, replacement taken literally."""
    if not pairs or not text:
        return text
    lookup = {written.lower(): spoken for written, spoken in pairs}
    if not lookup:
        return text
    alternation = "|".join(re.escape(w) for w in sorted(lookup, key=len, reverse=True))
    pattern = re.compile(rf"(?<!\w)(?:{alternation})(?!\w)", re.IGNORECASE)
    return pattern.sub(lambda m: lookup[m.group(0).lower()], text)
