"""First-speaker dispatcher for the telephony bridge.

Resolves which mode handler should run on a given ``voice_session``:

1. Per-call: ``voice_session._first_speaker``, set by ``make_call`` from the
   campaign's ``first_speaker`` value (which travels via the dialer worker's
   query param).
2. Fallback: env var ``TELEPHONY_FIRST_SPEAKER``, handled by
   :func:`app.domain.services.telephony.config._outbound_first_speaker`.
3. Clamp to ``{"agent", "user"}``; anything else collapses to ``"agent"``.

The mode handlers themselves live in
:mod:`app.domain.services.telephony.modes.agent_first` and
:mod:`app.domain.services.telephony.modes.user_first`.
"""
from __future__ import annotations

from typing import Literal

from app.domain.services.telephony.config import _outbound_first_speaker


def resolve_first_speaker(voice_session) -> Literal["agent", "user"]:
    """Return ``"agent"`` or ``"user"`` for this session, never anything else."""
    raw = getattr(voice_session, "_first_speaker", None) or _outbound_first_speaker()
    value = (raw or "").strip().lower()
    return "user" if value == "user" else "agent"
