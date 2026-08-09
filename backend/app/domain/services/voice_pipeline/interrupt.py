"""ONE idempotent operation that stops the agent talking, and proves it did.

WHY THIS EXISTS (2026-08-08)
----------------------------
Stopping playback used to be a sequence of independent best-effort steps spread
across `handle_barge_in`, `clear_output_buffer` and `interrupt_tts`. Each step
logged (or swallowed) its own outcome, and nobody assembled a verdict. Two
consequences:

  * Python declared the agent stopped the moment its own state said LISTENING.
    That is a statement about a local variable, not about audio in the caller's
    ear — the C++ gateway could still be draining a queue.
  * The chain could not be traced. A production incident needed
    "caller audio -> StartOfTurn -> echo decision -> barge-in -> state ->
    LLM/TTS cancelled -> Python buffer cleared -> C++ interrupt -> queue
    cleared", and the second half of that was simply never written down.

So: one function, one `interrupt_id`, one log line per step carrying that id,
and one `InterruptResult` that says whether the agent actually stopped and how
much audio was binned. Grep a call by its interrupt_id and the whole chain is
there in order.

WHAT MAKES IT IDEMPOTENT
------------------------
Duplicate StartOfTurn / barge-in events are normal — both arming sites fire for
the same utterance, and Flux can emit several. Re-running the full teardown for
each would rotate the utterance id repeatedly and re-cancel tasks mid-unwind.
So a completed interrupt is remembered on the session for
``_INTERRUPT_DEDUPE_S``; a repeat inside that window returns the FIRST result
tagged ``deduped=True`` without touching the session again.

The dedupe window is deliberately short. It must cover the duplicate-event
burst, and must NOT cover a caller who barges in again a moment later — that
second interruption is real and must do real work.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Duplicate barge-in events for ONE utterance arrive within a few tens of ms
# (the two arming sites are microseconds apart). 0.35s covers that burst with
# margin while staying well under a human's re-interruption time.
_INTERRUPT_DEDUPE_S = 0.35


@dataclass
class InterruptResult:
    """The acknowledgement: did the agent stop, and what did we throw away?"""

    interrupt_id: str
    call_id: str
    ok: bool = False
    deduped: bool = False
    state_changed: bool = False
    task_cancelled: bool = False
    tts_queue_cleared: bool = False
    utterance_rotated: bool = False
    local_bytes_discarded: int = 0
    pending_bytes: int = 0
    gateway_dropped_frames: int = 0
    gateway_interrupted_segments: int = 0
    gateway_attempts: int = 0
    elapsed_ms: float = 0.0
    errors: list = field(default_factory=list)

    @property
    def gateway_dropped_ms(self) -> int:
        """Discarded gateway audio as duration — 20ms per PCMU frame."""
        return self.gateway_dropped_frames * 20

    def as_log(self) -> Dict[str, Any]:
        return {
            "interrupt_id": self.interrupt_id,
            "ok": self.ok,
            "deduped": self.deduped,
            "task_cancelled": self.task_cancelled,
            "local_bytes": self.local_bytes_discarded,
            "gw_frames": self.gateway_dropped_frames,
            "gw_ms": self.gateway_dropped_ms,
            "gw_segments": self.gateway_interrupted_segments,
            "gw_attempts": self.gateway_attempts,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "errors": self.errors,
        }


def _log_step(interrupt_id: str, call_id: str, step: str, **fields: Any) -> None:
    """One line per step of the cancellation chain, all sharing interrupt_id.

    This is the instrumentation that makes an end-to-end trace possible:
        journalctl -u talky-api | grep interrupt_id=<id>
    """
    extra = " ".join(f"{k}={v}" for k, v in fields.items())
    logger.info(
        "interrupt_step=%s interrupt_id=%s call=%s %s",
        step, interrupt_id, call_id[:12], extra,
    )


async def interrupt_playback(
    session,
    *,
    media_gateway,
    reason: str,
    cancel_task=None,
    tts_provider=None,
) -> InterruptResult:
    """Stop the agent speaking. Idempotent. Returns a verdict, never None.

    Steps, in this order and all logged under one ``interrupt_id``:
      1. mark barge-in + switch the conversation to LISTENING
      2. cancel the in-flight LLM/TTS task (caller supplies the coroutine)
      3. clear the Python TTS + partial-packet buffers
      4. POST /v1/sessions/tts/interrupt -> C++ clears its queue, rotates the
         utterance id, and 409s every late chunk of the dead generation
      5. tell the TTS provider to stop generating server-side

    ``ok`` is the GATEWAY's verdict. Python's own state changing is necessary
    but not sufficient — the caller's ear is downstream of the C++ queue.
    """
    call_id = str(getattr(session, "call_id", "?"))
    now = time.monotonic()

    # ── Idempotency: a repeat inside the window returns the first verdict ──
    prior: Optional[InterruptResult] = getattr(session, "_last_interrupt", None)
    prior_at = getattr(session, "_last_interrupt_at", None)
    if prior is not None and prior_at is not None and (now - prior_at) < _INTERRUPT_DEDUPE_S:
        _log_step(prior.interrupt_id, call_id, "deduped",
                  reason=reason, age_ms=round((now - prior_at) * 1000, 1))
        duplicate = InterruptResult(**{**prior.__dict__, "deduped": True})
        return duplicate

    interrupt_id = uuid.uuid4().hex[:12]
    result = InterruptResult(interrupt_id=interrupt_id, call_id=call_id)
    started = now
    _log_step(interrupt_id, call_id, "begin", reason=reason,
              tts_active=getattr(session, "tts_active", None))

    # ── 1. state ──────────────────────────────────────────────────────────
    try:
        from app.domain.models.session import CallState

        session.tts_active = False
        session.state = CallState.LISTENING
        session.current_ai_response = ""
        session.current_user_input = ""
        result.state_changed = True
        _log_step(interrupt_id, call_id, "state_listening")
    except Exception as exc:  # noqa: BLE001 — never let bookkeeping stop the stop
        result.errors.append(f"state:{exc}")
        logger.warning("interrupt %s: state change failed: %s", interrupt_id, exc)

    # ── 2. cancel the in-flight turn ──────────────────────────────────────
    if cancel_task is not None:
        try:
            cancelled = await cancel_task()
            result.task_cancelled = bool(cancelled)
            _log_step(interrupt_id, call_id, "task_cancelled",
                      cancelled=result.task_cancelled)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"cancel:{exc}")
            logger.warning("interrupt %s: task cancel failed: %s", interrupt_id, exc)

    # ── 3+4. Python buffers, then the C++ queue (one call does both) ──────
    try:
        ack = await media_gateway.clear_output_buffer(call_id)
        if isinstance(ack, dict):
            result.local_bytes_discarded = int(ack.get("local_bytes_discarded") or 0)
            result.pending_bytes = int(ack.get("pending_bytes") or 0)
            result.tts_queue_cleared = True
            gw = ack.get("gateway") or {}
            result.ok = bool(ack.get("ok"))
            result.gateway_dropped_frames = int(gw.get("dropped_frames") or 0)
            result.gateway_interrupted_segments = int(gw.get("interrupted_segments") or 0)
            result.gateway_attempts = int(gw.get("attempts") or 0)
            result.utterance_rotated = bool(gw.get("utterance_rotated"))
            if gw.get("error"):
                result.errors.append(f"gateway:{gw['error']}")
            _log_step(interrupt_id, call_id, "buffers_cleared",
                      local_bytes=result.local_bytes_discarded,
                      pending_bytes=result.pending_bytes)
            _log_step(interrupt_id, call_id, "cpp_interrupt",
                      ok=result.ok, dropped_frames=result.gateway_dropped_frames,
                      dropped_ms=result.gateway_dropped_ms,
                      segments=result.gateway_interrupted_segments,
                      attempts=result.gateway_attempts,
                      rotated=result.utterance_rotated)
        else:
            # Gateway predates the acknowledgement contract (browser/other).
            result.tts_queue_cleared = True
            result.ok = True
            _log_step(interrupt_id, call_id, "buffers_cleared", ack="none")
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"clear:{exc}")
        logger.error(
            "interrupt %s call=%s: clear_output_buffer FAILED — the agent may "
            "still be audible: %s", interrupt_id, call_id[:12], exc,
        )

    # ── 5. stop the TTS provider generating more ──────────────────────────
    clear_queue = getattr(tts_provider, "clear_queue", None) if tts_provider else None
    if clear_queue:
        try:
            await clear_queue()
            _log_step(interrupt_id, call_id, "tts_provider_cleared")
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"tts_clear:{exc}")
            logger.debug("interrupt %s: tts clear_queue failed: %s", interrupt_id, exc)

    result.elapsed_ms = (time.monotonic() - started) * 1000.0

    # A failed interrupt is the loudest failure this system has — it means the
    # caller is still being talked over. Never let it sit at debug.
    if result.ok:
        logger.info("interrupt_complete %s", result.as_log())
    else:
        logger.error(
            "interrupt_FAILED call=%s session_state_says_stopped_but_gateway_did_not "
            "%s", call_id[:12], result.as_log(),
        )

    try:
        from app.infrastructure.metrics.voice_metrics import record_interrupt_outcome

        record_interrupt_outcome(
            ok=result.ok,
            dropped_frames=result.gateway_dropped_frames,
            attempts=result.gateway_attempts,
        )
    except Exception:  # noqa: BLE001 — metrics must never break the audio path
        pass

    session._last_interrupt = result
    session._last_interrupt_at = time.monotonic()
    return result
