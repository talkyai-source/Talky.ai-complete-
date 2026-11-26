"""Live checkpoint of call audio, so a crash does not lose a call in progress.

Until a call ends its audio exists only in memory (``recording_buffer`` and
``tts_recording_buffer`` of the media gateway session). This module copies the
NEW audio of every live outbound call to ``<spool_dir>/live/<key>/`` every few
seconds, and turns what a dead process left behind into a pending recording
(the durable pending folder of ``recording_spool``) when the process starts.

Folder layout (mode 0700, files 0600):
  caller.pcm  raw appended 16-bit PCM of the caller
  agent.bin   appended records: <int64 sample offset><uint32 length><PCM bytes>
  meta.json   ``asdict`` of the SpoolEntry for this call, rewritten every tick

The normal save path calls ``checkpoint_done`` once the audio is safely in the
pending folder (or deliberately not kept); that removes the folder. Inbound
calls are never written here. Phone numbers are never logged.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import struct
import time
from dataclasses import asdict, dataclass
from typing import Any, Optional

from app.domain.services.recording_spool import (
    _KEY_RE,
    SpoolEntry,
    _from_dict,
    pending_has_audio,
    spool_dir,
    utc_now_iso,
    write_entry_if_absent,
)

logger = logging.getLogger(__name__)

_AGENT_HDR = struct.Struct("<qI")
_MAX_SECONDS = 3600
_MIN_SECONDS = 0.5
_DEFAULT_INTERVAL_S = 10.0
_MIN_INTERVAL_S = 5.0
_DONE_MAX = 5000
_O_BINARY = getattr(os, "O_BINARY", 0)
# A session gone from the registry belongs to its own teardown for this long.
# 120 s: the pending loop re-processes entries after 120 s, and a teardown's
# save (mix + spool write) takes seconds, so a session still not marked done
# after this long really ended without the normal path.
_VANISHED_GRACE_S = 120.0


# A call's teardown that is still running in this process (it has taken the
# session out of the registry and has not yet finished its recording save)
# owns its checkpoint: the pass must not turn the older copy into a pending
# recording that could be stored before the teardown's full capture.
# Key: the session's call_id (what the pass reads as ``vs.call_id``) -> start.
_TEARDOWN: dict[str, float] = {}
# A teardown that never finishes must not hide the safety copy for ever.
_TEARDOWN_MAX_S = 1800.0


def _now() -> float:
    """Monotonic seconds; tests replace this to move the clock."""
    return time.monotonic()


@dataclass
class _CallState:
    key: str
    session_call_id: str
    caller_written: int = 0
    agent_written: int = 0
    first_caller: Any = None
    first_agent: Any = None
    caller_bytes: int = 0
    need_reset: bool = False
    vanished_at: Optional[float] = None


# id(voice_session) -> state. Calls marked finished (dict used as ordered set).
_STATE: dict[int, _CallState] = {}
_DONE: dict[str, None] = {}


def _folder(key: str) -> str:
    if not isinstance(key, str) or not _KEY_RE.match(key):
        raise ValueError("invalid checkpoint key")
    return os.path.join(spool_dir(), "live", key)


def _rmtree(key: str) -> None:
    try:
        shutil.rmtree(_folder(key), ignore_errors=True)
    except Exception:
        pass


def _interval_s() -> Optional[float]:
    """Seconds between passes; ``None`` when the feature is disabled."""
    raw = os.getenv("RECORDING_CHECKPOINT_INTERVAL_S")
    try:
        value = float(raw) if raw not in (None, "") else _DEFAULT_INTERVAL_S
    except ValueError:
        value = _DEFAULT_INTERVAL_S
    if value <= 0:
        return None
    return max(value, _MIN_INTERVAL_S)


def _is_call_owner() -> bool:
    """Is this process the telephony call owner right now?

    Fails OPEN: an unreachable backend, a backend without the method, or an
    error all answer True (the loops run as before). Only an explicit False
    says "not the owner"; the gate must never cost a safety copy.
    """
    try:
        from app.domain.services.telephony.lifecycle import _state

        check = getattr(_state(), "is_telephony_owner", None)
        if not callable(check):
            return True
        return check() is not False
    except Exception:
        return True


# loop name -> True while it is idle because this process is not the owner
_LOOP_IDLE: dict[str, bool] = {}


def loop_may_run(loop_name: str) -> bool:
    """Gate of a recording loop's pass. One INFO line per change, none per pass."""
    owner = _is_call_owner()
    if not owner:
        if not _LOOP_IDLE.get(loop_name):
            _LOOP_IDLE[loop_name] = True
            logger.info("recording_loops_idle reason=not_call_owner loop=%s", loop_name)
        return False
    if _LOOP_IDLE.pop(loop_name, False):
        logger.info("recording_loops_active loop=%s", loop_name)
    return True


