"""Caller evidence for opt-out persistence and explicit end-call authorization.

Ordinary identity interpretation and spoken responses belong to the model.
"""
import re

from app.domain.services.caller_assertions import (
    _text as _quote_blanked_text, assertion_matches, continuation_after, phrase_pattern,
)

_DNC_PHRASES = (
    "stop calling",
    "dont call me", "dont call us", "dont call again", "dont call back",
    "dont call here", "dont call this", "dont call anymore", "dont ever call",
    "do not call me", "do not call us", "do not call again", "do not call back",
    "do not call here", "do not call this", "do not call anymore",
    "dont contact me", "dont contact us", "do not contact me", "do not contact us",
    "take me off", "remove me from", "take my number off",
    "lose my number", "delete my number",
    "opt me out", "unsubscribe",
    "never call me", "never call us", "never call again", "never contact",
    # UK wording; paraphrases beyond this floor are the model's verified quote.
    "stop ringing me", "stop ringing us", "stop ringing this number", "dont ring me", "dont ring us",
    "do not ring me", "do not ring us",
    "never ring me", "never ring us", "never ring again", "never ring this number again",
    "dont phone me", "do not phone me", "do not call list",
    # Hostile dismissals count as a do-not-call request (owner decision 2026-10-21);
    # STT may mask profanity, so "f*** off" / "f off" are included.
    "fuck off", "f*** off", "f off", "fuck you", "piss off", "sod off",
)

# STRICT CORE (24 Nov 2026, fourth build; new path only). A caller's whole TURN must be requests to stop and
# nothing else: every sentence of the turn is either a request sentence (optional lead-ins, ONE core form,
# optional tails) or a courtesy sentence from a closed list (a stricter list beside form C4). Anything with a
# reason, a question, a time, a name or a correction attached is left to the language model, exactly as on
# main; several request sentences in one turn are allowed. A wrong catch here
# writes a PERMANENT do-not-call entry, so this path misses on purpose. Every list is a closed list of
# literals: a later change is one line. Matching is ASCII case-insensitive on a same-length copy of the text
# in which quoted words are blanked, an apostrophe is optional in every listed word ("dont", "im"), and a
# space in a listed word is any run of whitespace.
#
# Lead-ins: zero to three, in any order, before the core form.
_CORE_LEADINS = (
    "please", "just", "look", "listen", "right", "ok", "okay", "no", "hey", "oi", "erm", "um", "uh", "yeah",
    "so", "well", "honestly", "seriously", "for god's sake", "for the last time", "i said", "i told you",
    "i told you to", "i'm telling you", "i'm telling you to",
    "you need to", "you have to", "i want you to", "i need you to", "i'd like you to", "i've told you",
    "i have told you", "not interested",
)
# One optional intensifier where a core form shows it (the masked forms are what speech-to-text writes).
_CORE_INTENSIFIERS = (
    "bloody", "fucking", "freaking", "frigging", "flipping", "effing", "damn", "f***ing", "f**king", "f*cking",
)
# The object of a call: who or what must not be called (another number or place is not a request to stop).
_CORE_OBJECTS = ("me", "us", "here", "this number", "my number", "this phone", "my phone")
# The kinds of list a number can be taken off.
_CORE_LIST_KINDS = ("call", "calling", "contact", "phone", "dialling", "dialing", "marketing")
# What a number is taken off: the list itself or the records that hold it.
_CORE_RECORDS = ("list", "system", "database", "records")
# What may close the sentence after the core form: zero to three of these ("now" is not a tail; only form C1
# carries it).
_CORE_TAILS = (
    "please", "thanks", "thank you", "ok", "okay", "alright", "at all", "ever", "again", "anymore", "any more",
    "i mean it", "do you hear me", "i'm not interested", "i am not interested",
    "i've told you before", "i've told you already", "i told you already", "bye", "goodbye", "bye bye",
    "not interested",
)
# The only non-request sentences that may stand beside a request in the same turn (whole sentence, commas
# ignored); beside a form-C4 request the stricter _C4_COURTESY list applies instead.
_CORE_COURTESY = (
    "thanks", "thank you", "thanks very much", "cheers", "bye", "goodbye", "bye bye", "ok", "okay", "alright",
    "please", "no", "no thanks", "no thank you", "not interested", "i'm not interested",
    "i am not interested", "we're not interested", "i mean it",
)
# Lead-ins of form C4 alone ("no more calls" is also what a caller says when echoing or answering the agent).
_C4_LEADINS = ("please", "look", "listen", "just", "i said")
# The only non-request sentences that may stand beside a C4 request: a subset of the list above, with no "no",
# "ok", "alright" or the like.
_C4_COURTESY = (
    "thanks", "thank you", "thanks very much", "cheers", "bye", "goodbye", "bye bye", "please", "not interested",
    "i'm not interested", "i am not interested", "we're not interested", "i mean it",
)
# Verbs (infinitive and -ing) and the "again" words of the forms that need one.
_CORE_VERBS = ("call", "ring", "phone", "contact")
_CORE_VERBS_ING = ("calling", "ringing", "phoning", "contacting")
_CORE_AGAIN = ("again", "anymore", "any more", "ever again", "ever")


