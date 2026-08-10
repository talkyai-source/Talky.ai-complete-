"""LLM-authored escalation ladder for the "callee picked up and said nothing"
opening silence nudges.

WHY THIS EXISTS
===============
``turn_director.OPENING_PHRASES`` is a fixed 3-line script — "Hello?" / "Can
you hear me okay?" / "Is anyone there?" — spoken every single call, in that
exact wording, forever. A real person re-checking a silent line does not
recite a script: they say something short, then get a LITTLE more insistent
each time, in their own words —

    "Hello?"  ->  "Hello??"  ->  "Helloooo, are you there?"

— and no two callers escalate identically. This module authors that ladder
per call so it varies, while keeping every safety property the static ladder
already had (see the greeting-duplication invariant below).

NOTE ON THAT WORKED EXAMPLE: it illustrates the SHAPE — short, escalating,
never twice the same — for a human reader. The literal middle rung ("Hello??")
repeats the greeting word "Hello", which the invariant below forbids past rung
1 (a *fresh* greeting word starting a later rung reads as re-introducing
yourself, not as rising doubt about a still-silent line). The prompt template
below therefore uses a self-consistent example that never repeats a greeting
word after rung 1, so the model is never handed instructions that contradict
its own worked example.

WHY GENERATION IS NEVER ON THE NUDGE PATH
------------------------------------------
The nudge fires because the caller is ALREADY sitting in silence — that is
the single worst possible moment to add an LLM round-trip. This module is
therefore only ever called from ``telephony.prewarm`` during the pre-originate
/ ringing window (see ``prewarm._start_opening_ladder_generation``), as a
fire-and-forget background task. The result (or nothing, on any failure) is
stashed on the call session as ``_opening_ladder`` before the callee's phone
even finishes ringing. ``turn_director.choose_silence_phrase`` reads whatever
is on the session at nudge time — the generated ladder if it landed in time,
the static ``OPENING_PHRASES`` otherwise. Nothing in this module is awaited by
the audio pipeline.

FAIL-SOFT, ALWAYS
------------------
:func:`generate_opening_ladder` never raises. Flag off, no provider, timeout,
provider error, wrong rung count, banned content, a second greeting rung, a
non-escalating ladder — every one of those returns ``None``, and ``None``
means "the static ladder is used" (turn_director.py's default). A call is
never delayed or dropped over this feature: it isn't awaited on the answer
path at all (see prewarm.py), and even if it were, it is bounded by its own
short timeout.

THE GREETING-DUPLICATION INVARIANT (do not relax this)
--------------------------------------------------------
``tests/unit/test_greeting_duplication.py`` pins a real production bug: a
silent pickup once heard "Hello?" -> "Hi, can you hear me okay?" -> "Hi, it's
James from..." — three greetings back to back. The fix is "exactly ONE rung
may open with a greeting word, and it must be rung 1". :func:`validate_ladder`
enforces the identical rule on every LLM-generated ladder, using the same
greeting-word set, so this feature cannot reintroduce that bug through a new
door.

NO TENANT CONTEXT, ON PURPOSE
------------------------------
Unlike ``llm_opener.py`` (which needs the agent's name, the company, and
campaign context to write a real opener), this prompt is given NONE of that.
A "still checking the line" nudge has no business naming a company or pitching
anything — leaving that context out of the prompt is a stronger guarantee
against leakage than trying to regex it back out afterwards, and it also means
generation never waits on a campaign-row lookup.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Env knobs
# ---------------------------------------------------------------------------
#: Master switch. DEFAULT OFF — until an owner opts in, no LLM call is ever
#: made and every call speaks the existing static OPENING_PHRASES ladder,
#: byte-for-byte unchanged.
ENV_FLAG = "TELEPHONY_OPENING_LADDER_LLM_ENABLED"
#: Wall-clock budget for the whole generation (all attempts). This runs as a
#: background task during ring/warmup (see prewarm.py) — never awaited on the
#: answer path — but it is still bounded so a wedged provider can't leave a
#: task running indefinitely.
ENV_TIMEOUT_S = "TELEPHONY_OPENING_LADDER_TIMEOUT_S"
#: How many times we ask the model for a valid ladder before giving up and
#: leaving the static ladder in place.
ENV_ATTEMPTS = "TELEPHONY_OPENING_LADDER_ATTEMPTS"

_DEFAULT_TIMEOUT_S = 3.0
_DEFAULT_ATTEMPTS = 2
#: Matches audio_ingest.py's own default for VOICE_OPENING_MAX_NUDGES, used
#: only when that env var is unset or unparsable.
_DEFAULT_RUNG_COUNT = 3

#: Per-rung word budget. A nudge is a phone check, not a sentence — nothing
#: close to the 12-word opener budget belongs here.
MIN_RUNG_WORDS = 1
MAX_RUNG_WORDS = 6
#: Backstop for one pathologically long token; the word cap is the real guard.
MAX_RUNG_CHARS = 40
#: Higher than the conversation default on purpose: consecutive calls must not
#: escalate identically, which is exactly the "robotic script" complaint this
#: feature exists to fix.
LADDER_TEMPERATURE = 0.9
LADDER_MAX_TOKENS = 80


def opening_ladder_enabled() -> bool:
    """True only when the owner has explicitly switched this on."""
    return (os.getenv(ENV_FLAG, "") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _timeout_s() -> float:
    try:
        value = float(os.getenv(ENV_TIMEOUT_S, "") or _DEFAULT_TIMEOUT_S)
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_S
    return value if value > 0 else _DEFAULT_TIMEOUT_S


def _attempts() -> int:
    try:
        value = int(os.getenv(ENV_ATTEMPTS, "") or _DEFAULT_ATTEMPTS)
    except (TypeError, ValueError):
        return _DEFAULT_ATTEMPTS
    return min(max(value, 1), 3)


def rung_count() -> int:
    """How many rungs the ladder must have.

    Reads ``VOICE_OPENING_MAX_NUDGES`` — the SAME env var audio_ingest.py's
    silence monitor uses for its nudge cap — rather than hard-coding 3, so an
    operator who changes the nudge cap doesn't also have to remember to change
    this. A ladder shorter than the cap means the monitor's clamp-to-last-rung
    behaviour repeats the final rung for the extra nudges — the exact
    duplication failure mode ``test_greeting_duplication.py``'s collapsed-
    single-rung control case pins.
    """
    try:
        value = int(os.getenv("VOICE_OPENING_MAX_NUDGES", "") or _DEFAULT_RUNG_COUNT)
    except (TypeError, ValueError):
        return _DEFAULT_RUNG_COUNT
    return value if value > 0 else _DEFAULT_RUNG_COUNT


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
#: Characters with no business in a sentence a TTS engine will speak. Mirrors
#: llm_opener.py's identical guard.
_FORBIDDEN_CHARS = set('{}<>[]|\\*_#`"“”\n\r\t')

#: Same greeting-word set / shape the "multiple hi and hello" regression fix
#: uses (turn_director.py + test_greeting_duplication.py) — kept identical on
#: purpose so both ladders (static and generated) obey the same invariant.
_GREETING_RUNG = re.compile(
    r"^\s*(hi|hello|hey|hiya|good (morning|afternoon|evening))\b", re.IGNORECASE
)

#: A model volunteering any of these has stopped writing a "is anyone there"
#: check and started writing an introduction or a pitch — exactly what this
#: ladder must never become (it fires BEFORE the agent's real opener; see
#: turn_director.OPENING_PHRASES' docstring).
_BANNED_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bmy name is\b", "self-introduction, not a silence check"),
    (r"\bthis is\b", "self-introduction, not a silence check"),
    (r"\bi(?:'m| am) (?:with|from)\b", "self-introduction, not a silence check"),
    (r"\bcalling (?:from|on behalf|about|regarding)\b", "pitch/introduction language"),
    (r"\bhow (?:can|may) i help\b", "business question, not a silence check"),
    (r"\bwhat can i do for you\b", "business question, not a silence check"),
    (r"\bis (?:this|it) a (?:good|bad) time\b", "business/sales question"),
    (r"\binterested in\b", "pitch language"),
    (r"\boffer\b", "pitch language"),
    (r"\bproduct\b", "pitch language"),
    (r"\bservice\b", "pitch language"),
    (r"\bdeal\b", "pitch language"),
    (r"\bappointment\b", "pitch language"),
    (r"\bsign up\b", "pitch language"),
    (r"\bcold call\b", "pitch language"),
    (
        r"\b(?:ai|artificial intelligence|chatbot|bot|automated|robot|"
        r"virtual assistant|language model)\b",
        "mentions automation",
    ),
)
_BANNED_COMPILED = tuple((re.compile(p), why) for p, why in _BANNED_PATTERNS)


class LadderRejected(ValueError):
    """An LLM-generated opening ladder that must not be used. Carries why."""


def _spoken_words(text: str) -> list[str]:
    """The words a TTS engine actually voices. Mirrors llm_opener.spoken_words
    — a standalone em dash/ellipsis is a pause, not a word."""
    return [w for w in (text or "").split() if any(c.isalnum() for c in w)]


_LEADING_MARKER = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")


def _clean_rung(raw: str) -> str:
    """Strip list markers and wrapper quotes a model habitually adds.

    Deliberately does not repair content beyond that — a repaired rung is a
    rung nobody reviewed. Mirrors llm_opener.clean_candidate's philosophy.
    """
    text = (raw or "").strip()
    text = _LEADING_MARKER.sub("", text).strip()
    for _ in range(3):
        if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'‘“":
            text = text[1:-1].strip()
        elif text.startswith("“") and text.endswith("”"):
            text = text[1:-1].strip()
        else:
            break
    return text


def _parse_lines(raw: str) -> list[str]:
    """Split a raw completion into candidate rungs, one per non-empty line.

    Deliberately does NOT truncate to the expected count — a model that
    produced too many or too few lines is a validation failure
    (:func:`validate_ladder` checks the count), not something to silently
    paper over by slicing.
    """
    return [c for c in (_clean_rung(ln) for ln in (raw or "").splitlines()) if c]


def validate_ladder(rungs: list[str], *, expected_count: int) -> list[str]:
    """Return the ladder if it is safe to use; raise :class:`LadderRejected`.

    This is the whole safety story of the feature. Callers must treat any
    exception as "keep the static ladder" — never as a reason to use the
    ladder anyway.
    """
    if len(rungs) != expected_count:
        raise LadderRejected(
            f"{len(rungs)} rungs, expected exactly {expected_count}"
        )

    cleaned: list[str] = []
    for i, raw in enumerate(rungs):
        text = (raw or "").strip()
        if not text:
            raise LadderRejected(f"rung {i + 1} is empty")

        bad_chars = _FORBIDDEN_CHARS.intersection(text)
        if bad_chars:
            raise LadderRejected(
                f"rung {i + 1} has non-speakable characters: {sorted(bad_chars)!r}"
            )

        if len(text) > MAX_RUNG_CHARS:
            raise LadderRejected(
                f"rung {i + 1} is {len(text)} chars > {MAX_RUNG_CHARS}"
            )

        words = _spoken_words(text)
        if not (MIN_RUNG_WORDS <= len(words) <= MAX_RUNG_WORDS):
            raise LadderRejected(
                f"rung {i + 1} has {len(words)} words, must be "
                f"{MIN_RUNG_WORDS}-{MAX_RUNG_WORDS}"
            )

        low = text.lower()
        for pattern, why in _BANNED_COMPILED:
            if pattern.search(low):
                raise LadderRejected(f"rung {i + 1} {why}: {pattern.pattern!r}")

        cleaned.append(text)

    # The greeting-duplication invariant (see module docstring): exactly one
    # rung may open with a greeting word, and it must be first.
    greeting_idxs = [i for i, r in enumerate(cleaned) if _GREETING_RUNG.match(r)]
    if greeting_idxs != [0]:
        raise LadderRejected(
            f"greeting rungs at {greeting_idxs}, expected exactly [0] — "
            "see test_greeting_duplication.py"
        )

    # Must actually vary — a model that repeats itself defeats the entire
    # point of replacing a fixed script with one.
    lowered = [r.lower() for r in cleaned]
    if len(set(lowered)) != len(lowered):
        raise LadderRejected("duplicate rungs — the whole point is that they vary")

    # Escalation proxy: character length must never shrink rung-to-rung, and
    # the last rung must be strictly longer than the first. This is a cheap,
    # robust stand-in for "sounds more insistent" that needs no semantic
    # judgement at validation time — the worked example this feature is built
    # around ("Hello?" -> "Hello??" -> "Helloooo, are you there?") is
    # monotonically longer at every single step.
    for i in range(1, len(cleaned)):
        if len(cleaned[i]) < len(cleaned[i - 1]):
            raise LadderRejected(
                f"rung {i + 1} is shorter than rung {i} — a ladder must "
                "escalate, never shrink"
            )
    if len(cleaned[-1]) <= len(cleaned[0]):
        raise LadderRejected(
            "last rung is no more insistent than the first — ladder never escalates"
        )

    # Last line of defence, same as llm_opener.py: this ladder is built from
    # NO tenant/campaign context at all, so this is pure defense-in-depth
    # against a compromised or misbehaving provider rather than an expected
    # path.
    try:
        from app.services.scripts.prompts.prompt_safety import scan_for_injection

        for i, text in enumerate(cleaned):
            if scan_for_injection(text):
                raise LadderRejected(f"rung {i + 1} output is instruction-shaped")
    except LadderRejected:
        raise
    except Exception:  # pragma: no cover - defensive; scanner must never block
        pass

    return cleaned


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
_SYSTEM_TEMPLATE = """\
You write what a real person says when they place a phone call, the line \
connects, and the other person says NOTHING back. This is an ordinary moment \
— not a wrong number, not dead air forever, just someone who has not spoken \
yet. A real caller does not read a script; they say something short, then \
get a little more insistent each time the silence continues.