def checkpoint_teardown_started(session_call_id) -> None:
    """The call's teardown is running: keep its checkpoint. Never raises."""
    try:
        sid = str(session_call_id or "")
        if sid:
            now = _now()
            # A teardown that never reaches its save (the recording was already
            # saved at transfer) has no checkpoint for the pass to expire: drop
            # entries past the limit here, so the table cannot grow for ever.
            for old in [k for k, t in _TEARDOWN.items() if now - t >= _TEARDOWN_MAX_S]:
                _TEARDOWN.pop(old, None)
            _TEARDOWN[sid] = now
    except Exception:
        pass


def checkpoint_teardown_finished(session_call_id) -> None:
    """The teardown's save has returned (any result). Never raises.

    The normal grace for a checkpoint that is still there starts now.
    """
    try:
        sid = str(session_call_id or "")
        if sid and _TEARDOWN.pop(sid, None) is not None:
            now = _now()
            for st in list(_STATE.values()):
                if st.session_call_id == sid:
                    st.vanished_at = now
    except Exception:
        pass


def checkpoint_done(session_call_id) -> None:
    """Mark a call finished and remove its folder. Never raises."""
    try:
        sid = str(session_call_id or "")
        if not sid:
            return
        _TEARDOWN.pop(sid, None)
        _DONE[sid] = None
        while len(_DONE) > _DONE_MAX:
            _DONE.pop(next(iter(_DONE)), None)
        keys = {st.key for st in list(_STATE.values()) if st.session_call_id == sid}
        for sk in [k for k, st in list(_STATE.items()) if st.session_call_id == sid]:
            _STATE.pop(sk, None)
        if _KEY_RE.match(sid):
            keys.add(sid)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        for key in keys:
            if loop is not None:
                # Called from the end of a call: deleting files must not hold
                # up the other live calls.
                loop.run_in_executor(None, _rmtree, key)
            else:
                _rmtree(key)
    except Exception:
        pass


def _was_reset(buf, written: int, first) -> bool:
    if written <= 0:
        return False
    if len(buf) < written or not buf:
        return True
    return buf[0] is not first


def _write_files(
    key: str,
    sid: str,
    reset: bool,
    caller_new: list,
    agent_new: list,
    meta_bytes: bytes,
) -> None:
    """Worker thread: meta first (atomic), then append audio, fsync."""
    if sid in _DONE:
        return
    folder = _folder(key)
    try:
        _write_files_inner(folder, reset, caller_new, agent_new, meta_bytes)
    finally:
        if sid in _DONE:
            # checkpoint_done ran while this write was in flight (or the
            # write failed after it): the folder must not outlive the call.
            shutil.rmtree(folder, ignore_errors=True)


def _write_files_inner(folder: str, reset: bool, caller_new: list, agent_new: list, meta_bytes: bytes) -> None:
    os.makedirs(folder, mode=0o700, exist_ok=True)
    for private in (os.path.dirname(folder), os.path.dirname(os.path.dirname(folder))):
        # makedirs gives the mode to the last folder only.
        try:
            os.chmod(private, 0o700)
        except OSError:
            pass
    meta_path = os.path.join(folder, "meta.json")
    tmp_path = meta_path + ".tmp"
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _O_BINARY, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(meta_bytes)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, meta_path)

    def _append(name: str, payload: bytes) -> None:
        if not reset and not payload:
            return
        mode = os.O_WRONLY | os.O_CREAT | _O_BINARY
        mode |= os.O_TRUNC if reset else os.O_APPEND
        handle_fd = os.open(os.path.join(folder, name), mode, 0o600)
        with os.fdopen(handle_fd, "wb") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())

    _append("caller.pcm", b"".join(caller_new))
    _append(
        "agent.bin",
        b"".join(_AGENT_HDR.pack(int(off), len(chunk)) + chunk for off, chunk in agent_new),
    )


