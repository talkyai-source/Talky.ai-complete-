"""Never speak a price, fee or percentage the agent was not given.

Production, call d644f0ea (2026-09-28, Dojo-PC). The caller asked "so what's
the total, twenty-one ninety-nine?" and the agent answered "That comes to
£21.99 a month." The knowledge gives £11.99 per location per month for the plan
and £10 per month for the add-on, and says in so many words that the two are
quoted separately. £21.99 appears in nothing the model was given: it added two
figures with different units and presented the sum as a price.

A price the agent makes up is a promise the business did not make, on every
campaign that talks about money. The rule is deterministic and runs on every
sentence before it reaches TTS, next to the web-address check:

* the figure appears in what the model was given     -> spoken as written
* it does, but only under another currency sign      -> the given sign is used
  (a generated knowledge summary on the same campaign said "$11.99" where the
  source says "£11.99")
* it appears nowhere                                  -> the sentence is replaced
  with a line that promises to get the figure confirmed

"What the model was given" is the assembled per-turn prompt (script, persona,
retrieved knowledge) plus anything a knowledge tool returned. The caller's own
words are deliberately NOT a source: repeating a caller's guess back as a price
is exactly the failure above.
"""
from __future__ import annotations

import re
from typing import Iterable

UNGROUNDED_FIGURE_REPLACEMENT = (
    "I'd rather not give you a figure I can't confirm, so I'll make sure that's "
    "checked for you."
)

_NUM = r"\d[\d,]*(?:\.\d+)?"
_CURRENCY_SIGNS = "£$€"

# "£21.99", "$ 11.99", "€1,000"
_SIGNED = re.compile(rf"(?P<sign>[{_CURRENCY_SIGNS}])\s?(?P<num>{_NUM})")
# "21.99 pounds", "20p", "15 percent", "1.5%"
_SUFFIXED = re.compile(
    rf"(?<![\w{_CURRENCY_SIGNS}.,])(?P<num>{_NUM})\s?"
    r"(?P<unit>%|per\s?cent\b|percent\b|pounds?\b|quid\b|pence\b|p\b|dollars?\b|euros?\b)",
    re.IGNORECASE,
)
_ANY_NUMBER = re.compile(_NUM)


def _value(num: str) -> str:
    """A figure's value as a comparable string: '1,000.00' -> '1000'."""
    cleaned = num.replace(",", "").rstrip(".")
    if "." in cleaned:
        cleaned = cleaned.rstrip("0").rstrip(".")
    return cleaned or "0"


def _source_figures(grounding: Iterable[str]) -> tuple[set[str], dict[str, set[str]]]:
    source = " ".join(str(g) for g in grounding if g)
    values = {_value(m.group(0)) for m in _ANY_NUMBER.finditer(source)}
    signs: dict[str, set[str]] = {}
    for m in _SIGNED.finditer(source):
        signs.setdefault(_value(m.group("num")), set()).add(m.group("sign"))
    return values, signs


def ground_spoken_figures(text: str, grounding: Iterable[str]) -> tuple[str, list[str]]:
    """Check every money amount and percentage in ``text`` against ``grounding``.

    Returns the text to speak and the figures that had no source (empty when
    the text is unchanged or only had a currency sign corrected).
    """
    if not text or not any(ch.isdigit() for ch in text):
        return text, []
    values, signs = _source_figures(grounding)
    ungrounded: list[str] = []

    def _fix_sign(match: re.Match) -> str:
        sign, num = match.group("sign"), match.group("num")
        value = _value(num)
        if value not in values:
            ungrounded.append(match.group(0))
            return match.group(0)
        given = signs.get(value)
        if given and sign not in given and len(given) == 1:
            return next(iter(given)) + match.group(0)[1:]
        return match.group(0)

    checked = _SIGNED.sub(_fix_sign, text)
    for match in _SUFFIXED.finditer(checked):
        if _value(match.group("num")) not in values:
            ungrounded.append(match.group(0))

    if ungrounded:
        return UNGROUNDED_FIGURE_REPLACEMENT, ungrounded
    return checked, []