Write EXACTLY {n} short lines and nothing else, one per line, in order. Line \
1 is the very first thing you say into a silent line. Each following line is \
what you say a few seconds later if it is STILL silent — a natural \
escalation of a person's rising uncertainty.

One example of the SHAPE (do not copy it — invent your own words every time):
    "Hello?"
    "Anyone there?"
    "Is someone on the line?"

HARD RULES for every single line:
  * 1 to 6 spoken words. Short — this is a phone check, not a sentence.
  * ONLY line 1 may start with a greeting word (hello / hi / hey / hiya). \
Every later line must NOT start with a greeting word — it should sound like \
rising doubt, not a fresh hello.
  * NEVER say a name, a company, or anything about business, products, \
prices, deals, or why you are calling. This is purely "is anyone on this \
line" — nothing else.
  * NEVER ask a business question ("how can I help you", "is this a good \
time", "what's this regarding"). Only check that the line is live.
  * Each line must read as MORE urgent or uncertain than the one before it — \
never flatter or shorter than the previous line.
  * Plain spoken English. No numbering, no bullets, no quotation marks, no \
markdown, no labels, no emoji.

Reply with exactly {n} lines and nothing else."""

_RETRY_SUFFIX = (
    "\n\nYour previous attempt was REJECTED: {reason}\n"
    "Write a different {n}-line ladder that fixes exactly that."
)


def build_ladder_prompt(n: int, *, rejection: Optional[str] = None) -> tuple[str, str]:
    """Return ``(system_prompt, user_message)``. No tenant context is ever
    included — see the module docstring for why that is deliberate."""
    system = _SYSTEM_TEMPLATE.format(n=n)
    if rejection:
        system += _RETRY_SUFFIX.format(reason=rejection, n=n)
    return system, "Write the ladder now."


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------
async def _collect_stream(llm_provider, system_prompt: str, user_prompt: str) -> str:
    """Drain one streamed completion into a string, closing the generator.
    Mirrors llm_opener._collect_stream exactly."""
    from app.domain.models.conversation import Message, MessageRole

    stream = llm_provider.stream_chat(
        [Message(role=MessageRole.USER, content=user_prompt)],
        system_prompt=system_prompt,
        temperature=LADDER_TEMPERATURE,
        max_tokens=LADDER_MAX_TOKENS,
    )
    parts: list[str] = []
    try:
        async for token in stream:
            if token:
                parts.append(token)
    finally:
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except (Exception, asyncio.CancelledError):
                # See llm_opener._collect_stream's identical comment: the
                # common way here is a wait_for timeout cancelling us, and a
                # second exception from cleanup would mask nothing useful.
                pass
    return "".join(parts)


async def generate_opening_ladder(
    *,
    llm_provider,
    rungs: Optional[int] = None,
    call_id: Optional[str] = None,
) -> Optional[list[str]]:
    """An LLM-authored, VALIDATED opening ladder for this call — or ``None``.

    ``None`` is the normal, safe answer and means "use the static
    OPENING_PHRASES ladder". Returned for: the flag being off, no provider, a
    timeout, a provider error, and any output that fails validation.

    This function never raises (``CancelledError`` aside — a cooperative
    cancellation of the background task that hosts this call must still
    propagate).
    """
    tag = (call_id or "-")[:12]
    if not opening_ladder_enabled():
        return None
    if llm_provider is None:
        return None

    n = rungs if rungs is not None else rung_count()
    if n < 1:
        return None

    deadline = _timeout_s()
    loop = asyncio.get_running_loop()
    started = loop.time()
    rejection: Optional[str] = None

    for attempt in range(1, _attempts() + 1):
        remaining = deadline - (loop.time() - started)
        if remaining <= 0.05:
            logger.info(
                "opening_ladder_timeout call=%s after=%d attempt(s) — "
                "using static ladder",
                tag, attempt - 1,
            )
            return None

        system_prompt, user_prompt = build_ladder_prompt(n, rejection=rejection)
        try:
            raw = await asyncio.wait_for(
                _collect_stream(llm_provider, system_prompt, user_prompt),
                timeout=remaining,
            )
        except asyncio.TimeoutError:
            logger.info(
                "opening_ladder_timeout call=%s attempt=%d budget=%.1fs — "
                "using static ladder",
                tag, attempt, deadline,
            )
            return None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.info(
                "opening_ladder_provider_failed call=%s attempt=%d err=%r — "
                "using static ladder",
                tag, attempt, exc,
            )
            return None

        try:
            ladder = validate_ladder(_parse_lines(raw), expected_count=n)
        except LadderRejected as exc:
            rejection = str(exc)
            logger.info(
                "opening_ladder_rejected call=%s attempt=%d reason=%s raw=%r",
                tag, attempt, rejection, (raw or "")[:200],
            )
            continue
        except Exception as exc:  # pragma: no cover - defensive
            logger.info(
                "opening_ladder_validate_failed call=%s err=%r — using static ladder",
                tag, exc,
            )
            return None

        logger.info(
            "opening_ladder_ready call=%s rungs=%r elapsed_ms=%.0f",
            tag, ladder, (loop.time() - started) * 1000.0,
        )
        return ladder

    logger.info(
        "opening_ladder_exhausted call=%s attempts=%d last_reason=%s — "
        "using static ladder",
        tag, _attempts(), rejection,
    )
    return None