def _alt(words) -> str:
    """An alternation of literals; a space in a literal is any run of whitespace, an apostrophe is optional."""
    return "(?:" + "|".join(
        r"\s+".join("'?".join(re.escape(part) for part in piece.split("'")) for piece in word.split())
        for word in words) + ")"


_X = "(?:" + _alt(_CORE_INTENSIFIERS) + r"\s+)?"
_OBJ_ = _alt(_CORE_OBJECTS)
_LIST_ = r"(?:" + _alt(_CORE_LIST_KINDS) + r"\s+)?"
_NOT = _alt(("don't", "do not"))
_WE = _alt(("i", "we"))
# A list is OURS: "your" only ("the list" may be any list).
_THE_LIST = r"\s+" + _alt(("from", "off")) + r"\s+your\s+" + _LIST_
_CORE_FORMS = (
    # C1 stop / quit / cease + calling (+ an object) (+ now, right now)
    _alt(("stop", "quit", "cease")) + r"\s+" + _X + _alt(_CORE_VERBS_ING) + r"(?:\s+" + _OBJ_ + ")?"
    + r"(?:\s+(?:right\s+)?now)?",
    # C2 don't (ever) call + object + an "again" word: both required
    _NOT + r"\s+(?:" + _alt(("ever", "you ever", "you dare")) + r"\s+)?" + _X + _alt(_CORE_VERBS)
    + r"\s+" + _OBJ_ + r"\s+" + _alt(_CORE_AGAIN),
    # C3 never (ever) call + object (+ again)
    r"never(?:\s+ever)?\s+" + _alt(_CORE_VERBS) + r"\s+" + _OBJ_ + r"(?:\s+" + _alt(("again", "ever again")) + ")?",
    # C5 remove / delete / take my number off your list: all three need the list part (a bare "remove my number"
    # may answer a question about a record, and bare "take my number" is giving it)
    _alt(("remove", "delete", "take")) + r"\s+" + _alt(("my number", "this number")) + _THE_LIST
    + _alt(_CORE_RECORDS),
    # C6 remove / take me off your list
    _alt(("remove me", "remove us", "take me", "take us")) + _THE_LIST + "list",
    # C7 put me on the do-not-call list
    r"put\s+" + _alt(("me", "us", "my number", "this number")) + r"\s+on\s+" + _alt(("your", "the")) + r"\s+"
    + _alt(("do not call", "dnc", "no call")) + r"\s+list",
    # C8 I don't want any more calls (bare "any calls" is not enough: a plan with no call minutes)
    _WE + r"\s+" + _NOT + r"\s+want\s+" + _alt(("any more", "anymore", "your", "these", "any more of these"))
    + r"\s+calls",
    # C8b I don't want (any) calls from you
    _WE + r"\s+" + _NOT + r"\s+want\s+(?:any\s+)?calls\s+from\s+you",
    # C9 I don't want you to call + object + an "again" word: both required
    _WE + r"\s+" + _NOT + r"\s+want\s+you\s+"
    + _alt(("to call", "calling", "to ring", "ringing", "to phone", "phoning")) + r"\s+" + _OBJ_ + r"\s+"
    + _alt(("again", "anymore", "any more", "ever")),
    # N1 get me / us off your (calling) list
    r"get\s+" + _alt(("me", "us")) + r"\s+off\s+your\s+(?:calling\s+)?list",
    # N2 delete me / us from your list
    r"delete\s+" + _alt(("me", "us")) + r"\s+from\s+your\s+list",
    # N3 take my name off your list
    r"take\s+my\s+name\s+off\s+your\s+list",
    # N4 remove my name from your (kind of) list
    r"remove\s+my\s+name\s+from\s+your\s+" + _LIST_ + "list",
    # N5 I want / would like / 'd like to be removed from (or off) / taken off your (kind of) list
    _alt(("i want to be", "i would like to be", "i'd like to be", "we want to be", "we would like to be",
          "we'd like to be")) + r"\s+(?:removed\s+" + _alt(("from", "off")) + r"|taken\s+off)\s+your\s+" + _LIST_
    + "list",
    # N6 I do not wish to be contacted / to receive any more calls
    _WE + r"\s+" + _NOT + r"\s+wish\s+to\s+" + _alt(("be contacted", "receive any more calls")),
    # N7 I don't want to be called / contacted again
    _WE + r"\s+" + _NOT + r"\s+want\s+to\s+be\s+" + _alt(("called", "contacted")) + r"\s+again",
    # N8 cease all calls (to this number)
    r"cease\s+all\s+calls(?:\s+to\s+this\s+number)?",
)
# Form C4 on its own (it has its own lead-ins and a stricter courtesy list beside it).
_FORM_C4 = (r"no\s+more\s+(?:of\s+" + _alt(("these", "your")) + r"\s+)?(?:phone\s+)?calls(?:\s+from\s+you)?")
# ONE anchored pattern per sentence (used with fullmatch): lead-ins, one core form, tails, nothing else;
# group "core" is any form but C4, group "core4" is form C4.
_SENTENCE_REQUEST = re.compile(
    r"\s*(?:(?:" + _alt(_CORE_LEADINS) + r"\s+){0,3}(?P<core>" + "|".join(_CORE_FORMS) + r")"
    r"|(?:" + _alt(_C4_LEADINS) + r"\s+){0,3}(?P<core4>" + _FORM_C4 + r"))"
    r"(?:\s+" + _alt(_CORE_TAILS) + r"){0,3}\s*",
    re.I | re.A,
)
# A courtesy sentence: the whole sentence is one entry of the closed courtesy list.
_COURTESY_SENTENCE = re.compile(r"\s*" + _alt(_CORE_COURTESY) + r"\s*", re.I | re.A)
# The stricter courtesy sentence allowed beside a C4 request.
_C4_COURTESY_SENTENCE = re.compile(r"\s*" + _alt(_C4_COURTESY) + r"\s*", re.I | re.A)
# A sentence ends at . ! ? ; or a line break; the whole run of terminators is kept (a "?" in it makes a question).
_SENTENCE = re.compile(r"([^.!?;\r\n]+)([.!?;\r\n]*)")
# Same-length normalisation for matching only: a hyphen between letters becomes a space (quotes are blanked,
# curly apostrophes made straight and commas made spaces by caller_assertions._text, also same length).
_CORE_HYPHEN = re.compile(r"(?<=[A-Za-z])[-‐‑](?=[A-Za-z])")

