"""Accent-aware filler / discourse-marker guidance for the voice agent.

Why this exists
---------------
Fillers and discourse markers are accent-specific. The single most documented,
*audible* difference is how speakers spell/voice the filled pause:

  * British English speakers say **"er"** and **"erm"** (Tottie 2011, Cambridge).
  * American English speakers say **"uh"** and **"um"**.

Because our fillers reach the TTS engine as plain text (the LLM writes them and
the voice speaks them verbatim), choosing the right spelling — and the right
discourse markers ("sort of"/"I suppose" vs "like"/"for sure") — makes the
agent sound native to the selected voice's accent instead of generically
American.

How it's wired
--------------
``resolve_accent(voice_id)`` maps the selected voice to a normalized accent by
(1) reading a locale embedded in the id (e.g. ``en-GB-...``), then (2) looking
the id up in the static provider catalogs (Cartesia / Google / Deepgram), then
(3) the cached ElevenLabs catalog (cache-only, never a network call on the hot
path). ``accent_filler_block(accent)`` returns a short prompt block appended to
the system prompt for that turn (see voice_pipeline/turn_streamer.py). Unknown
or "Global" voices return ``"neutral"`` and get no override — the generic
guardrails (already American-flavoured) apply, which is the right default for
"general" voices.

Sources:
  - Tottie, "Uh and Um as sociolinguistic markers in British English" (2011)
  - Cambridge, "From pause to word: uh, um and er in written American English"
"""
from __future__ import annotations

import asyncio
import re
from typing import Optional

# --- Normalized accent keys -------------------------------------------------
AMERICAN = "american"
BRITISH = "british"
AUSTRALIAN = "australian"
IRISH = "irish"
INDIAN = "indian"
NEUTRAL = "neutral"

# Map raw accent/locale strings (from voice metadata) onto a normalized key.
# Keys are matched as lowercased substrings, longest first.
_ACCENT_ALIASES: dict[str, str] = {
    "received pronunciation": BRITISH,
    "great britain": BRITISH,
    "england": BRITISH,
    "english (uk)": BRITISH,
    "british": BRITISH,
    "en-gb": BRITISH,
    "scottish": BRITISH,   # nearest supported block
    "welsh": BRITISH,
    "north american": AMERICAN,
    "american": AMERICAN,
    "united states": AMERICAN,
    "en-us": AMERICAN,
    "canadian": AMERICAN,  # nearest supported block
    "en-ca": AMERICAN,
    "australian": AUSTRALIAN,
    "en-au": AUSTRALIAN,
    "new zealand": AUSTRALIAN,  # nearest supported block
    "en-nz": AUSTRALIAN,
    "irish": IRISH,
    "ireland": IRISH,
    "en-ie": IRISH,
    "indian": INDIAN,
    "en-in": INDIAN,
}

# Locale region code (from "en-XX" in a voice id) -> normalized accent.
_REGION_TO_ACCENT: dict[str, str] = {
    "GB": BRITISH, "UK": BRITISH,
    "US": AMERICAN, "CA": AMERICAN,
    "AU": AUSTRALIAN, "NZ": AUSTRALIAN,
    "IE": IRISH,
    "IN": INDIAN,
}

_LOCALE_RE = re.compile(r"\ben-([A-Za-z]{2})\b", re.IGNORECASE)


def normalize_accent(raw: Optional[str]) -> str:
    """Map a raw accent/locale label to a normalized accent key.

    "British"/"Received Pronunciation"/"en-GB" -> ``BRITISH``; unknown or
    "Global"/"" -> ``NEUTRAL``."""
    if not raw:
        return NEUTRAL
    val = raw.strip().lower()
    if not val or val == "global":
        return NEUTRAL
    # Longest alias first so "en-gb" wins over a bare "en".
    for alias in sorted(_ACCENT_ALIASES, key=len, reverse=True):
        if alias in val:
            return _ACCENT_ALIASES[alias]
    return NEUTRAL


def _accent_from_locale_in_id(voice_id: str) -> Optional[str]:
    """Extract accent from an "en-XX" locale embedded in a voice id
    (Google Chirp ids like ``en-GB-Chirp3-HD-...``)."""
    m = _LOCALE_RE.search(voice_id or "")
    if not m:
        return None
    return _REGION_TO_ACCENT.get(m.group(1).upper())


