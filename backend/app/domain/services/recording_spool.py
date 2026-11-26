"""Durable pending folder ("spool") for call recordings.

Once a call's audio has been mixed it is written here BEFORE anything that can
fail (tenant lookup, database, storage). An entry is a ``<key>.wav`` plus a
``<key>.json`` sidecar; the JSON is the commit marker. The entry stays until
the recording is safely saved or definitely refused.

Self-contained: no database code. Functions are synchronous; callers run them
with ``asyncio.to_thread``. Phone numbers are never logged.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from typing import Any, Optional

_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_ORPHAN_WAV_AGE_S = 600.0
# One lock for the whole of every write_entry: it runs in worker threads, and
# two writers of one key must never interleave (the loser of a race used to
# fail on a shared temp file or leave the older audio behind).
_WRITE_LOCK = threading.Lock()


@dataclass
class SpoolEntry:
    key: str
    pbx_call_id: str
    session_call_id: str
    tenant_id: Optional[str]
    campaign_id: Optional[str]
    phone_hint: Optional[str]
    sample_rate: int
    duration_seconds: float
    disclosure_state: Optional[str]
    callee_country: Optional[str]
    callee_country_known: bool
    inbound: bool
    strict_notice: bool
    awaiting: Optional[str]  # None or "transfer_part"
    transfer_recording_name: Optional[str]
    transfer_part: Optional[str]
    created_at: str
    attempts: int = 0
    last_error: Optional[str] = None
    last_attempt_at: Optional[str] = None
    version: int = 1


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def spool_dir() -> str:
    configured = os.getenv("RECORDING_SPOOL_DIR")
    if configured:
        return os.path.abspath(configured)
    base = os.path.abspath(os.getenv("LOCAL_RECORDINGS_DIR", "./recordings"))
    return os.path.join(base, ".pending")


def _ensure_dir() -> str:
    path = spool_dir()
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)  # also when it existed already with a wider mode
    except OSError:
        pass
    return path


def _check_key(key: str) -> str:
    if not isinstance(key, str) or not _KEY_RE.match(key):
        raise ValueError("invalid spool key")
    return key


def _paths(key: str) -> tuple[str, str]:
    _check_key(key)
    base = spool_dir()
    return os.path.join(base, key + ".wav"), os.path.join(base, key + ".json")


def _atomic_write(final_path: str, data: bytes) -> None:
    # A temp name of its own per call: two writers of one key never share a file.
    tmp_path = f"{final_path}.{secrets.token_hex(6)}.tmp"
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, final_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _entry_bytes(entry: SpoolEntry) -> bytes:
    return json.dumps(asdict(entry), sort_keys=True).encode("utf-8")


def _write_entry_unlocked(entry: SpoolEntry, wav_bytes: bytes) -> None:
    _check_key(entry.key)
    _ensure_dir()
    wav_path, json_path = _paths(entry.key)
    step = "wav"
    try:
        _atomic_write(wav_path, wav_bytes)
        step = "json"
        _atomic_write(json_path, _entry_bytes(entry))
    except Exception as exc:
        try:
            exc.spool_step = step  # type: ignore[attr-defined]  # the log line names it
        except Exception:
            pass
        raise


def write_entry(entry: SpoolEntry, wav_bytes: bytes) -> None:
    """Write the WAV, then the JSON commit marker, each atomically (one writer at a time)."""
    with _WRITE_LOCK:
        _write_entry_unlocked(entry, wav_bytes)


def _describe_wav(wav_path: str, entry: SpoolEntry) -> None:
    """Take the rate and duration from the WAV that is already on disk."""
    import wave

    try:
        with wave.open(wav_path, "rb") as handle:
            rate = handle.getframerate()
            if rate > 0:
                entry.sample_rate = int(rate)
                entry.duration_seconds = handle.getnframes() / float(rate)
    except Exception:
        pass  # unreadable header: keep what the caller knew


def write_entry_if_absent(entry: SpoolEntry, wav_bytes: bytes) -> bool:
    """Write the audio only when none is pending for this key; say whether it wrote.

    Audio on disk is never replaced. If it is there but its JSON is missing or
    unreadable (a write that failed at the JSON step), only the JSON is written,
    describing the EXISTING wav, so the entry becomes listable and gets saved;
    the return value is still False (no audio written). The check and the write
    are one step under the same lock as ``write_entry``.
    """
    _check_key(entry.key)
    with _WRITE_LOCK:
        if has_audio(entry.key):
            if read_entry(entry.key) is not None:
                return False  # a good json: the entry is never touched, whatever the wav's size
            wav_path, json_path = _paths(entry.key)
            if _wav_size(wav_path) > _WAV_HEADER_BYTES:
                _describe_wav(wav_path, entry)
                try:
                    _atomic_write(json_path, _entry_bytes(entry))
                except Exception as exc:
                    exc.spool_step = "json"  # type: ignore[attr-defined]
                    raise
                return False
            # No json and no audio frames (0 bytes or a bare header): absent.
        _write_entry_unlocked(entry, wav_bytes)
        return True


_WAV_HEADER_BYTES = 44


def _wav_size(wav_path: str) -> int:
    try:
        return os.path.getsize(wav_path)
    except OSError:
        return 0


def pending_has_audio(key: str) -> bool:
    """True when ``<key>.wav`` exists and is larger than a bare 44-byte header.

    A hint only (a size check, no parsing, no lock): the file may change right after.
    """
    try:
        wav_path, _ = _paths(key)
    except ValueError:
        return False
    return _wav_size(wav_path) > _WAV_HEADER_BYTES


def has_audio(key: str) -> bool:
    """Whether the pending folder holds this entry's WAV."""
    try:
        wav_path, _ = _paths(key)
    except ValueError:
        return False
    return os.path.isfile(wav_path)


