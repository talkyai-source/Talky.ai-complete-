"""Confirm-before-commit state machine for sensitive captured fields
(email / name / phone) — the "caller is the source of truth" guarantee.

Architecture (see the 2026-06-24 design discussion): you can't make an 8 kHz
phone transcription error-free, so don't try. Instead guarantee that no value is
ever *committed* until the caller has confirmed it, and that a correction always
re-opens the field. The reliability lives in this loop, not in a perfect guess.

Guarantees:
  * ``confirmed_value`` (THE COMMIT GATE) returns a value ONLY when the caller
    explicitly confirmed it — a single STT mishear can never silently be saved.
  * A new value, or an explicit "no", INVALIDATES any prior confirmation and
    re-opens the field (the repair loop).
  * Bounded attempts -> EXHAUSTED, so the agent escalates / falls back instead of
    looping forever.
  * Thread-safe + atomic: every mutating op takes a per-instance lock with NO
    await inside, so ANY interleaving of record/confirm/reject leaves the state
    valid (no torn writes, no lost updates, no unconfirmed commit). The voice
    pipeline is single-threaded asyncio, but the lock also makes it correct under
    threads — and is what the race tests exercise.

Pure logic, no I/O. Keyed by (call_id, field).
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class FieldStatus(str, Enum):
    EMPTY = "empty"          # nothing captured yet (or rejected -> awaiting re-capture)
    PENDING = "pending"      # captured, awaiting caller confirmation
    CONFIRMED = "confirmed"  # caller confirmed -> safe to commit
    EXHAUSTED = "exhausted"  # too many attempts -> escalate / human fallback


# How many capture attempts before we stop looping and escalate.
MAX_ATTEMPTS = int(os.getenv("CAPTURE_MAX_ATTEMPTS", "3"))


@dataclass
class _Field:
    value: str = ""
    status: FieldStatus = FieldStatus.EMPTY
    attempts: int = 0


# ── caller-response classification ───────────────────────────────────────────

_AFFIRM_RE = re.compile(
    r"\b(yes|yeah|yep|yup|correct|that'?s\s+right|that'?s\s+it|exactly|perfect|"
    r"spot\s+on|sounds?\s+good|got\s+it|uh[\s-]?huh|mm[\s-]?hmm|right|confirmed?)\b",
    re.IGNORECASE,
)
_NEGATE_RE = re.compile(
    r"\b(no|nope|nah|not\s+quite|not\s+right|that'?s\s+not|that'?s\s+wrong|wrong|"
    r"incorrect|mistake|actually|let\s+me\s+(?:correct|fix|redo))\b",
    re.IGNORECASE,
)


def classify_confirmation(utterance: Optional[str]) -> str:
    """Return 'affirm' | 'reject' | 'unclear' for a caller reply to a read-back.

    A negation WINS over an affirmation in the same utterance ("no, yeah it's
    actually…"), because catching the correction is the safety-critical case.
    """
    if not utterance:
        return "unclear"
    if _NEGATE_RE.search(utterance):
        return "reject"
    if _AFFIRM_RE.search(utterance):
        return "affirm"
    return "unclear"


class CaptureConfirmation:
    """Per-(call, field) confirm-before-commit state machine. Thread-safe."""

    def __init__(self) -> None:
        self._fields: dict[tuple[str, str], _Field] = {}
        self._lock = threading.Lock()

    # NOTE: _get must be called WITH the lock held.
    def _get(self, call_id: str, field: str) -> _Field:
        key = (call_id, field)
        f = self._fields.get(key)
        if f is None:
            f = _Field()
            self._fields[key] = f
        return f

    def record_capture(self, call_id: str, field: str, value: Optional[str]) -> FieldStatus:
        """A new candidate value was captured (STT / normalizer). Moves the field
        to PENDING (awaiting confirmation). A value different from a CONFIRMED one
        is a correction -> it RE-OPENS the field (un-commits)."""
        new = (value or "").strip()
        with self._lock:
            f = self._get(call_id, field)
            if not new:
                return f.status
            if f.status == FieldStatus.CONFIRMED and f.value == new:
                return f.status  # re-heard the exact confirmed value — keep it
            f.value = new
            f.status = FieldStatus.PENDING
            f.attempts += 1
            if f.attempts > MAX_ATTEMPTS:
                f.status = FieldStatus.EXHAUSTED
            return f.status

    def confirm(self, call_id: str, field: str) -> FieldStatus:
        """Caller affirmed the pending value -> CONFIRMED (committable)."""
        with self._lock:
            f = self._get(call_id, field)
            if f.status == FieldStatus.PENDING:
                f.status = FieldStatus.CONFIRMED
            return f.status

    def reject(self, call_id: str, field: str) -> FieldStatus:
        """Caller said it's wrong -> drop the value, await a fresh capture."""
        with self._lock:
            f = self._get(call_id, field)
            if f.status in (FieldStatus.PENDING, FieldStatus.CONFIRMED):
                f.value = ""
                f.status = FieldStatus.EMPTY
            return f.status

    def apply_caller_response(self, call_id: str, field: str, utterance: str) -> FieldStatus:
        """Interpret the caller's reply to a read-back and transition."""
        verdict = classify_confirmation(utterance)
        if verdict == "affirm":
            return self.confirm(call_id, field)
        if verdict == "reject":
            return self.reject(call_id, field)
        with self._lock:                       # unclear — no transition
            return self._get(call_id, field).status

    # ── read-only views ─────────────────────────────────────────────────────

    def status(self, call_id: str, field: str) -> FieldStatus:
        with self._lock:
            f = self._fields.get((call_id, field))
            return f.status if f else FieldStatus.EMPTY

    def attempts(self, call_id: str, field: str) -> int:
        with self._lock:
            f = self._fields.get((call_id, field))
            return f.attempts if f else 0

    def needs_confirmation(self, call_id: str, field: str) -> bool:
        """True when the agent should READ BACK + ask (value is PENDING)."""
        return self.status(call_id, field) == FieldStatus.PENDING

    def pending_value(self, call_id: str, field: str) -> Optional[str]:
        """The value awaiting confirmation (to read back). None if not pending."""
        with self._lock:
            f = self._fields.get((call_id, field))
            return f.value if (f and f.status == FieldStatus.PENDING) else None

    def confirmed_value(self, call_id: str, field: str) -> Optional[str]:
        """THE COMMIT GATE — returns a value ONLY when the caller confirmed it.
        Anything not CONFIRMED returns None, so an unconfirmed mishear can never
        be saved or acted on."""
        with self._lock:
            f = self._fields.get((call_id, field))
            return f.value if (f and f.status == FieldStatus.CONFIRMED) else None

    def snapshot(self, call_id: str, field: str) -> tuple:
        """Atomic (status, value) read — for callers/tests that must see BOTH
        consistently, without a transition straddling two separate reads."""
        with self._lock:
            f = self._fields.get((call_id, field))
            return (f.status, f.value) if f else (FieldStatus.EMPTY, "")

    def is_exhausted(self, call_id: str, field: str) -> bool:
        return self.status(call_id, field) == FieldStatus.EXHAUSTED

    def clear(self, call_id: str) -> None:
        """Drop all fields for a call (teardown)."""
        with self._lock:
            for key in [k for k in self._fields if k[0] == call_id]:
                del self._fields[key]
