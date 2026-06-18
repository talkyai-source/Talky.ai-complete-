"""Self-echo guard for the caller transcript.

On a telephony call with open-mic-during-TTS (``mute_during_tts=False``, kept so
barge-in works) and imperfect carrier echo cancellation, the agent's own TTS
audio can be transcribed back by STT and arrive as a "caller" turn. Observed in
production: a caller turn that was the agent's previous sentence verbatim, with
the real reply tacked on the end —

    Assistant: "...what kind of business are you running?"
    User:      "...what kind of business are you running? I'm running a restaurant."

The agent then answers its own words and the call derails.

``strip_self_echo`` removes a long contiguous run of the agent's own recent
words from the caller transcript. If little real speech remains, the caller-turn
should be ignored entirely. Short backchannels ("yeah", "okay", "no") can never
match a 5+ word run, so they always pass through untouched.

Pure function, no I/O.
"""
from __future__ import annotations

import re

_WORD = re.compile(r"[a-z0-9]+")


def _norm(token: str) -> str:
    """Lowercase + strip non-alphanumerics so "running?" == "running"."""
    return "".join(_WORD.findall(token.lower()))


def strip_self_echo(user_text: str, agent_text: str, *, min_run: int = 5) -> str:
    """Remove an echoed run of the agent's own recent words from the caller text.

    Returns the de-echoed caller transcript, or "" when what remains is too
    short to be real speech (i.e. the whole turn was echo). ``min_run`` is the
    minimum contiguous word-run (after normalisation) that counts as echo — set
    high enough that an ordinary short repeat by a real caller is never stripped.
    """
    if not user_text or not agent_text:
        return user_text or ""

    raw = user_text.split()
    un = [_norm(t) for t in raw]            # 1:1 aligned with raw
    an = [_norm(t) for t in agent_text.split()]
    if len([t for t in un if t]) < min_run or not any(an):
        return user_text                    # too short to hold a meaningful echo

    # Longest common contiguous token run (LCSubstring) between caller + agent.
    best_start = best_len = 0
    prev = [0] * (len(an) + 1)
    for i in range(1, len(un) + 1):
        cur = [0] * (len(an) + 1)
        ui = un[i - 1]
        if ui:                              # never match on empty (punct-only) tokens
            for j in range(1, len(an) + 1):
                if ui == an[j - 1]:
                    cur[j] = prev[j - 1] + 1
                    if cur[j] > best_len:
                        best_len = cur[j]
                        best_start = i - cur[j]
        prev = cur

    if best_len < min_run:
        return user_text                    # no substantial echoed run

    kept = raw[:best_start] + raw[best_start + best_len:]
    # If only stray punctuation / a word or two survive, the turn was pure echo.
    if len([t for t in (_norm(x) for x in kept) if t]) < 2:
        return ""
    return " ".join(kept).strip()
