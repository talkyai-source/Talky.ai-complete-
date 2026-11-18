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
import time
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from typing import Any, Optional

_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_ORPHAN_WAV_AGE_S = 600.0


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
    tmp_path = final_path + ".tmp"
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    os.replace(tmp_path, final_path)


def _entry_bytes(entry: SpoolEntry) -> bytes:
    return json.dumps(asdict(entry), sort_keys=True).encode("utf-8")


def write_entry(entry: SpoolEntry, wav_bytes: bytes) -> None:
    """Write the WAV, then the JSON commit marker, each atomically."""
    _check_key(entry.key)
    _ensure_dir()
    wav_path, json_path = _paths(entry.key)
    _atomic_write(wav_path, wav_bytes)
    _atomic_write(json_path, _entry_bytes(entry))


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
    wav_path, _ = _paths(key)
    try:
        with open(wav_path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def remove_entry(key: str) -> None:
    """Remove JSON first, then WAV; missing files are not an error."""
    wav_path, json_path = _paths(key)
    for path in (json_path, wav_path):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


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
