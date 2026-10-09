"""Money and percentages the agent speaks must come from what it was given.

Standard HAL-2 (docs/standards/voice-agent-standards.md). A price a caller
hears is a price they act on. The model is told to state only sourced figures;
this is the cheap check that it did, run on each sentence before TTS:

* a *figure* is a number written next to a currency marker (any Unicode
  currency symbol, an ISO 4217 code, a currency noun) or a percent;
  "5k", "£1.5m" and "1.5 million" are read as their full values, and so are
  numbers written as words ("one thousand seven hundred and sixty rupees",
  "two point five million", "two lakh"): models that write for speech spell
  prices out (test call f5dcac8e);
* it passes when the same value appears anywhere in the turn's grounding:
  the system prompt, the knowledge the model read, action results, or the
  conversation so far (a caller's own figure may be repeated back);
* otherwise the sentence is withheld and an honest line is spoken instead,
  once per turn.

Value match only, currency-agnostic: the vocabularies below are generic world
data, not any business or market. Derived figures (sums, differences,
per-seat maths) are withheld on purpose; the source must state them. Bare
numbers are never blocked, only logged when absent from the grounding, so
dates, counts, units ("48 HRS", "3 BHK") and read-backs are untouched.
Words are read as standard cardinals only: a digit-by-digit read-back is a
digit string, and a year-style reading ("seventeen sixty") is not guessed at.

VOICE_FIGURE_GROUNDING=enforce (default) | observe | off
"""
from __future__ import annotations

import logging
import os
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Callable, Iterable, Union

logger = logging.getLogger(__name__)

UNGROUNDED_FIGURE_LINE = "I can't confirm that exact figure from the information I have."

_NUMBER = re.compile(
    r"(?<![\w.,])(?P<num>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"(?:(?P<suffix>[kKmM]|bn)(?!\w)|(?!\w))"
    r"(?:\s+(?P<scale>thousand|million|billion)\b)?"
)
_SCALE = {"k": 10**3, "m": 10**6, "bn": 10**9, "thousand": 10**3, "million": 10**6, "billion": 10**9}
# Generic world-currency nouns (singular; a trailing "s" is accepted).
_CURRENCY_WORDS = frozenset("""
    pound quid pence penny dollar buck cent euro rupee rs paisa dirham dhs riyal rial
    dinar rand yen yuan renminbi rmb franc peso lira naira cedi shilling taka ringgit
    baht rupiah dong krona krone kronor zloty forint koruna hryvnia ruble rouble tenge
    percent
""".split())
# Active ISO 4217 codes, minus the ones that are everyday words or names in
# upper case (ALL, TOP, TRY, MAD, PEN, CUP, SOS, BOB, MOP, BAM, NAD, GEL, AMD).
_ISO_4217 = frozenset("""
    AED AFN ANG AOA ARS AUD AWG AZN BBD BDT BGN BHD BIF BMD BND BOV BRL BSD BTN
    BWP BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP COU CRC CUC CVE CZK DJF DKK
    DOP DZD EGP ERN ETB EUR FJD FKP GBP GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF
    IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK
    LBP LKR LRD LSL LYD MDL MGA MKD MMK MNT MRU MUR MVR MWK MXN MXV MYR MZN NGN
    NIO NOK NPR NZD OMR PAB PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR
    SDG SEK SGD SHP SLE SLL SRD SSP STN SVC SYP SZL THB TJS TMT TND TTD TWD TZS
    UAH UGX USD UYU UZS VES VND VUV WST XAF XCD XOF XPF YER ZAR ZMW ZWL
""".split())
_WORD_BEFORE = re.compile(r"([A-Za-z]+)\.?\s*$")
_WORD_AFTER = re.compile(r"^\s*([A-Za-z]+)(?:\s+(cent)\b)?")


def _currency_word(word: str) -> bool:
    lower = word.lower()
    return lower in _CURRENCY_WORDS or lower.rstrip("s") in _CURRENCY_WORDS or word in _ISO_4217


