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
from dataclasses import asdict, dataclass
from typing import Any, Optional

from app.domain.services.recording_spool import (
    _KEY_RE,
    SpoolEntry,
    _from_dict,
    read_entry,
    spool_dir,
    write_entry,
)

logger = logging.getLogger(__name__)

_AGENT_HDR = struct.Struct("<qI")
_MAX_SECONDS = 3600
_MIN_SECONDS = 0.5
_DEFAULT_INTERVAL_S = 10.0
_MIN_INTERVAL_S = 5.0
_DONE_MAX = 5000
_O_BINARY = getattr(os, "O_BINARY", 0)


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


def checkpoint_done(session_call_id) -> None:
    """Mark a call finished and remove its folder. Never raises."""
    try:
        sid = str(session_call_id or "")
        if not sid:
            return
        _DONE[sid] = None
        while len(_DONE) > _DONE_MAX:
            _DONE.pop(next(iter(_DONE)), None)
        keys = {st.key for st in list(_STATE.values()) if st.session_call_id == sid}
        if _KEY_RE.match(sid):
            keys.add(sid)
        for key in keys:
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
    os.makedirs(folder, mode=0o700, exist_ok=True)
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
    if sid in _DONE:
        # checkpoint_done ran while this write was in flight.
        shutil.rmtree(folder, ignore_errors=True)


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


def _recover_sync(key: str) -> tuple[str, float]:
    """Turn one folder into a pending recording. ``(status, seconds)``."""
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
    if read_entry(key) is not None:
        # A pending entry with this key already holds the call's audio.
        shutil.rmtree(_folder(key), ignore_errors=True)
        return "already_pending", duration
    wav = mix_stereo_recording(
        caller_chunks=caller_chunks, agent_chunks=agent_chunks, sample_rate=rate,
    )
    entry.duration_seconds = float(duration)
    write_entry(entry, wav)
    shutil.rmtree(_folder(key), ignore_errors=True)
    return "recovered", duration


async def _recover_key(key: str) -> bool:
    try:
        status, seconds = await asyncio.to_thread(_recover_sync, key)
    except Exception as exc:
        logger.error(
            "recording_checkpoint_error call=%s err=%s", str(key)[:12], type(exc).__name__,
        )
        return False
    if status == "unreadable":
        logger.warning("recording_checkpoint_unreadable call=%s", str(key)[:12])
        return False
    if status == "recovered":
        logger.info("recording_checkpoint_recovered call=%s seconds=%.1f", str(key)[:12], seconds)
        return True
    return False


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
            if await _recover_key(name):
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
    if _is_true_inbound_session(vs):
        return False
    if getattr(vs, "_transfer_connected", False) or getattr(vs, "_recording_saved_at_transfer", False):
        return False
    gateway = getattr(vs, "media_gateway", None)
    if gateway is None:
        return False

    st = _STATE.get(id(vs))
    if st is not None and st.session_call_id != sid:
        # The id of a finished session was reused: finish the old one first.
        _STATE.pop(id(vs), None)
        if st.session_call_id not in _DONE:
            await _recover_key(st.key)
        st = None

    enabled_fn = getattr(gateway, "is_recording_enabled", None)
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
        if st is None or seen.get(sk) == st.session_call_id:
            continue
        _STATE.pop(sk, None)
        if st.session_call_id in _DONE:
            continue
        # The call ended without the normal path finishing it: keep the audio.
        if os.path.isdir(_folder(st.key)):
            await _recover_key(st.key)
    live_ids = set(seen.values())
    for done_id in [d for d in _DONE if d not in live_ids]:
        _DONE.pop(done_id, None)
    return wrote


async def recording_checkpoint_loop(stop_event: asyncio.Event) -> None:
    interval = _interval_s()
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
            try:
                await checkpoint_pass()
            except Exception as exc:
                logger.error("recording_checkpoint_error err=%s", type(exc).__name__)
    finally:
        logger.info("recording_checkpoint_loop_stopped")