_EXPLICIT_GOODBYE_PHRASES = (
    "goodbye", "good bye",
    "bye now", "bye bye",
    "im hanging up", "i am hanging up",
    "i have to go", "ive got to go", "i gotta go", "gotta go",
    "hang up", "end the call", "end this call", "end call",
)

# The phrase list alone: the pattern of the 78f3ce42 function, which still runs first.
_DNC_PATTERN = phrase_pattern(_DNC_PHRASES)
_EXPLICIT_GOODBYE_PATTERN = phrase_pattern(_EXPLICIT_GOODBYE_PHRASES)


def contains_dnc(transcript: str) -> bool:
    """Directed opt-out, excluding quoted or negated uses."""
    return dnc_assertion_position(transcript) >= 0


# The scheduling words of the 78f3ce42 function: the legacy function skips its "don't ..." phrase when they
# follow, and the new path skips a request sentence when they follow it.
_OLD_SCHEDULING_AFTER = re.compile(
    r"^\W*(?:\w+\W+){0,2}?(?:tomorrow|today|tonight|later|right now|at the moment|at work|"
    r"this (?:week|morning|afternoon|evening|weekend)|until|before|after|in the (?:morning|evening)|"
    r"on (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b",
    re.I,
)


_LEGACY_SCHEDULABLE_PHRASE = re.compile(r"^(?:don'?t|do\s+not)\b(?!\s+call\s+list\b)", re.I)


