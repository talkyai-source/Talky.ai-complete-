"""Deliberately deafen the primary STT on one campaign, so the silent-stream
watchdog can be proven on a real call instead of only in unit tests.

WHY THIS EXISTS
---------------
On 2026-08-13, two of thirty-six answered calls were lost because Deepgram Flux
connected, accepted 400+ audio chunks and returned zero transcript events while
the caller talked at RMS 3504. ``resilient_stt._SilentStreamWatchdog`` now
detects exactly that and fails over to Nova-3.

That fix is covered by unit tests, but unit tests exercise the CLASS. They say
nothing about the wiring: whether the orchestrator really builds the wrapper on
a production call, whether Nova-3 really initialises with the tenant's
credential, whether the replayed buffer really reaches it, and whether the
downstream pipeline really keeps the call alive through a mid-stream provider
swap. The only honest way to answer those is to make it happen on a real call.

The alternative — waiting for Flux to go deaf again on its own — is not a test.
It happened on 5.6% of calls once, and the whole point of the fix is that it
should now be invisible when it recurs.

WHY IT IS SHAPED LIKE THIS
--------------------------
Fault injection that reaches production is only acceptable if it cannot go off
by accident, cannot be left on, and cannot silence a call that has nothing to
fall back to. So:

* **Off unless named.** The switch is a campaign UUID, not a boolean. There is
  no value of ``true`` that turns this on for everything — you must name the one
  campaign you are testing, so every other campaign on the box is untouched by
  construction rather than by a conditional someone might get wrong.
* **It expires.** ``VOICE_STT_FAULT_SILENT_UNTIL`` is mandatory. A forgotten env
  var stops mattering on its own; there is no state in which this runs
  indefinitely because somebody did not clean up.
* **It refuses to run without a safety net.** If ``STT_FAILOVER_ENABLED`` is
  off there is no secondary, so deafening the primary would simply destroy the
  call rather than test anything. In that case it declines and logs why.
* **It is loud.** Every activation logs at ERROR with the campaign and call id.
  A fault injector running unnoticed is worse than the fault it simulates.

WHAT IT SIMULATES
-----------------
Precisely the observed failure and nothing more: a provider that connects
successfully, consumes every frame it is given, and never emits an event. It
does not raise, does not close the socket, and does not fail to initialise —
those failure modes already had working failover before this incident, and
simulating them would prove nothing new.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import AsyncIterator, Callable, Optional

from app.domain.interfaces.stt_provider import STTProvider
from app.domain.models.conversation import AudioChunk, TranscriptChunk

logger = logging.getLogger(__name__)

_CAMPAIGN_ENV = "VOICE_STT_FAULT_SILENT_CAMPAIGN"
_UNTIL_ENV = "VOICE_STT_FAULT_SILENT_UNTIL"


class DeafSTTProvider(STTProvider):
    """Wraps a real provider and swallows its transcripts.

    Everything else is forwarded to the wrapped provider — initialise,
    pre-connect, mute, cleanup — so the socket really is opened and really does
    receive audio. Only the transcript stream is suppressed. That fidelity is
    the point: a fake that never connects would exercise a different code path
    from the one that failed in production.
    """

    def __init__(self, inner: STTProvider):
        self._inner = inner
        self.chunks_swallowed = 0

    @property
    def name(self) -> str:
        return f"deaf({self._inner.name})"

    async def initialize(self, config: dict) -> None:
        await self._inner.initialize(config)

    async def cleanup(self) -> None:
        await self._inner.cleanup()

    async def pre_connect(self, call_id: str) -> None:
        fn = getattr(self._inner, "pre_connect", None)
        if fn is not None:
            await fn(call_id)

    async def mute(self, call_id: str) -> None:
        fn = getattr(self._inner, "mute", None)
        if fn is not None:
            await fn(call_id)

    async def unmute(self, call_id: str) -> None:
        fn = getattr(self._inner, "unmute", None)
        if fn is not None:
            await fn(call_id)

    def is_muted(self, call_id: str) -> bool:
        fn = getattr(self._inner, "is_muted", None)
        return bool(fn(call_id)) if fn is not None else False

    async def stream_transcribe(
        self,
        audio_stream: AsyncIterator[AudioChunk],
        language: str = "en",
        context: Optional[str] = None,
        call_id: Optional[str] = None,
        on_eager_end_of_turn: Optional[Callable[[str], None]] = None,
        on_barge_in: Optional[Callable[[], None]] = None,
    ) -> AsyncIterator[TranscriptChunk]:
        """Consume every frame, emit nothing — the production failure verbatim.

        The audio is drained rather than ignored because the watchdog lives in
        the iterator that feeds this call. A wrapper that never pulled from
        ``audio_stream`` would never advance the watchdog and would therefore
        test nothing.
        """
        logger.error(
            "stt_fault_injection_active provider=%s call=%s — this call's PRIMARY "
            "STT is deliberately deaf; expect resilient_stt_stream_silent then a "
            "failover to the secondary",
            self._inner.name, (call_id or "?")[:12],
            extra={"call_id": call_id},
        )
        started = time.monotonic()
        async for _ in audio_stream:
            self.chunks_swallowed += 1
        logger.error(
            "stt_fault_injection_finished provider=%s call=%s chunks_swallowed=%d "
            "elapsed_s=%.1f",
            self._inner.name, (call_id or "?")[:12],
            self.chunks_swallowed, time.monotonic() - started,
            extra={"call_id": call_id},
        )
        return
        yield  # pragma: no cover — makes this an async generator


def _parse_until(raw: str) -> Optional[datetime]:
    """Accept an epoch seconds value or an ISO-8601 timestamp.

    Returns None if it cannot be read, which the caller treats as "do not
    inject" — an unparseable expiry must never be interpreted as "no expiry".
    """
    raw = raw.strip()
    if not raw:
        return None
    try:
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def maybe_deafen_primary(
    primary: STTProvider,
    *,
    campaign_id: Optional[str],
    call_id: Optional[str] = None,
    failover_enabled: bool = False,
    now: Optional[datetime] = None,
) -> STTProvider:
    """Return ``primary``, or a deaf wrapper around it when this exact campaign
    is the one under test and the window is still open.

    Returns the provider unchanged in every other case, including every error
    case. Nothing about this function may take a call down.
    """
    target = (os.getenv(_CAMPAIGN_ENV) or "").strip()
    if not target:
        return primary

    campaign = (campaign_id or "").strip()
    if not campaign or campaign.lower() != target.lower():
        return primary

    raw_until = os.getenv(_UNTIL_ENV) or ""
    until = _parse_until(raw_until)
    if until is None:
        # Deliberately fail CLOSED. Without a readable expiry this would be a
        # permanent production fault, which is the one outcome that must be
        # impossible.
        logger.error(
            "stt_fault_injection_refused campaign=%s reason=bad_expiry raw=%r — "
            "%s must be an epoch or ISO-8601 timestamp; not injecting",
            campaign, raw_until, _UNTIL_ENV,
        )
        return primary

    current = now or datetime.now(timezone.utc)
    if current >= until:
        logger.warning(
            "stt_fault_injection_expired campaign=%s until=%s — window closed, "
            "not injecting (clear %s and %s when convenient)",
            campaign, until.isoformat(), _CAMPAIGN_ENV, _UNTIL_ENV,
        )
        return primary

    if not failover_enabled:
        # Deafening the primary with no secondary behind it does not test the
        # watchdog; it just destroys the call. Refuse.
        logger.error(
            "stt_fault_injection_refused campaign=%s reason=no_failover — "
            "STT_FAILOVER_ENABLED is off, so there is no secondary to promote "
            "and this would silence the call rather than test it",
            campaign,
        )
        return primary

    logger.error(
        "stt_fault_injection_armed campaign=%s call=%s until=%s provider=%s — "
        "PRIMARY STT WILL BE DEAF ON THIS CALL BY DESIGN",
        campaign, (call_id or "?")[:12], until.isoformat(), primary.name,
        extra={"call_id": call_id},
    )
    return DeafSTTProvider(primary)
