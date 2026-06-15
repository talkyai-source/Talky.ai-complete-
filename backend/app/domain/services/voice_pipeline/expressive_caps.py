"""TTS expressive-capability registry — the single source of truth for which
voice models can PERFORM inline audio tags ([laughs], [sighs], [pause], …).

Why this exists
---------------
Different engines have different expressive surfaces:

  * ElevenLabs **eleven_v3** performs inline bracket audio tags.
  * Everything else we run (ElevenLabs flash/turbo, Cartesia Sonic, Google,
    Deepgram Aura) does NOT — a bracket tag sent to them is just text and gets
    READ ALOUD ("open bracket, laughs"). That's the misfire we must prevent.

Universal text disfluencies ("um", "hmm", "ah") are plain words and are spoken
correctly by every engine — they are never touched here.

Two consumers, one truth:
  1. The prompt layer (turn_streamer) asks ``supports_audio_tags`` to decide
     whether to even TELL the model it may use tags.
  2. The output layer (``llm_guardrails.clean_response``) asks ``strip_audio_tags``
     to physically remove any bracket tags before audio for engines that can't
     perform them — belt-and-suspenders, so a disobedient LLM can never leak a
     tag as spoken text.

Adding a new tag-capable model later = add its model_id to ``_AUDIO_TAG_MODELS``
and both layers light up automatically.
"""
from __future__ import annotations

import re
from typing import Any

# Model ids (lowercased) whose engine actually PERFORMS inline bracket audio
# tags. Keep this tiny and explicit — everything not listed is treated as
# "cannot perform tags" and gets them stripped.
_AUDIO_TAG_MODELS = {"eleven_v3"}

# A short bracketed token like [laughs], [pause], [warmly], [laughs softly].
# Bounded length + no nested brackets/newlines so it only catches audio-tag-
# shaped tokens, not arbitrary text. Markdown links ([x](y)) are converted to
# plain text earlier in clean_response, so by the time this runs a bare [...]
# is an audio tag, not a link.
AUDIO_TAG_RE = re.compile(r"\[[^\[\]\n]{1,40}\]")


def supports_audio_tags(model_id: Any) -> bool:
    """True if this TTS model performs inline bracket audio tags."""
    return str(model_id or "").strip().lower() in _AUDIO_TAG_MODELS


def model_id_of(pipeline: Any) -> str:
    """Best-effort read of the live TTS model id off a pipeline/provider.
    Returns '' when unknown (treated as no tag support → tags stripped)."""
    tts = getattr(pipeline, "tts_provider", None)
    return str(getattr(tts, "_model_id", "") or "")


def strip_audio_tags(text: str) -> str:
    """Remove bracket audio tags from text. Leaves plain-word fillers
    (um/hmm/ah) untouched — they aren't bracketed. Safe to call on any text;
    no-op when there are no brackets. Whitespace tidy-up is left to the
    caller's existing collapse step."""
    if not text or "[" not in text:
        return text
    return AUDIO_TAG_RE.sub("", text)


# Stage-direction action words. When wrapped in *asterisks* or (parens) — the
# notation models reflexively fall back to — these must be removed ENTIRELY,
# on every voice: it's never a valid performed format (v3 uses [brackets]), and
# left alone the markdown pass would turn "*laughs*" into the spoken word
# "laughs". Bare words in normal speech ("she laughs a lot") are NOT touched —
# only the wrapped form is.
_ACTION_WORDS = (
    r"laugh(?:s|ing|ed)?|sigh(?:s|ing|ed)?|chuckl(?:e|es|ing)|giggl(?:e|es|ing)|"
    r"clears?\s+throat|cough(?:s|ing)?|gasp(?:s|ing)?|exhal(?:e|es|ing)|"
    r"inhal(?:e|es|ing)|breath(?:e|es|ing)|sniff(?:s|ing)?|whisper(?:s|ing)?|"
    r"pause(?:s|ing)?|smil(?:e|es|ing)|grin(?:s|ning)?|scoff(?:s|ing)?|"
    r"clearing\s+throat|throat\s+clear"
)
_STAGE_ASTERISK_RE = re.compile(r"\*[^*\n]*\b(?:" + _ACTION_WORDS + r")\b[^*\n]*\*", re.IGNORECASE)
_STAGE_PAREN_RE = re.compile(r"\([^)\n]*\b(?:" + _ACTION_WORDS + r")\b[^)\n]*\)", re.IGNORECASE)


def strip_stage_directions(text: str) -> str:
    """Remove *asterisk*- or (paren)-wrapped action words ("*laughs*",
    "(sighs softly)") on any voice — wrong format, and would otherwise be read
    aloud. Targeted: only wrapped action words, never bare words in a sentence."""
    if not text or ("*" not in text and "(" not in text):
        return text
    text = _STAGE_ASTERISK_RE.sub("", text)
    text = _STAGE_PAREN_RE.sub("", text)
    return text