def _is_figure(text: str, start: int, end: int) -> bool:
    before, after = text[:start].rstrip(), text[end:]
    if before and unicodedata.category(before[-1]) == "Sc":
        return True
    stripped_after = after.lstrip()
    if stripped_after[:1] == "%" or (stripped_after and unicodedata.category(stripped_after[0]) == "Sc"):
        return True
    match = _WORD_BEFORE.search(text[:start])
    if match and _currency_word(match.group(1)):
        return True
    match = _WORD_AFTER.match(after)
    if match and (_currency_word(match.group(1)) or (match.group(1).lower() == "per" and match.group(2))):
        return True
    return False


def _digit_numbers(text: str):
    """(start, end, text, raw value, full value); full applies k/m/million."""
    for match in _NUMBER.finditer(text):
        try:
            raw = Decimal(match.group("num").replace(",", ""))
        except InvalidOperation:
            continue
        scale = match.group("suffix") or match.group("scale")
        full = raw * _SCALE[scale.lower()] if scale else raw
        yield match.start(), match.end(), match.group(0), raw.normalize(), full.normalize()


_UNIT_WORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine".split())}
_TEEN_WORDS = {w: i + 10 for i, w in enumerate(
    "ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS_WORDS = {w: (i + 2) * 10 for i, w in enumerate(
    "twenty thirty forty fifty sixty seventy eighty ninety".split())}
_SCALE_WORDS = {"thousand": 10**3, "lakh": 10**5, "lakhs": 10**5, "million": 10**6,
                "crore": 10**7, "crores": 10**7, "billion": 10**9}
_CARDINAL = "|".join(sorted([*_UNIT_WORDS, *_TEEN_WORDS, *_TENS_WORDS, *_SCALE_WORDS, "hundred"],
                            key=len, reverse=True))
# A run of number words joined by spaces, hyphens, "and" or "point". A scale
# word right after digits belongs to them ("2 thousand"), not to a new run.
_WORD_RUN = re.compile(
    rf"(?<![\w\d])(?<!\d )(?:a\s+(?=(?:hundred|{'|'.join(_SCALE_WORDS)})\b))?(?:{_CARDINAL})"
    rf"(?:(?:\s+|-)(?:(?:and|point)\s+)?(?:{_CARDINAL}))*(?![\w-])",
    re.IGNORECASE,
)


def _cardinal(words: list[str]):
    """Value of a well-formed English cardinal, else None.

    "one thousand seven hundred and sixty" -> 1760, "seventeen hundred" ->
    1700, "two point five million" -> 2500000, "two lakh fifty thousand" ->
    250000. A unit after a unit ("one two three") or a tens after a teen
    ("seventeen sixty") is a digit string or a year-style reading: None.
    """
    total, group, last, top = Decimal(0), None, None, None
    has_hundred, i = False, 0
    while i < len(words):
        word = words[i]
        if word == "a" and i == 0:
            group, last = Decimal(1), "a"
        elif word == "and":
            if last not in ("hundred", "scale") or i + 1 == len(words):
                return None
            last = "and"
        elif word == "zero":
            return Decimal(0) if len(words) == 1 else None
        elif word in _UNIT_WORDS:
            if last not in (None, "scale", "and", "hundred", "tens"):
                return None
            group, last = (group or 0) + _UNIT_WORDS[word], "unit"
        elif word in _TEEN_WORDS or word in _TENS_WORDS:
            if last not in (None, "scale", "and", "hundred"):
                return None
            group = (group or 0) + _TEEN_WORDS.get(word, _TENS_WORDS.get(word, 0))
            last = "teen" if word in _TEEN_WORDS else "tens"
        elif word == "hundred":
            # A count must come first ("a few hundred" is not 100).
            if group is None or group >= 100 or has_hundred or last == "decimal":
                return None
            group, last, has_hundred = group * 100, "hundred", True
        elif word in _SCALE_WORDS:
            scale = _SCALE_WORDS[word]
            if group is None or last == "and" or (top is not None and scale >= top):
                return None
            total += group * scale
            group, last, top, has_hundred = None, "scale", scale, False
        elif word == "point":
            digits = []
            while i + 1 < len(words) and words[i + 1] in _UNIT_WORDS:
                i += 1
                digits.append(str(_UNIT_WORDS[words[i]]))
            if not digits or group is None or last in ("and", "scale", "decimal"):
                return None
            group, last = group + Decimal("0." + "".join(digits)), "decimal"
        else:
            return None
        i += 1
    if last in ("and", "a"):
        return None
    return (total + (group or 0)).normalize()


