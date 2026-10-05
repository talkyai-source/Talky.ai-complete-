"""Select complete, query-relevant source passages within a voice budget."""
from __future__ import annotations

import re

_STOPWORDS = frozenset(
    "a an the and or but if then so of to in on at by for with from about as into "
    "is are was were be been being am do does did done have has had having can could "
    "will would shall should may might must i me my mine we us our you your yours he "
    "him his she her it its they them their this that these those there here what "
    "which who whom whose when where why how not no yes yeah ok okay please just "
    "also very really any some all more most much many than too up out tell know".split()
)

# A sentence referring back to the fact immediately before it cannot be
# excerpted independently, or silently dropped from that fact's price/terms.
_REFERS_BACK = re.compile(
    r"^(?:this|that|these|those|it|they)\b", re.I
)
_CONDITION = re.compile(
    r"\b(?:only|except|exclud\w*|includ\w*|unless|subject to|provided|"
    r"eligible|eligibility|additional|separately|however|does not|not cover|"
    r"requir\w*|minimum|must|mandatory)\b", re.I
)


def content_words(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(t) > 1 and t not in _STOPWORDS]


def select_passage(source: str, query: str, limit: int) -> str:
    """Keep the matching sentence and adjacent qualifications, never half a fact.

    Oversized indivisible sentences are withheld. No summary can substitute for
    source evidence. Neighbouring conditions travel with the chosen sentence.
    """
    source = (source or "").strip()
    if limit <= 0 or not source:
        return ""
    if len(source) <= limit:
        return source
    units = [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", source) if p.strip()]
    terms = set(content_words(query))
    ranked = sorted(range(len(units)), key=lambda i: (
        -len(terms.intersection(content_words(units[i]))), i,
    ))
    if terms and not any(terms.intersection(content_words(s)) for s in units):
        return ""
    for index in ranked:
        if terms and not terms.intersection(content_words(units[index])):
            continue
        # Bind nearby exceptions, eligibility and qualifiers to their fact.
        start, end = index, index + 1
        while start > 0 and _REFERS_BACK.match(units[start]):
            start -= 1
        if start > 0 and _CONDITION.search(units[start - 1]):
            start -= 1
        while end < len(units) and (
            _CONDITION.search(units[end]) or _REFERS_BACK.match(units[end])
        ):
            end += 1
        group = " ".join(units[start:end])
        if len(group) <= limit:
            # Preserve context when space permits; never displace the answer.
            if start > 0 and len(units[start - 1]) + len(group) + 1 <= limit:
                group = units[start - 1] + " " + group
            return group
    return ""
