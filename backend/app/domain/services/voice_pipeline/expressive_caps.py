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
from dataclasses import dataclass
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


# ── Per-provider expressive profiles ────────────────────────────────────────
# The old binary model ("eleven_v3 = tags, everything else = strip ALL") threw
# away every other engine's NATIVE expressiveness. This makes it per-provider:
# each engine keeps ONLY the inline cues it actually performs, default-deny on
# everything else, so a cue can never be read aloud on an engine that can't do it.

@dataclass(frozen=True)
class ExpressiveProfile:
    name: str
    allow_all_tags: bool                        # True → keep every [bracket] tag
    allowed_tag_words: frozenset = frozenset()  # specific [word …] tags to KEEP


# eleven_v3 — the only ElevenLabs model that performs the full inline tag surface.
_PROFILE_ELEVEN_V3 = ExpressiveProfile("eleven_v3", allow_all_tags=True)

# Cartesia sonic-* — performs [laughter] inline (its one inline non-verbal).
# Emotion is an OUT-OF-BAND API field (generation_config), not inline, so emotion
# words stay stripped here (out-of-band emotion routing is a separate adapter job).
_PROFILE_CARTESIA = ExpressiveProfile(
    "cartesia", allow_all_tags=False,
    allowed_tag_words=frozenset({"laughter", "laugh", "laughs"}),
)

# Default — Deepgram Aura, ElevenLabs flash/turbo, Google Chirp, unknown: perform
# NO inline bracket tags → strip all (they would be read aloud otherwise).
_PROFILE_NONE = ExpressiveProfile("none", allow_all_tags=False)


def expressive_profile(model_id: Any) -> ExpressiveProfile:
    """Map a TTS model id to its expressive profile (default-deny)."""
    m = str(model_id or "").strip().lower()
    if m in _AUDIO_TAG_MODELS:          # {"eleven_v3"}
        return _PROFILE_ELEVEN_V3
    if m.startswith("sonic"):           # cartesia sonic / sonic-2 / sonic-3
        return _PROFILE_CARTESIA
    return _PROFILE_NONE


def strip_unsupported_audio_tags(text: str, model_id: Any) -> str:
    """Strip bracket audio tags the live engine can't perform, keeping ONLY the
    ones it does (default-deny). eleven_v3 keeps all; cartesia keeps [laughter];
    every other engine keeps none. A kept tag gets performed; a stripped one can
    never be read aloud. Plain-word fillers (um/hmm/ah) are never bracketed, so
    they are always left untouched."""
    if not text or "[" not in text:
        return text
    profile = expressive_profile(model_id)
    if profile.allow_all_tags:
        return text
    if not profile.allowed_tag_words:
        return AUDIO_TAG_RE.sub("", text)
    allowed = profile.allowed_tag_words

    def _keep(m: "re.Match[str]") -> str:
        inner = m.group(0)[1:-1].strip().lower()
        first = inner.split()[0] if inner else ""
        return m.group(0) if first in allowed else ""

    return AUDIO_TAG_RE.sub(_keep, text)