def _accent_from_catalogs(voice_id: str) -> Optional[str]:
    """Look the voice id up across provider catalogs and read its accent.

    Static catalogs (Cartesia / Google / Deepgram) are imported lazily to avoid
    import cycles; the ElevenLabs catalog is read cache-only (never fetches)."""
    if not voice_id:
        return None
    try:
        from app.domain.models.ai_config import (
            CARTESIA_VOICES,
            GOOGLE_CHIRP3_VOICES,
            DEEPGRAM_AURA2_VOICES,
        )
        for catalog in (CARTESIA_VOICES, GOOGLE_CHIRP3_VOICES, DEEPGRAM_AURA2_VOICES):
            for v in catalog:
                if v.id == voice_id:
                    acc = normalize_accent(getattr(v, "accent", None))
                    if acc != NEUTRAL:
                        return acc
                    return normalize_accent(getattr(v, "language", None))
    except Exception:
        pass

    try:
        from app.infrastructure.tts.elevenlabs_catalog import get_cached_elevenlabs_voices
        cached = get_cached_elevenlabs_voices()
        if cached:
            for v in cached:
                if v.id == voice_id:
                    return normalize_accent(getattr(v, "accent", None))
    except Exception:
        pass
    return None


def resolve_accent(voice_id: Optional[str]) -> str:
    """Resolve the selected voice to a normalized accent key (SYNC, hot-path
    safe — never makes a network call). Falls back to ``NEUTRAL`` when the
    accent can't be determined (safe default — generic guardrails apply).

    For ElevenLabs voices this depends on the voice catalog already being
    cached; when it might be cold (e.g. call setup), prefer
    :func:`resolve_accent_async`, which can warm it."""
    if not voice_id:
        return NEUTRAL
    # 1) locale baked into the id (cheap, no imports)
    acc = _accent_from_locale_in_id(voice_id)
    if acc:
        return acc
    # 2) provider catalogs (static + EL cache, cache-only)
    acc = _accent_from_catalogs(voice_id)
    if acc:
        return acc
    return NEUTRAL


async def resolve_accent_async(
    voice_id: Optional[str], provider: Optional[str] = None
) -> str:
    """Like :func:`resolve_accent`, but may warm the ElevenLabs voice catalog
    (one allowed network call) so EL accents resolve reliably even on a cold
    cache. Used at call pre-warm. Always best-effort — returns ``NEUTRAL`` on
    any failure."""
    if not voice_id:
        return NEUTRAL
    acc = resolve_accent(voice_id)  # cheap paths + EL cache if already warm
    if acc != NEUTRAL:
        return acc
    # EL voice ids aren't locale-coded and aren't in the static catalogs, so a
    # cold EL cache is the one case that needs a fetch. Gate on provider to
    # avoid pointless EL calls for other providers.
    if (provider or "").lower() in ("elevenlabs", "eleven"):
        try:
            from app.infrastructure.tts.elevenlabs_catalog import (
                get_elevenlabs_voices_for_current_key,
            )
            await asyncio.wait_for(get_elevenlabs_voices_for_current_key(), timeout=4.0)
            acc = _accent_from_catalogs(voice_id)
            if acc:
                return acc
        except Exception:
            pass
    return NEUTRAL


# --- Prompt blocks per accent ----------------------------------------------
# Each block is appended AFTER the generic guardrails, so it overrides the
# generic (American-flavoured) filler guidance for non-American accents. Kept
# short to limit per-turn tokens.

_AMERICAN_BLOCK = """\
DIALECT — write your ENTIRE reply in natural American English (vocabulary,
spelling, and phrasing — not just the fillers). Keep it natural, never a caricature.
- Hesitation sounds: "um", "uh", "hmm".
- Discourse markers: "like", "you know", "I mean", "so", "okay", "right",
  "I guess", "kind of", "for sure", "totally".
- Reactions: "oh", "ah", "yeah", "gotcha", "no problem".
- Vocabulary: "cell"/"cell phone", "store", "vacation", "awesome"/"great",
  "reach out", "schedule", "zip code", "math".
- Spelling: American — "color", "realize", "center", "canceled"."""

_BRITISH_BLOCK = """\
DIALECT — write your ENTIRE reply in natural British English (vocabulary,
spelling, and phrasing — not just the fillers). Keep it natural, never a caricature.
- Hesitation sounds: write "er" and "erm" (NEVER the American "um"/"uh").
- Discourse markers: "well", "you know", "I mean", "sort of", "I suppose",
  "to be fair", "mind you", "right", "fair enough", "quite".
- Reactions: "oh", "ah", "right", "oh I see"; soften with "no worries",
  "lovely", "brilliant"; "cheers" / "ta" for thanks; "cheers" as a sign-off.
- Vocabulary: "mobile" (not cell), "shop" (not store), "holiday" (not vacation),
  "ring"/"give you a ring" (call), "get in touch" (not reach out), "sort it out",
  "keen", "fortnight", "postcode" (not zip), "maths", "have a quick chat".
- Spelling: British — "colour", "favour", "realise", "organise", "centre",
  "programme", "cancelled".
- Avoid Americanisms: "awesome", "for sure", "gotten", "you guys", "vacation",
  "cell phone", "zip code", "reach out"."""