def _legacy_dnc_assertion_position(transcript: str) -> int:
    """The 78f3ce42 function, unchanged: the phrase list alone, one scheduling check."""
    for match in reversed(assertion_matches(transcript, _DNC_PATTERN)):
        if (_LEGACY_SCHEDULABLE_PHRASE.match(match[0])
                and _OLD_SCHEDULING_AFTER.match(str(transcript or "")[match.end():])):
            continue
        return match.start()
    return -1


def dnc_assertion_position(transcript: str) -> int:
    """Whatever the legacy function catches is returned as it returns it, ungoverned by the strict core.
    Only where it finds nothing does the strict core (whole-turn rule) run."""
    legacy = _legacy_dnc_assertion_position(transcript)
    if legacy >= 0:
        return legacy
    return _new_dnc_assertion_position(transcript)


def _new_dnc_assertion_position(transcript: str) -> int:
    """Strict core: the offset of the core form of the LAST request sentence (one not followed by a scheduling
    word), but only when every sentence of the turn is a request sentence or a courtesy sentence. Any other
    sentence, or a question, makes it -1; so does a form-C4 request beside a courtesy sentence that is not on
    the stricter C4 courtesy list (request sentences are never courtesy candidates)."""
    original = str(transcript or "")
    text = _CORE_HYPHEN.sub(" ", _quote_blanked_text(original))
    position = -1
    has_c4 = False
    courtesy_bodies = []
    for sentence in _SENTENCE.finditer(text):
        body = sentence[1]
        if not body.strip():
            continue
        if "?" not in sentence[2]:
            match = _SENTENCE_REQUEST.fullmatch(body)
            if match:
                if match.group("core4") is not None:
                    has_c4 = True
                if not _OLD_SCHEDULING_AFTER.match(original[sentence.end(1):]):
                    group = "core4" if match.group("core4") is not None else "core"
                    position = sentence.start(1) + match.start(group)
                continue
            if _COURTESY_SENTENCE.fullmatch(body):
                courtesy_bodies.append(body)
                continue
        return -1
    if has_c4 and not all(_C4_COURTESY_SENTENCE.fullmatch(b) for b in courtesy_bodies):
        return -1
    return position


# STT writes the spaced goodbye "bye bye" as "Bye-bye." Same words: one character
# swapped for a space, so offsets in the transcript stay valid. Bare "bye" is
# still not a goodbye.
_HYPHENATED_BYE_BYE = re.compile(r"\b(bye)[-‐‑](?=bye\b)", re.I)


