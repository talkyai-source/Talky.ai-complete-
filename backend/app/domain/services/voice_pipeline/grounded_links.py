"""Extract supplied URL hosts solely for streaming punctuation boundaries."""
from __future__ import annotations

import re
from typing import Iterable

_HYPHENS = "‐‑‒–—―−"
_H = "\\-" + _HYPHENS

_URL_RE = re.compile(
    rf"(?<![@\w.{_H}])"                       # not the tail of an email/word
    rf"(?:https?://)?(?:www\.)?"
    # The host ends at a real boundary: without this the email exemption
    # below just backtracked to a shorter "host" ("john.cen").
    rf"(?P<host>(?:[a-z0-9][a-z0-9{_H}]*\.)+[a-z]{{2,}})(?![a-z0-9{_H}])"
    rf"(?P<path>/[^\s,;!?\"')\]]*)?"
    # ...and not the NAME part of an email: "john.cena at gmail dot com" or
    # "john.cena@gmail.com". Test call 1436672a (2026-09-29) had "john.cena"
    # rewritten to "our website" mid read-back. A real site followed by an
    # ordinary "at" ("...co.uk at any time") is still checked.
    r"(?!\s*@|\s+at\s+(?:the\s+rate\s+)?[a-z0-9][a-z0-9\s-]{0,30}?(?:\s+dot\s+|\.)[a-z])",
    re.IGNORECASE,
)


def _norm(text: str) -> str:
    out = str(text or "").lower()
    for ch in _HYPHENS:
        out = out.replace(ch, "-")
    return out


def _clean(host: str, path: str) -> tuple[str, str]:
    host = _norm(host).strip(".")
    path = _norm(path or "").rstrip("./")
    return host, path


def grounded_url_hosts(grounding: Iterable[str]) -> frozenset[str]:
    """Extract URL hosts from supplied passages for sentence segmentation.

    These hosts only disambiguate streaming punctuation; they do not approve
    a generated address or rewrite model speech.
    """
    return frozenset(
        _clean(match.group("host"), "")[0]
        for passage in grounding or ()
        for match in _URL_RE.finditer(str(passage or ""))
    )
