"""Never speak a web address the agent was not given.

Production, call 2427af7e (2026-09-22). The caller asked for a sample of the
company's work. The agent replied:

    "Here's a sample report page: allstateestimation.co.uk/sample-reports -
     does that cover what you need?"

The campaign's 27 knowledge nodes contain one URL -- the bare domain
``allstateestimation.co.uk``. The ``/sample-reports`` path appears nowhere in
anything the model was given. It invented a link and handed it to a caller as
real, which is worse than saying nothing: the caller goes to a page that may
not exist and blames the company.

The rule is deterministic and applied to every piece of text before it reaches
TTS, and to what is stored in history:

* the full address appears in what the model was given   -> spoken as written
* only its host does                                       -> the host alone
* the host was never given at all                          -> "our website"

Grounding consists of the factual company-knowledge passages and knowledge
tool results supplied this turn. Instructions and policies are not evidence
that a resource exists. Email addresses and the major mail providers are left
alone: a read-back of the caller's own address is not a claim about the
company.
"""
from __future__ import annotations

import re
from typing import Iterable

# Speech text often carries typographic hyphens (U+2010..U+2015, U+2212); the
# model wrote "sample‑reports" with a NON-BREAKING hyphen on the real call.
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

# A read-back of the caller's own address ("john at gmail.com") is not a claim
# about the company and must never be rewritten.
_MAIL_PROVIDERS = {
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.co.uk",
    "outlook.com", "live.com", "yahoo.com", "yahoo.co.uk", "icloud.com",
    "me.com", "aol.com", "protonmail.com", "proton.me", "btinternet.com",
}

UNGROUNDED_REPLACEMENT = "our website"
UNAVAILABLE_RESOURCE = "I can't confirm an available download link."
_RESOURCE_OFFER = re.compile(
    r"\b(?:(?:i|we)(?:\s+(?:can|could|will)|'ll)|would\s+you\s+like\s+me\s+to)"
    r"\s+(?:give|provide|share|send|read)\b[^.!?]{0,90}\b(?:link|download)\b",
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


def ground_spoken_links(text: str, grounding: Iterable[str]) -> tuple[str, list[str]]:
    """Rewrite every ungrounded web address in ``text``.

    Returns the text to speak and a list of the addresses that were changed, so
    the caller can log them. ``grounding`` contains supplied factual passages,
    never the assembled system prompt or policy instructions.
    """
    if not text:
        return text, []
    passages = [_norm(g) for g in grounding if g]
    source = " ".join(passages)
    # A model can invent an available resource without spelling a URL yet.
    # An offer to download must have both a supplied address and download
    # context; a bare company homepage does not prove a brochure exists.
    if _RESOURCE_OFFER.search(text.replace("’", "'")):
        resources = [word for word in ("download", "brochure", "sample", "report")
                     if re.search(r"\b" + word + r"\b", text, re.I)]
        supplied_resource = any(
            _URL_RE.search(passage)
            and all(re.search(r"\b" + word + r"\b", passage) for word in resources)
            for passage in passages
        )
        if not supplied_resource:
            return UNAVAILABLE_RESOURCE, ["unavailable_resource_offer"]
    if "." not in text:
        return text, []
    changed: list[str] = []

    def _replace(match: re.Match) -> str:
        whole = match.group(0)
        host, path = _clean(match.group("host"), match.group("path"))
        if host in _MAIL_PROVIDERS:
            return whole
        # Sentence punctuation the pattern swallowed stays in the sentence.
        tail = whole[len(whole.rstrip("./")):] if match.group("path") else ""
        if path and f"{host}{path}" in source:
            return whole
        if not path and host in source:
            return whole
        if host in source:
            changed.append(f"{host}{path}")
            return match.group("host").strip(".") + tail
        changed.append(f"{host}{path}")
        return UNGROUNDED_REPLACEMENT + tail

    return _URL_RE.sub(_replace, text), changed
