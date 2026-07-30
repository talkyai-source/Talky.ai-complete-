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


def strip_self_echo_multi(
    user_text: str, agent_texts, *, min_run: int = 5
) -> str:
    """Strip echoes of SEVERAL recent agent utterances from one caller turn.

    WHY THIS EXISTS (regression, 2026-07-31)
    ----------------------------------------
    The single-utterance form silently assumed exactly ONE agent utterance
    precedes each caller turn. That held until the spoken recording disclosure
    was added: the agent now says the notice AND then the greeting before the
    callee has said anything, so by the caller's first turn the disclosure is
    two messages back and invisible to a guard that only looks at the most
    recent one.

    The observed result, from a production transcript:

        User: This call may be recorded.        <- the agent's own notice
        Assistant: happy to continue. ...       <- agent answers itself
        User: Happy to continue. ...            <- and that echoes too

    The caller never spoke; the call was derailed from turn 0.

    ``agent_texts`` should be every agent utterance since the caller last
    spoke — that is exactly the set of audio still capable of echoing into
    this turn, and it is self-limiting (normally 1, two on the opening turn,
    three when a silence nudge intervened). Bounding it that way rather than
    by a fixed count is what keeps a caller who legitimately repeats the
    agent's wording from a minute ago being stripped.

    Applied most-recent-first, feeding each result forward, so a turn that
    echoes two separate utterances is fully cleaned. Returns "" when nothing
    real survives.
    """
    text = user_text or ""
    for agent_text in agent_texts or ():
        if not text.strip():
            return ""
        if not agent_text:
            continue
        text = strip_self_echo(text, agent_text, min_run=min_run)
    return text
