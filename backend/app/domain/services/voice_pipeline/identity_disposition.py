"""Caller evidence for opt-out persistence and explicit end-call authorization.

Ordinary identity interpretation and spoken responses belong to the model.
"""
import re

from app.domain.services.caller_assertions import (
    assertion_matches, continuation_after, phrase_pattern,
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

# Verb-and-object grammar ORed into the phrase list (24 Nov 2026 design: opt-out wordings).
# Matched by assertion_matches on its _text() copy, where a comma or a dash has become a space:
# so "two spaces" is how a removed comma looks to a lookahead.
_VERB = r"(?:call|ring|phone|contact|text|message)"
_VERB_ING = r"(?:calling|ringing|phoning|contacting|texting|messaging)"
_PHONE_KIND = (r"(?:(?:house|home|mobile|cell|landline|work|office|phone)\s+)?"
               r"(?:number|phone|line|house|home|mobile|cell|landline)")
_THIS_PHONE = r"(?:this\s+" + _PHONE_KIND + r")"
_MY_PHONE = r"(?:(?:my|our)\s+" + _PHONE_KIND + r"|my\s+\w+'s\s+(?:number|phone|mobile|house))"
_PHONE = r"(?:" + _THIS_PHONE + r"|" + _MY_PHONE + r")"
_OBJ = r"(?:me|us|here|" + _PHONE + r")"
_AGAIN = r"(?=\s+(?:again|anymore|any\s+more|ever)\b)"
_ITEM = (r"(?:me|us|(?:my|our|this)\s+(?:(?:phone|mobile|telephone)\s+)?"
         r"(?:number|details|information|info|data|name|contact\s+details))")
_LIST = r"(?:your|the|this|that|ur)\s+(?:\w+\s+)?(?:list|database|system|records|books|register)"
_END = (r"(?=\s*(?:$|[.!?;\n])|\s\s|\s*(?:please|now|right\s+now|ok|okay|alright|mate|thanks|thank\s+you|"
        r"again|anymore|any\s+more|ever|every\s+day|twice|already|last\s+time|"
        r"(?:two|three|four|five|several|many|so\s+many)\s+times)\b)")
_DNC_FORMS = (
    # G1 stop / quit / cease + calling, ringing, phoning, contacting, texting, messaging (+ harassing me)
    r"(?:stop|quit|cease)\s+" + _VERB_ING + r"(?:\s+" + _OBJ + r"\b|" + _END + r")",
    r"stop\s+harassing\s+(?:me|us)\b",
    # G2 never + verb + me / us, or + here / this number only with "again" (or at a clause end)
    r"never\s+(?:ever\s+)?" + _VERB + r"\s+(?:me|us)\b",
    r"never\s+(?:ever\s+)?" + _VERB + r"\s+(?:here|" + _PHONE + r")(?=\s+(?:again|anymore|any\s+more|ever)\b|" + _END[3:],
    # G3 don't / do not (you) (ever / dare) + call, ring, phone, contact, or bother + verb-ing; + me, us, here,
    # this number, again, back, a clause end; "my mobile" / "my work number" only with "again" (a phone of
    # theirs we may not be calling: "don't call my mobile, call the landline")
    r"(?:don't|dont|do\s+not)\s+(?:you\s+)?(?:ever\s+|dare\s+)?(?:(?:call|ring|phone|contact)|bother\s+" + _VERB_ING + r")"
    r"(?:\s+(?:me|us|here|" + _THIS_PHONE + r"|again|back|anymore|any\s+more)\b|\s+" + _MY_PHONE + _AGAIN + r"|"
    + _END + r")",
    # G4 remove / delete / erase / wipe / scrub + me, us, my number, my details ... at a clause end or + from your list
    r"(?:remove|delete|erase|wipe|scrub)\s+" + _ITEM + r"(?:\s+(?:from|off)\s+" + _LIST + r"\b|" + _END + r")",
    # G5 take / get + item + off (your list) or at a clause end; "I want off your list"
    r"(?:take|get)\s+" + _ITEM + r"\s+off(?:\s+" + _LIST + r"\b|" + _END + r")",
    r"want\s+(?:to\s+be\s+)?off\s+" + _LIST + r"\b",
    # G6 want / like / need + to be / my number + removed, taken off; can I be removed
    r"(?:want|like|need)\s+(?:to\s+be|(?:my|our)\s+(?:number|details|name|information)|us|me)\s+"
    r"(?:removed|taken\s+off|deleted|erased)\b",
    r"(?:can|could)\s+(?:i|we)\s+be\s+(?:removed|taken\s+off)\b",
    # G7 no more (of these / your) (phone) calls
    r"no\s+more\s+(?:(?:of\s+)?(?:these|your|the)\s+)?(?:phone\s+)?(?:calls|calling)\b",
    # G8 don't want / wish (to receive / get / have) any more / more / these / your calls; any calls from you
    r"(?:don't|dont|do\s+not)\s+(?:want|wish)\s+(?:to\s+(?:receive|get|have)\s+)?"
    r"(?:(?:any\s*more|more|these|your)\s+(?:(?:of\s+)?(?:these|your|the)\s+)?(?:phone\s+)?calls|"
    r"any\s+(?:phone\s+)?calls\s+from\s+(?:you|your))\b",
    # G9 don't want you (to) call / calling + object, again, or a clause end
    r"(?:don't|dont|do\s+not|never)\s+(?:want|wish)\s+(?:you|anyone)\s+(?:to\s+)?(?:" + _VERB + r"|" + _VERB_ING + r")"
    r"(?:\s+(?:" + _OBJ + r"|again|anymore|any\s+more|ever)\b|" + _END + r")",
    # G10 don't want to be called / rung / phoned / contacted (again, by you, at a clause end) -- not "called Dave"
    r"(?:don't|dont|do\s+not)\s+(?:want|wish)\s+to\s+be\s+(?:called|rung|phoned|contacted)"
    r"(?=\s*(?:$|[.!?;\n])|\s\s|\s+(?:again|anymore|any\s+more|ever|at\s+all|please|here|on\s+this\s+number|"
    r"by\s+(?:you|your|anyone))\b)",
    # G11 don't / never want to hear from you (again)
    r"(?:don't|dont|do\s+not|never)\s+want\s+to\s+hear\s+from\s+(?:you|your\s+company)\b",
    # G12 opt out OF CALLS only. A bare "opt out" / "I want to opt out" is left to the model: right
    # after the recording notice it most likely means the recording ("can I opt out?").
    r"opt\s+out\s+of\s+(?:these|your|the|all|future|any)\s+(?:phone\s+)?(?:calls|calling)\b",
    # G13 the do-not-call list in other words
    r"no\s+call\s+list\b", r"do\s+not\s+call\s+registry\b",
    r"put\s+(?:me|us|my\s+number|this\s+number|our\s+number)\s+on\s+(?:your|the)\s+(?:do\s+not\s+call|no\s+call|dnc)\b",
)

_EXPLICIT_GOODBYE_PHRASES = (
    "goodbye", "good bye",
    "bye now", "bye bye",
    "im hanging up", "i am hanging up",
    "i have to go", "ive got to go", "i gotta go", "gotta go",
    "hang up", "end the call", "end this call", "end call",
)

_DNC_PATTERN = re.compile(
    phrase_pattern(_DNC_PHRASES).pattern + r"|\b(?:" + "|".join(_DNC_FORMS) + r")", re.I)
_EXPLICIT_GOODBYE_PATTERN = phrase_pattern(_EXPLICIT_GOODBYE_PHRASES)


def contains_dnc(transcript: str) -> bool:
    """Directed opt-out, excluding quoted or negated uses."""
    return dnc_assertion_position(transcript) >= 0


# "Don't call me tomorrow" / "don't call me at work" is a scheduling
# preference, not an opt-out: a phrase followed at once by a time or place.
_SCHEDULING_AFTER = re.compile(
    r"^\W*(?:\w+\W+){0,2}?(?:tomorrow|today|tonight|later|right now|at the moment|at work|"
    r"this (?:week|morning|afternoon|evening|weekend|early|late|time)|until|till|before|after|"
    r"in the (?:morning|evening|afternoon)s?|"
    r"on (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?|"
    # added
    r"on (?:the )?weekends?|at (?:night|lunch(?:time)?|dinner(?:time)?|the office)|"
    r"during|between|while|when|so (?:early|late)|next (?:week|month)|"
    r"for (?:a (?:couple|few|while|bit)|the next)|"
    r"in (?:an|a few|the next)? ?(?:hour|week|month)s?)\b"
    # "don't call me now, call me later" / "don't ring back now": "now" straight after only
    r"|^\s+now\b",
    re.I,
)


# Only a "don't / do not call me ..." (or "no more ...") phrase can be a scheduling request. "Stop
# calling", "take me off", "remove me", "unsubscribe", "never call" and the
# do-not-call list stay opt-outs whatever follows ("stop calling me right now").
_SCHEDULABLE_PHRASE = re.compile(r"^(?:don'?t|do\s+not|no\s+more)\b(?!\s+call\s+(?:list|registry)\b)", re.I)


# Text preparation: same length as the input, so every offset the callers use still points into
# the original text (precedent: _HYPHENATED_BYE_BYE below).
_HYPHEN = re.compile(r"(?<=[A-Za-z])[-‐‑](?=[A-Za-z])")
_IMPERATIVE = (r"(?:stop|quit|cease|take|remove|delete|erase|wipe|lose|never|don'?t|do\s+not|please|"
               r"unsubscribe|put\s+me|get\s+me|no\s+more)\b")
_RUN_ON = re.compile(
    r"\b(?:(?:calling|ringing|phoning|bothering|texting|contacting|harassing)\s+(?:me|us|here)(?:\s+again)?|"
    r"(?:tell|telling)\s+you(?:\s+(?:people|guys|lot|all))?|"
    r"(?:i|we)(?:'?ve|\s+have)?(?:\s+(?:already|just))?\s+(?:told|asked)\s+"
    r"(?:you\s+(?:people|guys|lot|all)|your\s+(?:people|company|colleagues|team|lot|staff|firm|office))(?:\s+to)?|"
    r"(?:my|this|our)\s+number|do\s+you\s+want|is\s+this|what\s+part\s+of)(\s+)(?=" + _IMPERATIVE + ")",
    re.I,
)


def _prepare(text: str) -> str:
    """Same length as the input, so offsets stay valid (like _HYPHENATED_BYE_BYE)."""
    text = _HYPHEN.sub(" ", str(text or ""))
    # A question or a complaint run straight into the request ("why do you keep calling me stop
    # calling me"): end that clause with a newline in place of the last space.
    return _RUN_ON.sub(lambda m: m[0][:-1] + "\n", text)


# "opt out of the recording" is a recording choice.
_RECORDING_AFTER = re.compile(r"^\s+(?:of\s+)?(?:the\s+|this\s+|being\s+|call\s+)?record", re.I)
# A statement about a habit, not a request: "my kids don't call anymore", "they never call us",
# "I quit calling them", "they stop calling after a while", "I'll remove this number from my contacts".
# Not "you people": that is the company being addressed ("you people stop calling me").
_SUBJECT_BEFORE = re.compile(
    r"\b(?:i|we|they|he|she|it|(?<!\byou )people|nobody|kids|who)"
    r"(?:'?ll|'?d|\s+(?:will|would|always|usually|often|just))?\s+$",
    re.I)
_STATEMENT_HEAD = re.compile(
    r"^(?:(?:don'?t|do\s+not|never|stop|quit|cease)\s+(?:call|ring|phon|contact|text)|"
    r"remove|delete|erase|wipe|take|get|lose)", re.I)
# Said to or by a third party: "I told them to stop calling", "my husband asked them to remove our
# number", "he keeps telling me to stop", "the app says to remove my number". Not "I told you to ...".
_THIRD_PARTY_BEFORE = re.compile(
    r"\b(?:told|asked|telling|asking|said|says)\s+(?!you\b|your\b)(?:[\w']+\s+){0,3}?to\s+$|"
    r"\b(?:they|he|she)(?:'?d|'?ll|\s+(?:would|will|could|should|might))\s+$",
    re.I)
# Naming, not calling: "stop calling it a discount", "don't call me that", "stop calling me Mrs Jones".
_NAMING_AFTER = re.compile(
    r"^\s+(?:it(?!'?s\b)|that|by|this(?!\s+(?:number|phone|line|house|home|mobile))|"
    r"(?:me\s+)?(?:that|by|mr|mrs|ms|miss|mister|dr)\b)\b",
    re.I,
)
_NAMING_TAIL = re.compile(r"^[^.!?;\n]*?\b(?:my\s+name|call\s+me\s+[A-Za-z]|it'?s\s+(?:mr|mrs|ms|miss|dr)\b)", re.I)
_CALL_HEAD = re.compile(r"^(?:stop|quit|don'?t|do\s+not)\s+(?:calling|call|contact|contacting)\b", re.I)
# "take me off hold / speaker" is not a list.
_NOT_A_LIST_AFTER = re.compile(r"^\s+(?:hold|speaker\w*|mute|loudspeaker)\b", re.I)
# "you never call me back" is a complaint about callbacks.
_NEVER_BACK = re.compile(r"^never\s+(?:ever\s+)?(?:call|ring|phone)\s+(?:me|us)$", re.I)
# "did you lose my number?" is a question about the past.
_DID_YOU_LOSE = re.compile(r"\b(?:did|have|has)\s+(?:you|they|we)\s+$", re.I)


def dnc_assertion_position(transcript: str) -> int:
    text = _prepare(transcript)
    for match in reversed(assertion_matches(text, _DNC_PATTERN)):
        phrase, after, before = match[0], text[match.end():], text[:match.start()]
        if _SCHEDULABLE_PHRASE.match(phrase) and _SCHEDULING_AFTER.match(after):
            continue
        if re.search(r"opt\s+out", phrase, re.I) and _RECORDING_AFTER.match(after):
            continue
        if _STATEMENT_HEAD.match(phrase) and _SUBJECT_BEFORE.search(before):
            continue
        if _THIRD_PARTY_BEFORE.search(before):
            continue
        if _CALL_HEAD.match(phrase) and (_NAMING_AFTER.match(after) or _NAMING_TAIL.match(after)):
            continue
        if re.match(r"take\s+me\s+off$", phrase, re.I) and _NOT_A_LIST_AFTER.match(after):
            continue
        if _NEVER_BACK.match(phrase) and re.match(r"^\s+back\b", after, re.I):
            continue
        if re.match(r"lose\b", phrase, re.I) and _DID_YOU_LOSE.search(before):
            continue
        return match.start()
    return -1


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
