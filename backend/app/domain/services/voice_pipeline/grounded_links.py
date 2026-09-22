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

"What the model was given" is the fully assembled per-turn prompt (base prompt,
inline knowledge, retrieved facts, company details) plus anything the knowledge
tool returned this turn. Email addresses and the major mail providers are left
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
    rf"(?P<host>(?:[a-z0-9][a-z0-9{_H}]*\.)+[a-z]{{2,}})"
    rf"(?P<path>/[^\s,;!?\"')\]]*)?",
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
    the caller can log them. ``grounding`` is every piece of text the model was
    given this turn.
    """
    if not text or "." not in text:
        return text, []
    source = _norm(" ".join(g for g in grounding if g))
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