def _read_folder(key: str):
    """Return ``(entry, caller_chunks, agent_chunks)`` or ``None`` if unreadable."""
    folder = _folder(key)
    try:
        with open(os.path.join(folder, "meta.json"), "rb") as handle:
            data = json.loads(handle.read().decode("utf-8"))
        if not isinstance(data, dict):
            return None
        entry = _from_dict(data)
        entry.key = key
        int(entry.sample_rate)
    except (OSError, ValueError, TypeError):
        return None
    try:
        with open(os.path.join(folder, "caller.pcm"), "rb") as handle:
            caller = handle.read()
    except FileNotFoundError:
        caller = b""
    caller = caller[: len(caller) - (len(caller) % 2)]
    agent_chunks: list[tuple[int, bytes]] = []
    try:
        with open(os.path.join(folder, "agent.bin"), "rb") as handle:
            blob = handle.read()
    except FileNotFoundError:
        blob = b""
    pos = 0
    while pos + _AGENT_HDR.size <= len(blob):
        offset, length = _AGENT_HDR.unpack_from(blob, pos)
        start = pos + _AGENT_HDR.size
        if start + length > len(blob):
            break  # cut-off record at the end
        agent_chunks.append((offset, blob[start:start + length]))
        pos = start + length
    return entry, ([caller] if caller else []), agent_chunks


def _recover_sync(key: str, source: str = "startup") -> tuple[str, float]:
    """Turn one folder into a pending recording. ``(status, seconds)``.

    Status: ``recovered`` (written to pending, folder removed), ``already_pending``
    (a pending entry holds the audio, folder removed), ``kept`` (a pending entry
    exists with an empty wav: nothing written, folder KEPT), plus ``short`` and
    ``unreadable``.

    The ``created_at`` stamp for ``source=pass`` is so that the pending loop (an
    entry with no attempts waits ``_ORPHAN_AGE_S`` from ``created_at``) leaves
    it long enough for a late teardown's ``write_entry`` to replace it; for
    ``id_reuse`` it is kept for uniformity (harmless: the old session object is gone).
    """
    from app.domain.services.recording_service import mix_stereo_recording

    parsed = _read_folder(key)
    if parsed is None:
        return "unreadable", 0.0
    entry, caller_chunks, agent_chunks = parsed
    rate = int(entry.sample_rate)
    caller_bytes = sum(len(c) for c in caller_chunks)
    duration = caller_bytes / (rate * 2) if rate > 0 else 0.0
    if duration < _MIN_SECONDS:
        shutil.rmtree(_folder(key), ignore_errors=True)
        return "short", duration
    wav = mix_stereo_recording(
        caller_chunks=caller_chunks, agent_chunks=agent_chunks, sample_rate=rate,
    )
    entry.duration_seconds = float(duration)
    if source in ("pass", "id_reuse"):
        entry.created_at = utc_now_iso()
    if not write_entry_if_absent(entry, wav):
        # A pending entry with this key exists and was not replaced (checked
        # under the spool lock, after the mix).
        if not pending_has_audio(key):
            # Its wav is empty (the transfer hand-over's awaiting entry): the
            # checkpointed audio is the only copy, so the folder stays.
            return "kept", duration
        shutil.rmtree(_folder(key), ignore_errors=True)
        return "already_pending", duration
    shutil.rmtree(_folder(key), ignore_errors=True)
    return "recovered", duration


async def _recover_key(key: str, source: str = "pass") -> bool:
    try:
        status, seconds = await asyncio.to_thread(_recover_sync, key, source)
    except Exception as exc:
        logger.error(
            "recording_checkpoint_error call=%s err=%s", str(key)[:12], type(exc).__name__,
        )
        return False
    if status == "unreadable":
        logger.warning("recording_checkpoint_unreadable call=%s", str(key)[:12])
        return False
    if status == "kept":
        logger.warning(
            "recording_checkpoint_kept call=%s source=%s reason=pending_entry_has_no_audio",
            str(key)[:12], source,
        )
        return False
    if status == "recovered":
        # source=pass means audio is really being rescued: WARNING.
        (logger.warning if source in ("pass", "id_reuse") else logger.info)(
            "recording_checkpoint_recovered call=%s source=%s seconds=%.1f", str(key)[:12], source, seconds,
        )
        return True
    return False


