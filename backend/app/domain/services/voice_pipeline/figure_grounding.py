"""Money and percentages the agent speaks must come from what it was given.

Standard HAL-2 (docs/standards/voice-agent-standards.md). A price a caller
hears is a price they act on. The model is told to state only sourced figures;
this is the cheap check that it did, run on each sentence before TTS:

* a *figure* is a number written next to a currency marker (any Unicode
  currency symbol, an ISO 4217 code, a currency noun) or a percent;
  "5k", "£1.5m" and "1.5 million" are read as their full values;
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
Numbers written as words are not checked.

VOICE_FIGURE_GROUNDING=enforce (default) | observe | off
"""
from __future__ import annotations

import logging
import os
import re
import unicodedata
from decimal import Decimal, InvalidOperation
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


def _numbers(text: str):
    """(match, raw value, full value) for each number; full applies k/m/million."""
    for match in _NUMBER.finditer(text):
        try:
            raw = Decimal(match.group("num").replace(",", ""))
        except InvalidOperation:
            continue
        scale = match.group("suffix") or match.group("scale")
        full = raw * _SCALE[scale.lower()] if scale else raw
        yield match, raw.normalize(), full.normalize()


def grounded_values(sources: Iterable[object]) -> set[Decimal]:
    values: set[Decimal] = set()
    for source in sources:
        if source:
            for _, raw, full in _numbers(str(source)):
                values.add(raw)
                values.add(full)
    return values


Sources = Union[Iterable[object], Callable[[], Iterable[object]]]


def ungrounded_figures(sentence: str, sources: Sources) -> tuple[list[str], list[str]]:
    """Return (figures absent from the grounding, bare numbers >= 10 absent)."""
    found = list(_numbers(sentence or ""))
    if not found:
        return [], []
    known = grounded_values(sources() if callable(sources) else sources)
    figures, bare = [], []
    for match, raw, full in found:
        if full in known:
            continue
        if _is_figure(sentence, match.start(), match.end()):
            figures.append(match.group(0))
        elif full >= 10:
            bare.append(match.group(0))
    return figures, bare


def figure_grounding_mode() -> str:
    mode = os.getenv("VOICE_FIGURE_GROUNDING", "enforce").strip().lower()
    return mode if mode in {"enforce", "observe", "off"} else "enforce"


def guard_figures(session: object, sentence: str, sources: Sources) -> str:
    """What to speak for ``sentence`` under the figure-grounding standard.

    ``sources`` may be a callable so the grounding is only built for a
    sentence that contains a number. Returns "" when the honest line was
    already spoken this turn, so the caller never hears it twice in a row.
    """
    mode = figure_grounding_mode()
    if mode == "off" or not sentence or not any(ch.isdigit() for ch in sentence):
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