# "bye bye" is a farewell only when it stands alone in its clause. Clauses end at
# sentence ends and commas. The words just before it in its own clause (none
# when a comma or full stop comes first) and just after it must be only
# interjections, thanks, farewell or courtesy phrases, or a term of address (after
# it also a single capitalised name). Earlier clauses are free: "Thanks for your
# help, bye-bye". Nothing may take it back later (wait, go on, a question), and no
# pause request may stand before it in the same sentence. Anything else makes it
# part of a sentence ("kiss your money bye-bye", "we said bye-bye to BT").
# Hyphen and spaced forms share this one path.
_FAREWELL_WORDS = (
    r"okay|ok|right|alright|all right|yes|yeah|yep|no|nope|well|so|oh|then|now|for now|ta|"
    r"lovely|great|fine|good|sounds good|that's (?:great|fine|good|lovely|ok|okay|all right|alright)|"
    r"no problem|not a problem|no worries|sorry|never mind|cheers|and|again|too|you too|"
    r"(?:thanks|thank you)(?: (?:so|very))?(?: much| a lot)?|thank you so much|"
    r"for calling|for your (?:time|help|call)|for that|"
    r"i'm not interested|i am not interested|not interested|no thanks|"
    r"take care|all the best|god bless|good luck|enjoy|speak soon|talk soon|"
    r"speak to you (?:soon|later)|i'll speak to you (?:soon|later)|see you(?: soon| later)?|"
    r"have a (?:good|nice|great|lovely|wonderful) (?:day|evening|night|weekend|one|afternoon|morning|time)|"
    r"i(?:'ve| have) got to (?:go|run)|i have to (?:go|run)|i'm off|i'm busy(?: right now)?|"
    r"don't call (?:me )?(?:again|back)|do not call (?:me )?(?:again|back)|"
    r"mate|love|darling|dear|sweetheart|pal|sir|madam|honey|buddy|bye|goodbye"
)
_FAREWELL_ONLY = re.compile(r"(?:(?:" + _FAREWELL_WORDS + r")\s+)*")
_NAME = re.compile(r"[A-Z][a-z]+")
_PAUSE_BEFORE = re.compile(
    r"\b(?:hang\s+on|hold\s+on|wait|one\s+moment|just\s+a\s+(?:sec|second|minute))\b", re.I)
_TAKEN_BACK_AFTER = re.compile(
    r"\b(?:wait|hang\s+on|hold\s+on|go\s+on|carry\s+on|actually|one\s+more\s+thing|before\s+you\s+go|"
    r"what\s+do\s+you\s+mean|continue|keep\s+going|you\s+were\s+saying|question)\b|\?", re.I)
_SENTENCE_END = re.compile(r"[.!?;\n]")
_CLAUSE_END = re.compile(r"[.!?;\n,]")


def _words(fragment: str) -> str:
    return " ".join(re.findall(r"[a-z']+", fragment.lower().replace("\u2019", "'"))) + " "


def _only_farewell(fragment: str) -> bool:
    """Only farewell words. Over 12 words it cannot be one, and the regex's
    overlapping alternatives would backtrack exponentially on a long non-match.
    The count is of the words the regex is given, not of whitespace-separated
    pieces: "no/thanks/no/thanks" is one piece and many words."""
    words = _words(fragment).lstrip()
    return len(words.split()) <= 12 and bool(_FAREWELL_ONLY.fullmatch(words))


def _is_standalone_bye_bye(text: str, match: re.Match) -> bool:
    """A "bye bye" counts only as a farewell, not as words inside a sentence."""
    if " ".join(re.findall(r"[a-z]+", match[0].lower())) != "bye bye":
        return True
    before_ends = [m.end() for m in _CLAUSE_END.finditer(text, 0, match.start())]
    clause_start = before_ends[-1] if before_ends else 0
    after = _CLAUSE_END.search(text, match.end())
    clause_end = after.start() if after else len(text)
    sentence_ends = [m.end() for m in _SENTENCE_END.finditer(text, 0, match.start())]
    sentence_start = sentence_ends[-1] if sentence_ends else 0
    if _PAUSE_BEFORE.search(text[sentence_start:match.start()]) or _TAKEN_BACK_AFTER.search(text[match.end():]):
        return False
    if not _only_farewell(text[clause_start:match.start()]):
        return False
    tail = text[match.end():clause_end]
    return _only_farewell(tail) or bool(_NAME.fullmatch(tail.strip()))


def contains_explicit_goodbye(transcript: str) -> bool:
    """Explicit caller ending with no later request to continue."""
    transcript = _HYPHENATED_BYE_BYE.sub(r"\1 ", str(transcript or ""))
    matches = [m for m in assertion_matches(transcript, _EXPLICIT_GOODBYE_PATTERN)
               if _is_standalone_bye_bye(transcript, m)]
    position = matches[-1].start() if matches else -1
    return position >= 0 and not continuation_after(transcript, position)