async def recover_session_checkpoint(session_call_id) -> str:
    """Move one session's checkpoint copy (if any) to the pending folder.

    Returns ``"recovered"`` (copy now pending, or already pending), ``"none"``
    (nothing there, or too short to keep) or ``"failed"`` (copy left on disk).
    Never raises.
    """
    try:
        sid = str(session_call_id or "")
        if not _KEY_RE.match(sid):
            return "none"
        if not os.path.isdir(_folder(sid)):
            return "none"
        status, _seconds = await asyncio.to_thread(_recover_sync, sid)
    except Exception as exc:
        logger.error(
            "recording_checkpoint_error call=%s err=%s",
            str(session_call_id)[:12], type(exc).__name__,
        )
        return "failed"
    if status in ("recovered", "already_pending"):
        return "recovered"
    if status == "short":
        return "none"
    return "failed"


async def recover_orphans() -> int:
    """Recover folders that do not belong to a live session of this process."""
    try:
        live_dir = os.path.join(spool_dir(), "live")
        try:
            names = await asyncio.to_thread(os.listdir, live_dir)
        except FileNotFoundError:
            return 0
        tracked = {st.key for st in list(_STATE.values())}
        recovered = 0
        for name in sorted(names):
            if not _KEY_RE.match(name) or name in tracked:
                continue
            if not os.path.isdir(os.path.join(live_dir, name)):
                continue
            if await _recover_key(name, "startup"):
                recovered += 1
        return recovered
    except Exception as exc:
        logger.error("recording_checkpoint_error err=%s", type(exc).__name__)
        return 0


def _sample_rate(gateway) -> int:
    try:
        return int(getattr(gateway, "_sample_rate", None) or 16000)
    except Exception:
        return 16000


async def _checkpoint_one(pbx_id: str, vs, sid: str) -> bool:
    from app.domain.services.telephony.recording import (
        _build_spool_entry,
        _is_true_inbound_session,
    )

    if sid in _DONE:
        return False
    st = _STATE.get(id(vs))
    if st is not None and st.session_call_id != sid:
        # The id of a finished session was reused: finish the old one first
        # (before any branch below can delete its folder).
        _STATE.pop(id(vs), None)
        if st.session_call_id not in _DONE:
            await _recover_key(st.key, "id_reuse")
        st = None
    inbound = _is_true_inbound_session(vs)
    if inbound and getattr(vs, "_recording_allowed", False) is not True:
        # An inbound gate opens only after the notice attempt; until then nothing goes to disk.
        # A gate closed mid-call (emergency stop) also destroys the copy already written
        # (this session's own; another call's stale state was handled above).
        gone = _STATE.pop(id(vs), None)
        if gone is not None and gone.session_call_id == sid:
            await asyncio.to_thread(_rmtree, gone.key)
        return False
    if getattr(vs, "_transfer_connected", False) or getattr(vs, "_recording_saved_at_transfer", False):
        return False
    if not inbound and getattr(vs, "_recording_policy_decided", False) is not True:
        # Until the call has taken its recording decision nothing goes to
        # disk: not the audio of a tenant who switched recording off, and
        # not what was said before the spoken notice.
        return False
    gateway = getattr(vs, "media_gateway", None)
    if gateway is None:
        return False

    enabled_fn = getattr(gateway, "is_recording_enabled", None)
    buffer_fn = getattr(gateway, "get_recording_buffer", None)
    session_exists = not callable(buffer_fn) or buffer_fn(vs.call_id) is not None
    if callable(enabled_fn) and not enabled_fn(vs.call_id) and not session_exists:
        # The gateway session is gone (not "recording disabled"): leave the
        # folder alone; it is recovered when the voice session disappears.
        return False
    if callable(enabled_fn) and not enabled_fn(vs.call_id):
        # Audio that must not be kept is not kept on disk either.
        gone = _STATE.pop(id(vs), None)
        if gone is not None:
            await asyncio.to_thread(_rmtree, gone.key)
        return False

    # ---- one synchronous stretch: read, detect reset, slice ----
    caller_live = gateway.get_recording_buffer(vs.call_id) or []
    getter = getattr(gateway, "get_tts_recording_buffer", None)
    agent_live = (getter(vs.call_id) if callable(getter) else None) or []
    if st is None and not caller_live and not agent_live:
        return False
    reset = False
    if st is not None:
        reset = (
            st.need_reset
            or _was_reset(caller_live, st.caller_written, st.first_caller)
            or _was_reset(agent_live, st.agent_written, st.first_agent)
        )
    c_from = 0 if (st is None or reset) else st.caller_written
    a_from = 0 if (st is None or reset) else st.agent_written
    base_bytes = 0 if (st is None or reset) else st.caller_bytes
    cap = _sample_rate(gateway) * 2 * _MAX_SECONDS
    if base_bytes >= cap:
        caller_new: list = []
        agent_new: list = []
    else:
        caller_new = list(caller_live[c_from:])
        agent_new = list(agent_live[a_from:])
    first_caller = caller_live[0] if caller_live else None
    first_agent = agent_live[0] if agent_live else None

    entry = _build_spool_entry(
        vs, pbx_id, sample_rate=_sample_rate(gateway), duration=0.0,
        awaiting_transfer=False, transfer_recording_name=None,
    )
    if st is None:
        st = _CallState(key=entry.key, session_call_id=sid)
        _folder(st.key)  # validates the key before anything is stored
    entry.key = st.key
    meta_bytes = json.dumps(asdict(entry), sort_keys=True).encode("utf-8")

    # When capped, caller_new is empty and the counts stay where they were.
    st.caller_written = c_from + len(caller_new)
    st.agent_written = a_from + len(agent_new)
    st.first_caller = first_caller
    st.first_agent = first_agent
    st.caller_bytes = base_bytes + sum(len(c) for c in caller_new)
    st.need_reset = False
    _STATE[id(vs)] = st
    # ---- end of the synchronous stretch ----

    try:
        await asyncio.to_thread(_write_files, st.key, sid, reset, caller_new, agent_new, meta_bytes)
    except BaseException:
        st.need_reset = True  # files and memory disagree: start over next pass
        raise
    return True