_AUSTRALIAN_BLOCK = """\
DIALECT — write your ENTIRE reply in natural Australian English (vocabulary,
spelling, and phrasing — not just the fillers). Relaxed and warm, never stiff
or a caricature.
- Hesitation sounds: "um", "ah".
- Discourse markers: "yeah nah" / "nah yeah", "no worries", "no dramas",
  "reckon", "fair enough", "too easy", "heaps".
- Reactions: "oh", "ah", "yeah righto", "good on ya"; "cheers" for thanks.
- Vocabulary: "mobile", "arvo" (afternoon), "heaps" (a lot), "keen",
  "give you a buzz" (call), "sort it out"; "mate" sparingly and professionally.
- Spelling: British-style — "colour", "realise", "centre"."""

_IRISH_BLOCK = """\
DIALECT — write your ENTIRE reply in natural Irish English / Hiberno-English
(vocabulary, spelling, and phrasing — not just the fillers). Warm and
easy-going, never a caricature.
- Hesitation sounds: "em", "erm", "ah".
- Discourse markers: "sure", "grand", "you know", "I mean", "now",
  "to be fair", "no bother", "fair play"; sentence-final "like".
- Reactions: "ah", "sure look", "grand so".
- Vocabulary: "grand" (fine/good), "no bother" (no problem), "sound"
  (nice/reliable), "give you a ring" (call), "sort it out", "brilliant".
- Spelling: British-style — "colour", "realise", "centre"."""

_INDIAN_BLOCK = """\
DIALECT — write your ENTIRE reply in natural Indian English (vocabulary,
spelling, and phrasing — not just the fillers). Polite and professional,
never a caricature.
- Hesitation sounds: "um", "hmm".
- Discourse markers: "actually", "you know", "I mean", "see", "basically",
  "the thing is", "no?" / "na" as a tag.
- Reactions: "oh", "ah", "got it".
- Vocabulary: "kindly", "revert" (reply back), "prepone" (move earlier),
  "your good name", "do let me know", "only" for emphasis ("today only").
- Spelling: British-style — "colour", "realise", "centre"."""

_BLOCKS: dict[str, str] = {
    AMERICAN: _AMERICAN_BLOCK,
    BRITISH: _BRITISH_BLOCK,
    AUSTRALIAN: _AUSTRALIAN_BLOCK,
    IRISH: _IRISH_BLOCK,
    INDIAN: _INDIAN_BLOCK,
}


# Short "thinking" phrases played when a reply is slow to produce its first
# audio, so the caller hears a natural hesitation instead of dead air. Matched
# to the accent so they reinforce the dialect. Kept very short (<~1s of speech).
_THINKING_FILLERS: dict[str, tuple] = {
    AMERICAN: ("Let me see...", "Sure, one sec...", "Okay, let me check...", "Mm, let me see..."),
    BRITISH: ("Right, let me see...", "Erm, one sec...", "Let me have a look...", "Okay, just a moment..."),
    AUSTRALIAN: ("Yeah, let me check...", "No worries, one sec...", "Righto, let me see..."),
    IRISH: ("Right, let me see now...", "Em, one sec...", "Let me have a look there..."),
    INDIAN: ("Okay, let me check...", "One moment, please...", "Let me see..."),
    NEUTRAL: ("Let me see...", "One moment...", "Okay, let me check..."),
}


def thinking_filler(accent: str) -> str:
    """A short accent-matched 'thinking' phrase to cover a slow first-audio gap.
    Random within the accent so it doesn't sound canned on repeat."""
    import random
    pool = _THINKING_FILLERS.get(accent or NEUTRAL) or _THINKING_FILLERS[NEUTRAL]
    return random.choice(pool)


def accent_filler_block(accent: str) -> str:
    """Return the prompt block for a normalized accent key, or "" for neutral/
    unknown (no override — generic guardrails apply)."""
    return _BLOCKS.get(accent or NEUTRAL, "")


def filler_block_for_voice(voice_id: Optional[str]) -> str:
    """Convenience: resolve a voice id straight to its filler prompt block."""
    return accent_filler_block(resolve_accent(voice_id))
