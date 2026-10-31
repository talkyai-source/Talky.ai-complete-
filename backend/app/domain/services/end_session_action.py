"""Shared LLM-routed end-session action helpers."""
from __future__ import annotations

import json
import logging
import re
from typing import Optional
from app.domain.services.caller_assertions import (
    assertion_matches, continuation_after,
)

logger = logging.getLogger(__name__)

END_SESSION_ACTION = "end_session"
LEGACY_ASK_AI_END_SESSION_ACTION = "end_ask_ai_session"

# A model's completion label or number of turns is not caller authorization.

# Caller utterances that genuinely mean "I'm ending this." Tight on purpose —
# we'd rather keep a call alive on a false-negative than hang up on a phantom.
_CALLER_END_INTENT = re.compile(
    r"""\b(
        bye | good\s?bye | good\s?night | see\s+(?:ya|you) | take\s+care |
        talk\s+(?:to\s+you\s+)?later | catch\s+you\s+later | gotta\s+go |
        got\s+to\s+go | have\s+to\s+go | need\s+to\s+go | i'?m\s+done |
        we'?re\s+done | that'?s\s+(?:all|it) | that\s+is\s+all | nothing\s+else |
        no\s+thank(?:s|\s+you) | not\s+interested | hang\s+up |
        end\s+(?:this\s+|the\s+)?call |
        leave\s+me\s+alone | lose\s+my\s+number
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)


def previous_assistant_turn(history) -> str:
    """Assistant turn before the latest caller, never this turn's new farewell."""
    items = list(history or ())

    def role(message):
        value = getattr(message, "role", "")
        return getattr(value, "value", value)

    user_index = next((i for i in range(len(items) - 1, -1, -1) if role(items[i]) == "user"), len(items))
    return next((str(getattr(message, "content", "") or "")
                 for message in reversed(items[:user_index]) if role(message) == "assistant"), "")


# These are questions whose negative answer ends the conversation, not business-topic labels.
# Anything unrecognised stays a question about its own subject, so a short
# negative reply cannot let a model hang up on an declined optional offer.
_NEGATIVE_REPLY_ENDS_CALL = re.compile(
    r"(?:is there |do you need |can (?:i|we) help (?:you )?with )?anything else"
    r"(?: (?:i|we) can (?:help(?: you)?(?: with)?|do(?: for you)?)| you (?:need|want to (?:ask|discuss)))?"
    r"(?: today| before (?:we|you) go)?|"
    r"(?:do you have|have you got|got) (?:a|one|a quick) (?:minute|moment)(?: to (?:talk|chat))?|"
    r"is (?:now|this) (?:a )?good time(?: to (?:talk|chat))?",
    re.I,
)


def _reply_scoped_to_question(previous_assistant_text: Optional[str]) -> bool:
    """A short refusal answers the last question, including unpunctuated speech."""
    text = str(previous_assistant_text or "").strip()
    questions = re.findall(r"[^.!?]+\?", text)
    last = questions[-1] if questions else re.split(r"[.!]", text.rstrip(".!"))[-1]
    last = " ".join(last.strip(" \t\r\n?\"'“”").lower().split())
    if not last:
        return False
    is_question = bool(questions) or bool(re.match(
        r"(?:who|what|when|where|why|how|which|would|could|can|do|does|did|are|is|have|has|got|anything else)\b", last))
    return is_question and not _NEGATIVE_REPLY_ENDS_CALL.fullmatch(last)


def _call_end_position(text: Optional[str], pattern: re.Pattern, previous_assistant_text: Optional[str] = None) -> int:
    """A topic-qualified refusal/completion is not permission to end the call.

    Match grammar, not a list of business topics: "done giving my number" and
    "not interested in email" concern that activity. Explicit goodbye and DNC
    remain independent authorizations, including later in the same utterance.
    "For now" and "with this call" retain their ordinary whole-call meaning.
    """
    accepted = []
    for match in assertion_matches(text, pattern):
        phrase = match[0].lower()
        soft = re.fullmatch(r"(?:no\s+thank(?:s|\s+you)|not\s+interested|"
            r"(?:i'?m|we'?re)\s+done|that'?s\s+(?:all|it)|that\s+is\s+all|nothing\s+else)", phrase)
        tail = str(text or "")[match.end():]
        scoped = re.match(r"\s+(?:in|to|for|about|with|[a-z]+ing)\b", tail, re.I)
        whole_call = re.match(r"\s+(?:for\s+now\b|(?:in|to|for|with|about)\s+"
            r"(?:(?:this|the|our)\s+)?(?:call|conversation)\b)", tail, re.I)
        explicit_whole_call = whole_call and not re.match(r"\s+for\s+now\b", tail, re.I)
        if soft and ((scoped and not whole_call) or
                     (_reply_scoped_to_question(previous_assistant_text) and not explicit_whole_call)):
            continue
        accepted.append(match.start())
    return accepted[-1] if accepted else -1


def caller_signaled_end(text: Optional[str], *, previous_assistant_text: Optional[str] = None) -> bool:
    """True if the caller's own words clearly signal ending the call."""
    from app.domain.services.voice_pipeline.identity_disposition import dnc_assertion_position
    position = max(_call_end_position(text, _CALLER_END_INTENT, previous_assistant_text), dnc_assertion_position(text))
    return position >= 0 and not continuation_after(text, position)


_CURRENT_DECLINE = re.compile(
    r"\b(?:not\s+interested|no\s+thanks?|don't\s+want\s+(?:this|that|it)|"
    r"do\s+not\s+want\s+(?:this|that|it)|not\s+for\s+(?:me|us))\b", re.I)


def repeated_decline_allows_end(text: Optional[str], declined_count: int, *, previous_assistant_text: Optional[str] = None) -> bool:
    """Historical objections cannot authorize a close on a new help request."""
    if not isinstance(declined_count, int) or isinstance(declined_count, bool) or declined_count < 2:
        return False
    if _reply_scoped_to_question(previous_assistant_text):
        return False
    position = _call_end_position(text, _CURRENT_DECLINE)
    return position >= 0 and not continuation_after(text, position)


_FILLER = frozenset("please um uh erm just look so ok okay well and".split())
_DETERMINER = frozenset("the a an your my our this that".split())


def _quote_tokens(text: object) -> list[tuple[str, int]]:
    """Normalised words with the end offset of each in the original text.

    Case, apostrophes and "do not"/"don't" are ignored because STT varies
    them; filler is dropped and determiners are interchangeable, so "take me
    off the list" matches "take me off your list".
    """
    raw = str(text or "").replace("\u2019", "'")
    out: list[tuple[str, int]] = []
    for match in re.finditer(r"[A-Za-z0-9']+", raw):
        word = match.group(0).lower().replace("'", "")
        if word in _FILLER:
            continue
        if word == "not" and out and out[-1][0] == "do":
            out[-1] = ("dont", match.end())
            continue
        out.append(("<det>" if word in _DETERMINER else word, match.end()))
    return out


def _quote_end(quote: object, caller_text: Optional[str]) -> int:
    """End offset of the quote inside the caller's words, or -1."""
    words = [w for w, _ in _quote_tokens(quote)]
    said = _quote_tokens(caller_text)
    n = len(words)
    if n < 2:
        return -1
    for i in range(len(said) - n + 1):
        if [w for w, _ in said[i:i + n]] == words:
            return said[i + n - 1][1]
    return -1


def caller_quote_verified(quote: object, caller_text: Optional[str]) -> bool:
    """The model's quote is a run of at least two words the caller really said."""
    return _quote_end(quote, caller_text) >= 0


# What the quoted words must express for a permanent suppression: a refusal of
# future contact. The model decides; this only checks its quote is such a
# refusal, so "not interested", "I'm busy" or "call me tomorrow" cannot become
# a do-not-call. "Don't call me tomorrow" is a scheduling preference.
_REMOVAL = re.compile(r"\b(?:remove|removed|delete|unsubscribe|opt out|take (?:me|us|<det> number|my number) off)\b")
_REFUSAL = re.compile(r"\b(?:stop|never|dont|no more|quit|cease|not to)\b")
_CONTACT = re.compile(
    r"\b(?:call|calls|calling|called|ring|rings|ringing|rang|phone|phoning|phoned|contact|"
    r"contacting|number|list|message|messages|messaging|text|texts|texting|bother|bothering)\b")
_TEMPORAL = re.compile(
    r"\b(?:tomorrow|today|tonight|now|later|moment|until|week|morning|afternoon|evening|"
    r"weekend|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b")


# A later "wait", "actually" or "never mind" walks the opt-out back. A later
# question ("why do you keep calling?") does not.
_WALK_BACK = re.compile(r"\b(?:wait|hold on|hang on|actually|never ?mind|i mean|just kidding|on second thought)\b", re.I)


def _refuses_contact(quote: object) -> bool:
    text = " ".join(w for w, _ in _quote_tokens(quote))
    if _TEMPORAL.search(text):
        return False
    return bool(_REMOVAL.search(text) or (_REFUSAL.search(text) and _CONTACT.search(text)))


def verified_opt_out(action: Optional[dict], caller_text: Optional[str]) -> bool:
    """A caller opt-out backed by the caller's own words.

    Either a directed opt-out phrase (the deterministic floor), or the model's
    do_not_call judgement carrying a quote that (a) the caller really said,
    (b) refuses future contact, and (c) the caller did not walk back ("wait, actually...")
    later in the same turn. The model understands paraphrases the phrase list cannot
    ("never bother this number again"); the checks stop it turning a decline
    or a scheduling preference into a permanent suppression.
    """
    from app.domain.services.voice_pipeline.identity_disposition import contains_dnc

    if contains_dnc(caller_text):
        return True
    if not (action and action.get("do_not_call")):
        return False
    quote = action.get("opt_out_quote")
    end = _quote_end(quote, caller_text)
    return end >= 0 and _refuses_contact(quote) and not _WALK_BACK.search(str(caller_text or "")[end:])


def should_honor_end_session(
    action: Optional[dict],
    last_user_text: Optional[str],
    user_turn_count: int,
    declined_count: int = 0,
    *,
    previous_assistant_text: Optional[str] = None,
) -> bool:
    """Decide whether to actually hang up on an LLM end-session action, or treat
    it as a phantom goodbye and keep the call going.

    Honor the caller's end intent, a verified opt-out, or the recorded
    repeated-decline policy. Model-only completion labels and conversation
    length are not evidence; a do_not_call label counts only with a verified
    quote of the caller (verified_opt_out).
    """
    if not action:
        return False
    if caller_signaled_end(last_user_text, previous_assistant_text=previous_assistant_text):
        return True
    if action.get("do_not_call") and verified_opt_out(action, last_user_text):
        return True
    if repeated_decline_allows_end(last_user_text, declined_count, previous_assistant_text=previous_assistant_text):
        return True
    return False

END_SESSION_REASONS = {
    "user_goodbye",
    "user_done",
    "conversation_complete",
}

DEFAULT_FAREWELL = "Goodbye, take care."


def build_end_session_tool_instructions(*, action_name: str = END_SESSION_ACTION) -> str:
    return (
        f"Internal action available: {action_name}.\n"
        "If the user is clearly ending the interaction, saying goodbye, saying they "
        "are done, asking to hang up, or indicating the conversation is complete, "
        "respond with exactly this JSON and no spoken text outside JSON:\n"
        f'{{"action":"{action_name}","reason":"user_goodbye","farewell":"{DEFAULT_FAREWELL}"}}\n'
        "Use reason user_goodbye for farewells, user_done when the user says they "
        "are done with the conversation, and conversation_complete only when the caller "
        "confirms the whole conversation is finished. A completed task or thanks alone "
        "does not mean the caller wants to end the call. "
        "Set farewell to one short natural sentence that matches the user's goodbye "
        "style: if they say goodbye, say goodbye; if they say see you, say see you; "
        "if they say take care, answer in that same friendly closing style. "
        "If — and only if — the user asks NOT to be contacted again, in any wording or "
        "language (\"stop calling me\", \"never ring this number again\", \"take me off your list\"), "
        'add "do_not_call":true and "opt_out_quote" with their exact words to the same JSON, '
        "and set the farewell to a brief, respectful confirmation that they won't be "
        "contacted again, e.g. "
        f'{{"action":"{action_name}","reason":"user_done","farewell":"Understood — I\'ll '
        'remove you from our list. Sorry to bother you, take care.","do_not_call":true,'
        '"opt_out_quote":"never ring this number again"}}. '
        "Do NOT set do_not_call for ordinary goodbyes, objections, or \"I\'m busy right "
        "now\" — only a genuine request never to be called again. "
        "For all other messages, answer normally. Do not use this action when the "
        "user is asking a question about ending, goodbye handling, calls, or sessions."
    )


def _normalise_action_name(value: object) -> Optional[str]:
    """Match an action name the model got slightly wrong.

    Observed in production: ``endsession`` for ``end_session`` and
    ``conversationcomplete`` for ``conversation_complete`` — the model simply
    dropped the underscores. An exact-match check rejected the envelope, so it
    was spoken aloud instead of ending the call. Compare on letters only.
    """
    if not isinstance(value, str):
        return None
    squashed = "".join(ch for ch in value.lower() if ch.isalnum())
    for known in (END_SESSION_ACTION, LEGACY_ASK_AI_END_SESSION_ACTION):
        if squashed == "".join(ch for ch in known.lower() if ch.isalnum()):
            return known
    return None


def _repair_action_json(candidate: str) -> Optional[dict]:
    """Best-effort recovery of a nearly-valid action envelope.

    Only ever used AFTER strict json.loads has failed, and only to decide
    whether this text is an internal action (which must be swallowed) rather
    than speech (which is spoken). Getting it wrong in the conservative
    direction just means we return None and behave exactly as before.

    Handles the failure actually seen on a live call — a key whose closing
    quote is missing (``"farewell:"`` instead of ``"farewell":"``) — plus
    trailing commas and single-quoted keys, which are the other two ways these
    small models mangle JSON. Deliberately NOT a general JSON fixer: anything
    it cannot repair confidently returns None.
    """
    import re as _re

    text = candidate
    # `"key:"value"`  ->  `"key":"value"`   (missing closing quote on the key)
    text = _re.sub(r'"([A-Za-z_][A-Za-z0-9_]*):"', r'"\1":"', text)
    # `'key':`        ->  `"key":`
    text = _re.sub(r"'([A-Za-z_][A-Za-z0-9_]*)'\s*:", r'"\1":', text)
    # trailing comma before a closing brace
    text = _re.sub(r",\s*}", "}", text)
    try:
        repaired = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(repaired, dict):
        return None
    logger.warning(
        "end_session_action: model emitted malformed JSON; repaired rather "
        "than speaking it aloud. raw=%r", candidate[:160],
    )
    return repaired


def parse_end_session_action(text: str) -> Optional[dict[str, object]]:
    """Parse the provider-agnostic structured action envelope emitted by the LLM."""
    raw = (text or "").strip()
    if not raw:
        return None

    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None

    candidate = raw[start:end + 1]
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        # TOLERANT REPAIR (2026-08-03). A parse failure here is not harmless:
        # the envelope stops being recognised as an action, falls through as
        # ordinary reply text, and TTS READS IT ALOUD. That happened in
        # production (2026-07-08) — a caller heard:
        #
        #   {"action":"endsession","reason":"conversationcomplete",
        #    "farewell:"Message left, I'll try again another time. Cheers."}
        #
        # Note `farewell:"` — a missing quote. One dropped character turned a
        # clean hangup into machine noise played down the line.
        #
        # Constrained decoding would prevent this at the token level, but Groq
        # only supports strict mode on gpt-oss-20b/120b, and this codebase
        # deliberately does not run gpt-oss for conversational voice. So the
        # repair has to live here, where it works for every provider.
        payload = _repair_action_json(candidate)
        if payload is None:
            return None

    action = _normalise_action_name(payload.get("action") or payload.get("name"))
    if action not in {END_SESSION_ACTION, LEGACY_ASK_AI_END_SESSION_ACTION}:
        return None

    reason = payload.get("reason") or "conversation_complete"
    if reason not in END_SESSION_REASONS:
        reason = "conversation_complete"

    farewell = payload.get("farewell") or payload.get("message") or DEFAULT_FAREWELL
    if not isinstance(farewell, str) or not farewell.strip():
        farewell = DEFAULT_FAREWELL

    # Opt-out flag — accept real booleans and the common string spellings an
    # LLM might emit. Defaults to False so ordinary end-sessions are unaffected.
    raw_dnc = payload.get("do_not_call")
    do_not_call = raw_dnc is True or (
        isinstance(raw_dnc, str) and raw_dnc.strip().lower() in {"true", "yes", "1"}
    )

    action = {
        "reason": reason,
        "farewell": farewell.strip(),
        "do_not_call": do_not_call,
    }
    quote = payload.get("opt_out_quote")
    if do_not_call and isinstance(quote, str) and quote.strip():
        action["opt_out_quote"] = quote.strip()[:300]
    return action