def _word_numbers(text: str):
    """(start, end, text, value, value) for numbers written as words.

    A run that is not one cardinal is split at each conjunction "and" that
    the grammar cannot carry ("between one and two thousand"); a remaining
    run of single digits is a read-back and keeps its digit-string value.
    """
    for run in _WORD_RUN.finditer(text):
        pieces: list[list[tuple[str, int, int]]] = [[]]
        for m in re.finditer(r"[A-Za-z]+", run.group(0)):
            word = m.group(0).lower()
            if word == "and":
                pieces.append([])
            else:
                pieces[-1].append((word, run.start() + m.start(), run.start() + m.end()))
        merged: list[list[tuple[str, int, int]]] = []
        for piece in filter(None, pieces):
            joined = merged[-1] + [("and", -1, -1)] + piece if merged else None
            if joined and _cardinal([w for w, _, _ in joined]) is not None:
                merged[-1] = joined
            else:
                merged.append(piece)
        for piece in merged:
            words = [w for w, _, _ in piece]
            value = _cardinal(words)
            if value is None and len(words) > 1 and all(w in _UNIT_WORDS for w in words):
                value = Decimal("".join(str(_UNIT_WORDS[w]) for w in words)).normalize()
            if value is not None:
                start, end = piece[0][1], piece[-1][2]
                yield start, end, text[start:end], value, value


def _numbers(text: str):
    """Every number in ``text``, written as digits or as words."""
    yield from _digit_numbers(text)
    yield from _word_numbers(text)


@lru_cache(maxsize=256)
def _values_in(text: str) -> frozenset[Decimal]:
    """Each source is parsed once: the prompt and history repeat every sentence."""
    values: set[Decimal] = set()
    for _, _, _, raw, full in _numbers(text):
        values.add(raw)
        values.add(full)
    return frozenset(values)


def grounded_values(sources: Iterable[object]) -> set[Decimal]:
    values: set[Decimal] = set()
    for source in sources:
        if source:
            values |= _values_in(str(source))
    return values


Sources = Union[Iterable[object], Callable[[], Iterable[object]]]


def ungrounded_figures(sentence: str, sources: Sources) -> tuple[list[str], list[str]]:
    """Return (figures absent from the grounding, bare numbers >= 10 absent)."""
    sentence = sentence or ""
    candidates = []
    for start, end, text, _, full in sorted(_numbers(sentence)):
        figure = _is_figure(sentence, start, end)
        if figure or full >= 10:
            candidates.append((text, full, figure))
    if not candidates:
        return [], []
    known = grounded_values(sources() if callable(sources) else sources)
    figures, bare = [], []
    for text, full, figure in candidates:
        if full not in known:
            (figures if figure else bare).append(text)
    return figures, bare


def figure_grounding_mode() -> str:
    mode = os.getenv("VOICE_FIGURE_GROUNDING", "enforce").strip().lower()
    return mode if mode in {"enforce", "observe", "off"} else "enforce"


def guard_figures(session: object, sentence: str, sources: Sources) -> str:
    """What to speak for ``sentence`` under the figure-grounding standard.

    ``sources`` may be a callable so the grounding is only built for a
    sentence that holds a figure or a bare number of 10 or more. Returns ""
    when the honest line was already spoken this turn, so the caller never
    hears it twice in a row.
    """
    mode = figure_grounding_mode()
    if mode == "off" or not sentence:
        return sentence
    figures, bare = ungrounded_figures(sentence, sources)
    call_id = str(getattr(session, "call_id", "?"))[:12]
    if bare:
        logger.info("ungrounded_number_observed call_id=%s numbers=%s", call_id, bare)
    if not figures:
        return sentence
    logger.warning("ungrounded_figure_withheld call_id=%s mode=%s figures=%s", call_id, mode, figures)
    try:
        session._ungrounded_figures = int(getattr(session, "_ungrounded_figures", 0) or 0) + len(figures)
    except Exception:
        pass
    if mode == "observe":
        return sentence
    spoken = getattr(session, "_spoken_sentences", None) or []
    return "" if spoken and spoken[-1] == UNGROUNDED_FIGURE_LINE else UNGROUNDED_FIGURE_LINE
