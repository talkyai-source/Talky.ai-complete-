"""Recording of the person-to-caller conversation after a supervised transfer.

At the handover the AI part of the call is mixed and parked in the durable
recording spool (``awaiting="transfer_part"``).  Asterisk records the bridge
while the person talks to the caller.  When the call ends,
``finalize_transfer_entry`` fetches that file, appends it to the AI part as one
stereo 8 kHz WAV, writes it back to the spool and hands the entry to the normal
save path.  Nothing here may raise (except cancellation): a failure leaves the
entry awaiting so the spool retry loop comes back to it.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import wave
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_ENV_NAME = "TELEPHONY_TRANSFER_MAX_CALL_DURATION_S"
_DEFAULT_S = 3600
_MIN_S = 600
_MAX_S = 14400
_OUT_RATE = 8000
_NO_ADAPTER_GRACE_S = 900.0
# How many times the stored file is asked for before the transfer part is
# given up as missing. The live path asks once at the end of the call; the
# retry loop asks about once a minute after that.
_FETCH_TRIES = 6
# Keys being finalised right now, so the end of the call and the retry loop
# never join the same entry twice.
_FINALIZING: set = set()


def transfer_max_duration_seconds() -> int:
    """Longest a transferred call may run; read at call time, clamped 600..14400."""
    raw = os.getenv(_ENV_NAME)
    if raw is None or not str(raw).strip():
        return _DEFAULT_S
    try:
        value = int(float(str(raw).strip()))
    except (TypeError, ValueError, OverflowError):
        return _DEFAULT_S
    return max(_MIN_S, min(_MAX_S, value))


def _read_wav(data: bytes):
    """Return (int16 array shaped (frames, channels), rate). Raises ValueError."""
    import numpy as np

    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            channels = wf.getnchannels()
            width = wf.getsampwidth()
            rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
    except Exception as exc:  # wave.Error, EOFError, struct.error
        raise ValueError(f"unreadable_wav:{type(exc).__name__}") from exc
    if width != 2 or channels < 1 or rate <= 0:
        raise ValueError("unsupported_wav_format")
    usable = len(frames) - (len(frames) % (2 * channels))
    arr = np.frombuffer(frames[:usable], dtype="<i2").reshape(-1, channels)
    return arr, int(rate)


def _to_rate(arr, rate: int):
    """Resample (frames, channels) int16 to 8 kHz; vectorised, no Python loops."""
    import numpy as np

    if rate == _OUT_RATE or arr.shape[0] == 0:
        return arr
    import soxr

    out = soxr.resample(arr.astype(np.float32) / 32768.0, rate, _OUT_RATE, quality="HQ")
    out = np.asarray(out).reshape(-1, arr.shape[1])
    return np.clip(np.rint(out * 32768.0), -32768, 32767).astype(np.int16)


def append_transfer_part(ai_wav: Optional[bytes], part_wav: bytes) -> tuple[bytes, float]:
    """One stereo 8 kHz WAV: AI part (caller L / agent R) then the transfer part
    on both channels. Returns (wav_bytes, duration_seconds)."""
    import numpy as np

    part, part_rate = _read_wav(part_wav)
    mono = part.astype(np.int32).mean(axis=1) if part.shape[1] > 1 else part[:, 0]
    mono = np.rint(mono).astype(np.int16).reshape(-1, 1)
    mono = _to_rate(mono, part_rate)
    part_stereo = np.repeat(mono, 2, axis=1)

    ai_stereo = None
    if ai_wav:
        try:
            ai, ai_rate = _read_wav(ai_wav)
            if ai.shape[1] == 1:
                ai = np.repeat(ai, 2, axis=1)
            elif ai.shape[1] > 2:
                ai = ai[:, :2]
            ai_stereo = _to_rate(ai, ai_rate)
            if ai_stereo.shape[0] == 0:
                ai_stereo = None
        except ValueError:
            ai_stereo = None

    joined = part_stereo if ai_stereo is None else np.concatenate([ai_stereo, part_stereo], axis=0)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(_OUT_RATE)
        wf.writeframes(np.ascontiguousarray(joined, dtype="<i2").tobytes())
    return buf.getvalue(), joined.shape[0] / float(_OUT_RATE)


def _ai_duration(entry) -> float:
    try:
        return float(entry.duration_seconds or 0.0)
    except (TypeError, ValueError):
        return 0.0


async def finalize_transfer_entry(entry, *, adapter: Any, voice_session: Any = None) -> str:
    """Join the transfer part to the spooled AI part and save. Never raises
    (except cancellation); on an unexpected error returns "retry" with the
    entry still awaiting."""
    call = str(getattr(entry, "pbx_call_id", "") or "")[:12]
    key = getattr(entry, "key", None)
    if key in _FINALIZING:
        return "retry"
    _FINALIZING.add(key)
    try:
        return await _finalize_locked(entry, adapter=adapter, voice_session=voice_session, call=call)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - the retry loop comes back to it
        logger.error(
            "transfer_recording_finalize_failed call=%s err=%s", call, type(exc).__name__
        )
        try:
            entry.awaiting = "transfer_part"
        except Exception:  # noqa: BLE001
            pass
        return "retry"
    finally:
        _FINALIZING.discard(key)


async def _finalize_locked(entry, *, adapter: Any, voice_session: Any, call: str) -> str:
    from app.domain.services import recording_spool as spool
    from app.domain.services.telephony.recording import process_spool_entry

    name = entry.transfer_recording_name
    part_wav: Optional[bytes] = None
    reason = "not_started"
    if name and adapter is None:
        reason = "no_adapter"
    elif name:
        await adapter.stop_transfer_recording(name)
        part_wav = await adapter.fetch_stored_recording(name)
        reason = "not_found"

    ai_wav = await asyncio.to_thread(spool.read_wav, entry.key)

    final_wav = ai_wav
    joined = False
    if part_wav:
        try:
            final_wav, seconds = await asyncio.to_thread(append_transfer_part, ai_wav, part_wav)
            joined = True
        except ValueError:
            reason = "unreadable"

    if not joined and reason in ("not_found", "no_adapter"):
        # The phone system may still be writing the file, or could not be
        # asked. Leave the entry awaiting and ask again, a few times,
        # before the transfer part is given up as missing.
        tries = int(getattr(entry, "attempts", 0) or 0) + 1
        if tries < _FETCH_TRIES:
            entry.attempts = tries
            entry.last_error = "transfer_part_" + reason
            entry.last_attempt_at = spool.utc_now_iso()
            await asyncio.to_thread(spool.update_entry, entry)
            logger.warning(
                "transfer_recording_not_ready call=%s reason=%s tries=%d",
                call, reason, tries,
            )
            return "retry"

    # The counter above counted fetches; the save starts from zero.
    entry.attempts = 0
    entry.last_error = None
    entry.last_attempt_at = None
    if joined:
        part_seconds = max(0.0, seconds - (_ai_duration(entry) if ai_wav else 0.0))
        entry.transfer_part = "included"
        entry.sample_rate = _OUT_RATE
        entry.duration_seconds = seconds
        logger.info(
            "transfer_recording_included call=%s part_seconds=%.1f", call, part_seconds
        )
    else:
        entry.transfer_part = "missing"
        logger.error("transfer_recording_missing call=%s reason=%s", call, reason)
    entry.awaiting = None

    if final_wav is None:
        # No AI audio at all and no transfer part: nothing to write back.
        await asyncio.to_thread(spool.update_entry, entry)
    else:
        await asyncio.to_thread(spool.write_entry, entry, final_wav)

    if name and adapter is not None:
        if joined:
            # Only now: the pending folder holds the whole conversation.
            await adapter.delete_stored_recording(name)
        # A part that was never joined stays in the phone system's own
        # folder, so a person can still recover it.
        adapter.forget_transfer_recording(entry.pbx_call_id)

    return await process_spool_entry(entry, wav_bytes=final_wav, voice_session=voice_session)


def _live_session_exists(pbx_call_id: str) -> bool:
    from app.domain.services.telephony.lifecycle import _state

    return _state().get_voice_session(pbx_call_id) is not None


def _entry_age_s(entry) -> Optional[float]:
    from app.domain.services.recording_spool import parse_iso

    created = parse_iso(getattr(entry, "created_at", None))
    if created is None:
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - created).total_seconds()


async def finalize_awaiting_entry(entry) -> None:
    """Retry-loop hook for entries still awaiting their transfer part."""
    try:
        if _live_session_exists(entry.pbx_call_id):
            return
        from app.domain.services.telephony.adapter_registry import get_adapter

        adapter = get_adapter()
        if adapter is not None and not getattr(adapter, "connected", True):
            # The phone system cannot be asked right now: do not spend a try.
            adapter = None
        if adapter is None:
            age = _entry_age_s(entry)
            if age is None or age < transfer_max_duration_seconds() + _NO_ADAPTER_GRACE_S:
                return
        await finalize_transfer_entry(entry, adapter=adapter)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "transfer_recording_finalize_failed call=%s err=%s",
            str(getattr(entry, "pbx_call_id", ""))[:12],
            type(exc).__name__,
        )