async def checkpoint_pass() -> int:
    """Copy new audio of every live call to disk. Returns calls written."""
    from app.domain.services.telephony.lifecycle import _state

    items = list(_state().iter_voice_session_items())
    wrote = 0
    seen: dict[int, str] = {}
    for pbx_id, vs in items:
        sid = str(getattr(vs, "call_id", "") or "")
        seen[id(vs)] = sid
        try:
            if await _checkpoint_one(str(pbx_id), vs, sid):
                wrote += 1
        except Exception as exc:
            logger.error(
                "recording_checkpoint_error call=%s err=%s", sid[:12], type(exc).__name__,
            )
    for sk in list(_STATE):
        st = _STATE.get(sk)
        if st is None:
            continue
        if seen.get(sk) == st.session_call_id:
            st.vanished_at = None  # back in the registry: no stale clock
            continue
        if st.session_call_id in _DONE:
            _STATE.pop(sk, None)
            continue
        # Gone from the registry and not done: its teardown may be saving right
        # now. Leave it alone until the grace has passed without checkpoint_done.
        now = _now()
        started = _TEARDOWN.get(st.session_call_id)
        if started is not None:
            if now - started < _TEARDOWN_MAX_S:
                st.vanished_at = None  # its own teardown is still running
                continue
            _TEARDOWN.pop(st.session_call_id, None)  # never finished: normal rules
        if st.vanished_at is None:
            st.vanished_at = now
            continue
        if now - st.vanished_at < _VANISHED_GRACE_S:
            continue
        _STATE.pop(sk, None)
        _TEARDOWN.pop(st.session_call_id, None)
        # The call ended without the normal path finishing it: keep the audio.
        if os.path.isdir(_folder(st.key)):
            await _recover_key(st.key)
    live_ids = set(seen.values())
    for done_id in [d for d in _DONE if d not in live_ids]:
        _DONE.pop(done_id, None)
    return wrote


async def recording_checkpoint_loop(stop_event: asyncio.Event) -> None:
    interval = _interval_s()
    # Start-up recovery runs ONCE, on the first moment this process is the call
    # owner (the loop may start just before the ownership lock is taken). A
    # non-owner would treat the owner's live folders as orphans.
    orphans_done = False
    if loop_may_run("recording_checkpoint_loop"):
        orphans_done = True
        try:
            await recover_orphans()
        except Exception as exc:
            logger.error("recording_checkpoint_error err=%s", type(exc).__name__)
    if interval is None:
        return
    logger.info("recording_checkpoint_loop_started interval_s=%s", interval)
    try:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                pass
            if not loop_may_run("recording_checkpoint_loop"):
                continue  # not the call owner right now: do nothing this round
            if not orphans_done:
                orphans_done = True
                try:
                    await recover_orphans()
                except Exception as exc:
                    logger.error("recording_checkpoint_error err=%s", type(exc).__name__)
            try:
                await checkpoint_pass()
            except Exception as exc:
                logger.error("recording_checkpoint_error err=%s", type(exc).__name__)
    finally:
        logger.info("recording_checkpoint_loop_stopped")
