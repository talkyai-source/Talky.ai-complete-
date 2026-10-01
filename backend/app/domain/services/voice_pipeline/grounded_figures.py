"""Validate money against approved source passages, never the system prompt.

English spoken numbers and digits share one check. Preserve currency, billing
period and explicit per-unit restrictions; plain counts/phone numbers are not
financial claims. This is a deterministic guard, not a general fact verifier.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Iterable

UNGROUNDED_FIGURE_REPLACEMENT = (
    "I can't confirm that figure from the information available."
)
_NUM = r"\d[\d,]*(?:\.\d+)?"
_WORD_VALUES = dict(zip(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split(), range(20)
))
_WORD_VALUES.update(dict(zip("twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10))))
_WORD = "(?:" + "|".join([*_WORD_VALUES, "hundred", "thousand", "million", "point", "dot", "and"]) + ")"
_WORD_AMOUNT = rf"{_WORD}(?:[ -]+{_WORD})*"
_AMOUNT = rf"(?:{_NUM}|{_WORD_AMOUNT})"
_MONEY = re.compile(
    rf"(?P<sign>[£$€])\s*(?P<signed>{_NUM})|"
    rf"(?<![\w£$€.,])(?P<amount>{_AMOUNT})\s*(?P<currency>%|per\s?cent\b|percent\b|pounds?\b|quid\b|pence\b|p\b|dollars?\b|euros?\b)", re.I
)
_CURRENCIES = {"£": "GBP", "$": "USD", "€": "EUR", "pound": "GBP", "pounds": "GBP", "quid": "GBP",
               "pence": "GBP_MINOR", "p": "GBP_MINOR", "dollar": "USD", "dollars": "USD",
               "euro": "EUR", "euros": "EUR", "%": "PERCENT", "percent": "PERCENT", "per cent": "PERCENT"}


def _number(text: str) -> Decimal | None:
    text = text.lower().replace(",", "").strip()
    try:
        return Decimal(text)
    except InvalidOperation:
        pass
    tokens = text.replace("-", " ").split()
    total = group = 0
    previous = None
    for i, token in enumerate(tokens):
        if token in ("point", "dot"):
            fraction = tokens[i + 1:]
            if not fraction:
                return None
            if all(_WORD_VALUES.get(t, 10) < 10 for t in fraction):
                digits = "".join(str(_WORD_VALUES[t]) for t in fraction)
            elif len(fraction) <= 2:
                value = _number(" ".join(fraction))
                if value is None or not 0 <= value < 100:
                    return None
                digits = str(value)
            else:
                return None
            return Decimal(f"{total + group}.{digits}")
        if token == "and":
            continue
        if token in _WORD_VALUES:
            value = _WORD_VALUES[token]
            # Reject ambiguous telephone-style runs such as eleven ninety nine.
            if previous is not None and not (previous >= 20 and value < 10):
                return None
            group += value
            previous = value
        elif token == "hundred":
            group = (group or 1) * 100
            previous = None
        elif token in ("thousand", "million"):
            total += (group or 1) * (1000 if token == "thousand" else 1000000)
            group = 0
            previous = None
        else:
            return None
    return Decimal(total + group)


_CLAUSE_BREAK = re.compile(r"(?<!\d)[.!?]|[.!?](?!\d)|[;\n]|\b(?:or|but|while|whereas)\b", re.I)
_AMOUNT_BREAK = re.compile(r",|\band\b", re.I)


def _amount_context(text: str, start: int, end: int) -> tuple[str, str]:
    """Amount-local prefix/tail, without borrowing another price's units.

    Prefix qualifiers matter too: 'Monthly fee: £49' and '£49 per month'
    convey the same billing period. This deliberately handles local clauses,
    not eligibility inference across arbitrary paragraphs.
    """
    left, right = 0, len(text)
    for boundary in _CLAUSE_BREAK.finditer(text):
        if boundary.end() <= start:
            left = boundary.end()
        elif boundary.start() >= end:
            right = boundary.start()
            break
    others = list(_MONEY.finditer(text, left, right))
    previous = [m for m in others if m.end() <= start]
    following = [m for m in others if m.start() >= end]
    if previous:
        joins = list(_AMOUNT_BREAK.finditer(text, previous[-1].end(), start))
        left = joins[-1].end() if joins else start
    if following:
        join = _AMOUNT_BREAK.search(text, end, following[0].start())
        right = join.start() if join else following[0].start()
    return text[left:start].lower(), text[end:right].lower()


def _conditions(text: str, start: int, end: int) -> frozenset[str]:
    prefix, tail = _amount_context(text, start, end)
    context = prefix + " " + tail
    result = set()
    for period, adjective in (("day", "daily"), ("week", "weekly"), ("month", "monthly"), ("year", "yearly|annual|annually")):
        if re.search(rf"\b(?:(?:per|a|each|every)\s+{period}|{adjective})\b", context):
            result.add(period)
    for unit in ("location", "user", "seat", "transaction", "device"):
        if re.search(rf"\b(?:per|each|a)\s+{unit}\b", context):
            result.add(unit)
    if re.search(r"\b(?:upfront|one[- ]time|one[- ]off)\b", context):
        result.add("upfront")
    if re.search(r"\b(?:excluding|excludes|plus|before)\s+(?:vat|tax)\b", context):
        result.add("excludes_tax")
    if re.search(r"\b(?:including|includes)\s+(?:vat|tax)\b", context):
        result.add("includes_tax")
    return frozenset(result)


def _negated_amount(text: str, start: int, end: int) -> bool:
    prefix, tail = _amount_context(text, start, end)
    # Do not promote a rejected/example price into approved positive evidence.
    # Bound this to the amount's own clause so 'not £10 but £20' keeps £20.
    return bool(
        re.search(r"\b(?:not|never|no\s+longer|used\s+to|formerly|previously)\b[^,;:.!?]{0,60}$", prefix)
        or re.search(r"\bno\s+$", prefix)
        or re.match(r"\s*(?:(?:is|was|would\s+be)\s+)?(?:not\b|incorrect\b|wrong\b|outdated\b|unavailable\b)", tail)
    )


def _figures(text: str):
    for match in _MONEY.finditer(text):
        value = _number(match.group("signed") or match.group("amount"))
        currency = _CURRENCIES[match.group("sign") or match.group("currency").lower()]
        if currency == "GBP_MINOR":
            currency = "GBP"
            value = value / 100 if value is not None else None
        yield match, value, currency, _conditions(text, match.start(), match.end())


def ground_spoken_figures(text: str, grounding: Iterable[str]) -> tuple[str, list[str]]:
    """Return safe speech and unsupported claims; never derive or sum prices."""
    if not text:
        return text, []
    approved = [(value, currency, conditions) for source in grounding if source
                for match, value, currency, conditions in _figures(str(source))
                if value is not None and not _negated_amount(str(source), match.start(), match.end())]
    unsupported = []
    replacements = []
    for match, value, currency, conditions in _figures(text):
        candidates = [(c, u) for v, c, u in approved if v == value]
        if value is not None and (currency, conditions) in candidates:
            continue
        # A unique symbol typo can be corrected deterministically. A spoken
        # currency change or a changed/missing billing unit is withheld.
        currencies = {c for c, u in candidates if u == conditions}
        sign_for = {"GBP": "£", "USD": "$", "EUR": "€"}
        if match.group("sign") and len(currencies) == 1 and next(iter(currencies)) in sign_for:
            replacements.append((match.start(), match.start() + 1, sign_for[next(iter(currencies))]))
        else:
            unsupported.append(match.group(0))
    if unsupported:
        return UNGROUNDED_FIGURE_REPLACEMENT, unsupported
    for start, end, value in reversed(replacements):
        text = text[:start] + value + text[end:]
    return text, []