def update_entry(entry: SpoolEntry) -> None:
    """Rewrite only the JSON sidecar (the WAV is untouched).

    Does nothing when the WAV is not there: an entry is its audio, and a
    sidecar alone would make a later recovery believe the audio is safe
    (and would bring back an entry that was already saved and removed).
    """
    _check_key(entry.key)
    wav_path, json_path = _paths(entry.key)
    with _WRITE_LOCK:  # "the wav is there" and the write are one step
        if not os.path.isfile(wav_path):
            return
        _ensure_dir()
        _atomic_write(json_path, _entry_bytes(entry))


def _from_dict(data: dict[str, Any]) -> SpoolEntry:
    names = {f.name for f in fields(SpoolEntry)}
    return SpoolEntry(**{k: v for k, v in data.items() if k in names})


def read_entry(key: str) -> Optional[SpoolEntry]:
    _, json_path = _paths(key)
    try:
        with open(json_path, "rb") as handle:
            data = json.loads(handle.read().decode("utf-8"))
        if not isinstance(data, dict):
            return None
        entry = _from_dict(data)
        _check_key(entry.key)
        return entry
    except (OSError, ValueError, TypeError):
        return None


def read_wav(key: str) -> Optional[bytes]:
    """The pending audio, or None when the file does not exist.

    Any other error (permission, I/O, too many open files) is raised: it is
    not "the audio is missing", and the callers keep the copy and try again.
    """
    wav_path, _ = _paths(key)
    try:
        with open(wav_path, "rb") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def remove_entry(key: str) -> None:
    """Remove JSON first, then WAV; missing files are not an error."""
    wav_path, json_path = _paths(key)
    with _WRITE_LOCK:
        for path in (json_path, wav_path):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


_STALE_TMP_AGE_S = 3600.0


def sweep_leftovers(now: Optional[float] = None) -> int:
    """Remove what a crash leaves in the pending folder; returns files removed.

    Called by the application's own pending loop, never by ``list_entries``
    (the health watch, another process, calls that, and the lock does not span
    processes):
    - a ``<key>.json`` whose audio is missing, older than ``_ORPHAN_WAV_AGE_S``;
    - a per-call ``*.tmp`` older than one hour (it holds call audio; temp names
      are unique per call, so a live writer is never hit).
    """
    base = spool_dir()
    now_ts = time.time() if now is None else now
    try:
        names = os.listdir(base)
    except OSError:
        return 0
    removed = 0
    for name in names:
        path = os.path.join(base, name)
        try:
            if name.endswith(".tmp"):
                if now_ts - os.path.getmtime(path) > _STALE_TMP_AGE_S:
                    os.unlink(path)
                    removed += 1
            elif name.endswith(".json") and _KEY_RE.match(name[: -len(".json")]):
                key = name[: -len(".json")]
                with _WRITE_LOCK:
                    if os.path.exists(os.path.join(base, key + ".wav")):
                        continue
                    if now_ts - os.path.getmtime(path) > _ORPHAN_WAV_AGE_S:
                        os.unlink(path)
                        removed += 1
        except OSError:
            continue
    return removed


def list_entries() -> list[SpoolEntry]:
    """Committed entries only (JSON parses AND WAV exists), oldest first."""
    base = spool_dir()
    try:
        names = os.listdir(base)
    except FileNotFoundError:
        return []
    entries: list[SpoolEntry] = []
    for name in names:
        if not name.endswith(".json"):
            continue
        key = name[: -len(".json")]
        if not _KEY_RE.match(key):
            continue
        try:
            entry = read_entry(key)
            if entry is None or entry.key != key:
                continue
            if not os.path.exists(os.path.join(base, key + ".wav")):
                continue
            entries.append(entry)
        except Exception:
            continue
    entries.sort(key=lambda e: e.created_at or "")
    return entries


def pending_summary(now: Optional[float] = None) -> dict:
    """``{"count", "oldest_age_seconds", "awaiting"}``; never raises."""
    try:
        entries = list_entries()
        now_ts = time.time() if now is None else now
        oldest = None
        for entry in entries:
            created = parse_iso(entry.created_at)
            if created is None:
                continue
            age = max(0.0, now_ts - created.timestamp())
            if oldest is None or age > oldest:
                oldest = age
        return {
            "count": len(entries),
            "oldest_age_seconds": oldest if oldest is not None else 0.0,
            "awaiting": sum(1 for e in entries if e.awaiting),
        }
    except Exception as exc:
        return {"count": None, "error": type(exc).__name__}
