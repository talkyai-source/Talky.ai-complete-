"""
Transcript Service
Handles transcript accumulation and storage for call conversations.
Provider-agnostic - works with any voice pipeline.
"""
import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from weakref import WeakValueDictionary

logger = logging.getLogger(__name__)


@dataclass
class TranscriptTurn:
    """A single turn in a conversation transcript."""
    role: str  # "user" or "assistant"
    content: str
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    confidence: Optional[float] = None  # STT confidence score if available
    talklee_call_id: Optional[str] = None
    turn_index: Optional[int] = None
    event_type: str = "utterance"
    is_final: Optional[bool] = None
    audio_window_start: Optional[str] = None
    audio_window_end: Optional[str] = None
    include_in_plaintext: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "confidence": self.confidence,
            "talklee_call_id": self.talklee_call_id,
            "turn_index": self.turn_index,
            "event_type": self.event_type,
            "is_final": self.is_final,
            "audio_window_start": self.audio_window_start,
            "audio_window_end": self.audio_window_end,
            "include_in_plaintext": self.include_in_plaintext,
            "metadata": self.metadata,
        }


def effective_turn(turn: Dict[str, Any]) -> Dict[str, Any]:
    """Project a bound ASR revision while retaining the original audit text.

    A truncated/corrupt revision is unavailable evidence, not permission to
    silently resurrect its older assertion. This function is idempotent and
    never mutates the raw buffer or stored historical row.
    """
    result = dict(turn)
    role = str(turn.get("role") or turn.get("speaker") or "").lower()
    result["role"] = {"caller": "user", "customer": "user", "agent": "assistant"}.get(role, role)
    if "content" not in result:
        result["content"] = turn.get("text", "")
    metadata = turn.get("metadata")
    if result["role"] != "user" or not isinstance(metadata, dict) or "asr_latest_revision" not in metadata:
        return result
    result["original_content"] = turn.get("original_content", result.get("content", ""))
    revision = metadata.get("asr_latest_revision")
    valid = isinstance(revision, dict)
    if valid:
        content = revision.get("content")
        order = revision.get("caller_turn_order")
        number = revision.get("revision")
        item = revision.get("provider_item_id")
        valid = (
            isinstance(content, str) and len(content) <= 4096
            and isinstance(item, str) and bool(item.strip()) and len(item) <= 256
            and item == metadata.get("provider_item_id")
            and isinstance(order, int) and not isinstance(order, bool) and order >= 0
            and isinstance(metadata.get("caller_turn_order"), int) and not isinstance(metadata.get("caller_turn_order"), bool)
            and order == metadata.get("caller_turn_order")
            and isinstance(number, int) and not isinstance(number, bool) and number > 0
            and revision.get("truncated") is False
            and isinstance(revision.get("characters"), int) and not isinstance(revision.get("characters"), bool)
            and revision.get("characters") == len(content)
            and revision.get("retracted") is (not bool(content.strip()))
            and revision.get("content_sha256") == hashlib.sha256(content.encode("utf-8")).hexdigest()
        )
    result["content"] = content.strip() if valid else ""
    result["effective_content_status"] = (
        "revised" if valid and result["content"] else "retracted" if valid else "unavailable"
    )
    if not result["content"]:
        result["include_in_plaintext"] = False
    return result


def conversation_turns(turns: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """The conversation as people read it: one row per spoken line.

    The buffer records every STT interim as its own caller row
    (``is_final=False``, event_type ``update``) so live consumers can react to
    partial speech. Stored and shown as-is, a caller who said one number read
    "It's", "It's plus", "It's plus nine", ... as a dozen lines (2026-09-28,
    call b847f447: 132 rows for 24 turns).

    Within each run of consecutive caller rows for the same turn: keep the
    final recognitions (dropping exact repeats), or — if the line never
    finalised, e.g. the caller hung up mid-sentence — the latest partial.
    Rows with no ``is_final`` (older calls, agent lines) count as final.
    """
    if isinstance(turns, dict):
        turns = turns.get("turns")
    if not isinstance(turns, list):
        return []
    out: List[Dict[str, Any]] = []
    group: List[Dict[str, Any]] = []

    def flush() -> None:
        if not group:
            return
        finals = [t for t in group if t.get("is_final") is not False]
        kept = finals or [group[-1]]
        for turn in kept:
            text = str(turn.get("content") or "").strip()
            previous = out[-1] if out else None
            metadata = turn.get("metadata") if isinstance(turn.get("metadata"), dict) else {}
            previous_metadata = previous.get("metadata") if previous and isinstance(previous.get("metadata"), dict) else {}
            if (
                previous is not None
                and previous.get("effective_content_status") == turn.get("effective_content_status")
                and previous.get("role") == turn.get("role")
                and previous_metadata.get("provider_item_id") == metadata.get("provider_item_id")
                and previous_metadata.get("caller_turn_order") == metadata.get("caller_turn_order")
                and previous_metadata.get("asr_latest_revision") == metadata.get("asr_latest_revision")
                and str(previous.get("content") or "").strip() == text
            ):
                continue
            out.append(turn)
        group.clear()

    for turn in turns or []:
        if not isinstance(turn, dict):
            continue
        turn = effective_turn(turn)
        if turn.get("role") != "user":
            flush()
            out.append(turn)
            continue
        if group and group[-1].get("turn_index") != turn.get("turn_index"):
            flush()
        group.append(turn)
    flush()
    return out


def transcript_text_from_turns(turns) -> str:
    """One canonical readable projection shared by storage, APIs and summaries."""
    return "\n".join(
        f"{'User' if turn.get('role') == 'user' else 'Assistant'}: {turn['content']}"
        for turn in conversation_turns(turns)
        if turn.get("include_in_plaintext", True) and str(turn.get("content") or "").strip()
    )


class TranscriptService:
    """
    Handles transcript accumulation and storage.
    
    Provider-agnostic service that works with any voice pipeline.
    Accumulates conversation turns and saves to database.
    
    Uses class-level storage for singleton-like access across
    different instances during a call lifecycle.
    """
    
    # Class-level storage for transcript buffers
    # This allows multiple instances to share the same data
    _buffers: Dict[str, List[TranscriptTurn]] = {}
    _call_bindings: Dict[str, str] = {}
    # Calls whose final transcript the hangup persister has written. Live call
    # 4291700f (2026-09-24 11:18:14): the hangup save wrote 34 turns / 399
    # words, then 44 ms later a late STT final ("Yes. But") started a fresh
    # buffer and the per-turn flush wrote that ONE line over the whole
    # transcript — so the lead gate then saw caller_turns=1 and refused to
    # mark a qualified lead. Same shape on 08-12, 08-20 and 09-10. Once
    # sealed, a call's buffer takes no more turns and flushes nothing.
    _sealed: Dict[str, None] = {}
    _SEALED_MAX = 5000
    _persist_locks = WeakValueDictionary()
    _failed_finalizations: OrderedDict[str, float] = OrderedDict()
    _FAILED_RETAIN_MAX = 64
    _FAILED_RETAIN_TTL_S = 300.0
    _FAILED_RETAIN_MAX_CHARS = 2_000_000

    def persistence_lock(self, call_id: str):
        lock = self._persist_locks.get(call_id)
        if lock is None:
            lock = asyncio.Lock()
            self._persist_locks[call_id] = lock
        return lock

    def freeze(self, call_id: str) -> None:
        """Stop late ASR intake without discarding the final snapshot."""
        self._sealed[call_id] = None
        if len(self._sealed) > self._SEALED_MAX:
            self._sealed.pop(next(iter(self._sealed)), None)

    def _evict_failed_finalizations(self) -> None:
        now = time.monotonic()
        def size():
            return sum(len(turn.content) + len(str(turn.metadata))
                       for key in self._failed_finalizations
                       for turn in self._buffers.get(key, ()))
        while self._failed_finalizations:
            oldest, timestamp = next(iter(self._failed_finalizations.items()))
            if (now - timestamp <= self._FAILED_RETAIN_TTL_S
                    and len(self._failed_finalizations) <= self._FAILED_RETAIN_MAX
                    and size() <= self._FAILED_RETAIN_MAX_CHARS):
                break
            self._failed_finalizations.pop(oldest, None)
            self.clear_buffer(oldest)
            logger.warning("unsaved_transcript_memory_evicted call=%s durable_recovery=false", oldest)

    def retain_failed_finalization(self, call_id: str) -> None:
        self.freeze(call_id)
        self._failed_finalizations[call_id] = time.monotonic()
        self._failed_finalizations.move_to_end(call_id)
        self._evict_failed_finalizations()

    @staticmethod
    def _resolve_pool(db_client, db_pool):
        """Return an asyncpg pool from an explicit ``db_pool``, a postgres-
        adapter ``db_client`` (``.pool``), or the DI container — whichever is
        available. ``None`` when the DB layer isn't initialised (tests, early
        startup) so callers fail soft instead of raising."""
        if db_pool is not None:
            return db_pool
        pool = getattr(db_client, "pool", None)
        if pool is not None:
            return pool
        try:
            from app.core.container import get_container
            container = get_container()
            if getattr(container, "is_initialized", False):
                return getattr(container, "db_pool", None)
        except Exception:
            return None
        return None

    async def _write_calls_transcript(
        self, pool, row_id: str, transcript_text: str,
        transcript_json: List[Dict[str, Any]], talklee_call_id: Optional[str],
        *, tenant_id: str, final: bool = False, metrics=None,
    ) -> Optional[str]:
        """Commit an explicitly owned snapshot; return its durable row identity.

        The calls-row lock serializes final jobs across workers. A completed
        snapshot fences late incremental writes. Historical duplicate child
        rows are retained; subsequent finalization updates the newest child.
        """
        from app.core.db_utils import acquire_with_tenant

        if not tenant_id:
            return None
        turns_jsonb = json.dumps(transcript_json)
        async with acquire_with_tenant(pool, tenant_id) as conn:
            row = await conn.fetchrow(
                "SELECT id, transcript_save_state FROM calls "
                "WHERE id=$1::uuid AND tenant_id=$2::uuid FOR UPDATE",
                row_id, tenant_id,
            )
            if row is None:
                return None
            if row["transcript_save_state"] == "complete":
                return str(row["id"]) if final else None
            if not transcript_json:
                return None  # no in-memory evidence is not a complete empty call
            written = await conn.fetchval(
                "UPDATE calls SET transcript=$1, transcript_json=$2::jsonb, "
                "talklee_call_id=COALESCE($3,talklee_call_id), "
                "transcript_save_state=$4, updated_at=NOW() "
                "WHERE id=$5::uuid AND tenant_id=$6::uuid RETURNING id",
                transcript_text, turns_jsonb, talklee_call_id,
                "complete" if final else "partial", row_id, tenant_id,
            )
            if written is None:
                return None
            if not final:
                return str(written)
            child = await conn.fetchval(
                "SELECT id FROM transcripts WHERE call_id=$1::uuid AND tenant_id=$2::uuid "
                "ORDER BY updated_at DESC, id DESC LIMIT 1 FOR UPDATE", row_id, tenant_id,
            )
            metrics = metrics or {}
            args = [turns_jsonb, transcript_text, metrics.get("word_count", 0),
                    metrics.get("turn_count", 0), metrics.get("user_word_count", 0),
                    metrics.get("assistant_word_count", 0)]
            if child:
                saved = await conn.fetchval(
                    "UPDATE transcripts SET turns=$1::jsonb,full_text=$2,word_count=$3,"
                    "turn_count=$4,user_word_count=$5,assistant_word_count=$6,updated_at=NOW() "
                    "WHERE id=$7::uuid AND tenant_id=$8::uuid AND call_id=$9::uuid RETURNING id",
                    *args, str(child), tenant_id, row_id,
                )
            else:
                saved = await conn.fetchval(
                    "INSERT INTO transcripts(turns,full_text,word_count,turn_count,"
                    "user_word_count,assistant_word_count,call_id,tenant_id) "
                    "VALUES($1::jsonb,$2,$3,$4,$5,$6,$7::uuid,$8::uuid) RETURNING id",
                    *args, row_id, tenant_id,
                )
            if saved is None:
                raise RuntimeError("Transcript child write was not acknowledged")
            return str(saved)

    def accumulate_turn(
        self, 
        call_id: str, 
        role: str, 
        content: str,
        confidence: Optional[float] = None,
        talklee_call_id: Optional[str] = None,
        turn_index: Optional[int] = None,
        event_type: Optional[str] = None,
        is_final: Optional[bool] = None,
        audio_window_start: Optional[str] = None,
        audio_window_end: Optional[str] = None,
        include_in_plaintext: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[TranscriptTurn]:
        """
        Add a turn to the transcript buffer.
        
        Args:
            call_id: Call identifier
            role: Speaker role ("user" or "assistant")
            content: The spoken/generated text
            confidence: Optional STT confidence score
        """
        if not isinstance(content, str):
            return None
        from app.domain.services.explicit_secrets import sanitize_explicit_secrets, sanitize_transcript_metadata
        content = sanitize_explicit_secrets(content)
        metadata = sanitize_transcript_metadata(metadata)
        if not content.strip():
            # Keep an owned empty final as audit evidence so a later revision
            # can amend that exact provider item, without inventing speech.
            owner = metadata if isinstance(metadata, dict) else {}
            item, order = owner.get("provider_item_id"), owner.get("caller_turn_order")
            if not (role == "user" and is_final is True
                    and isinstance(item, str) and 0 < len(item.strip()) <= 256
                    and isinstance(order, int) and not isinstance(order, bool) and order >= 0):
                return None
        if call_id in self._sealed:
            # The hangup persister already wrote the final transcript. A late
            # STT final landing here would start a fresh one-line buffer that
            # the next per-turn flush writes OVER the full transcript.
            return

        if call_id not in self._buffers:
            self._buffers[call_id] = []

        resolved_talklee_call_id = self._resolve_talklee_call_id(
            call_id=call_id,
            talklee_call_id=talklee_call_id,
        )
        resolved_event_type = event_type or "utterance"
        
        turn = TranscriptTurn(
            role=role,
            content=content.strip(),
            timestamp=datetime.utcnow().isoformat(),
            confidence=confidence,
            talklee_call_id=resolved_talklee_call_id,
            turn_index=turn_index,
            event_type=resolved_event_type,
            is_final=is_final,
            audio_window_start=audio_window_start,
            audio_window_end=audio_window_end,
            include_in_plaintext=include_in_plaintext,
            metadata=dict(metadata or {}),
        )
        
        self._buffers[call_id].append(turn)
        
        logger.debug(
            f"Transcript turn added for call {call_id}: "
            f"{role}: characters={len(content)}"
        )
        return turn

    def bind_caller_turn(self, call_id: str, turn: Optional[TranscriptTurn], *, caller_turn_order: int) -> bool:
        """Bind the exact accumulated final object at synchronous acceptance.

        Text matching is deliberately insufficient: repeated identical caller
        utterances and coalesced queued finals must retain separate identities.
        """
        if (call_id in self._sealed or turn is None
                or not any(item is turn for item in self._buffers.get(call_id, ()))
                or turn.role != "user" or turn.is_final is False
                or not isinstance(caller_turn_order, int) or isinstance(caller_turn_order, bool)
                or caller_turn_order < 0):
            return False
        identity = f"traditional:{caller_turn_order}"
        if turn.metadata.get("provider_item_id") not in (None, identity):
            return False
        turn.metadata.update(provider_item_id=identity, caller_turn_order=caller_turn_order)
        return True

    def caller_source(self, call_id: str, caller_turn_order: int) -> Optional[dict]:
        """Resolve a traditional accepted order to one actual saved caller row."""
        evidence = self.caller_evidence(call_id, caller_turn_order)
        return evidence["source"] if evidence is not None else None

    def caller_evidence(self, call_id: str, caller_turn_order: int) -> Optional[dict]:
        """Resolve text and source together before any conversational cleanup."""
        identity = f"traditional:{caller_turn_order}"
        matches = [turn for turn in self.get_transcript_json(call_id)
                   if turn.get("role") == "user" and turn.get("is_final") is not False
                   and (turn.get("metadata") or {}).get("provider_item_id") == identity
                   and (turn.get("metadata") or {}).get("caller_turn_order") == caller_turn_order]
        if len(matches) != 1:
            return None
        projected = matches[0]
        if not projected.get("content") or projected.get("effective_content_status") == "unavailable":
            return None
        return {"text": projected["content"], "source": {
            "provider_item_id": identity, "caller_turn_order": caller_turn_order,
            "revision_sha256": hashlib.sha256(projected["content"].encode("utf-8")).hexdigest(),
        }}

    def annotate_turn_revision(
        self, call_id: str, *, turn_index: int, provider_item_id: str,
        caller_turn_order: int, content: str,
    ) -> bool:
        """Retain the latest ASR revision as evidence, without rewriting speech.

        Canonical consumers validate and apply this metadata, retaining the
        original capture separately. Empty retractions are not utterances.
        """
        if (call_id in self._sealed or not isinstance(content, str)
                or not isinstance(provider_item_id, str)
                or not provider_item_id.strip() or len(provider_item_id) > 256
                or isinstance(turn_index, bool) or not isinstance(turn_index, int) or turn_index < 0
                or isinstance(caller_turn_order, bool) or not isinstance(caller_turn_order, int)
                or caller_turn_order < 0):
            return False
        from app.domain.services.explicit_secrets import sanitize_explicit_secrets
        originally_truncated = len(content) > 4096
        content = sanitize_explicit_secrets(content)
        for turn in reversed(self._buffers.get(call_id, ())):
            if (turn.role != "user" or turn.turn_index != turn_index
                    or turn.metadata.get("provider_item_id") != provider_item_id
                    or turn.metadata.get("caller_turn_order") != caller_turn_order):
                continue
            previous = turn.metadata.get("asr_latest_revision") or {}
            revision = previous.get("revision", 0) if isinstance(previous, dict) else 0
            revision = revision if isinstance(revision, int) and not isinstance(revision, bool) else 0
            turn.metadata["asr_latest_revision"] = {
                "revision": max(0, revision) + 1,
                "provider_item_id": provider_item_id,
                "caller_turn_order": caller_turn_order,
                "content": content[:4096],
                "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "characters": len(content),
                "truncated": originally_truncated or len(content) > 4096,
                "retracted": not content.strip(),
            }
            return True
        return False

    def bind_call_identity(self, call_id: str, talklee_call_id: Optional[str]) -> None:
        """Bind call_id to talklee_call_id for transcript integrity checks."""
        if not talklee_call_id:
            return
        existing = self._call_bindings.get(call_id)
        if existing and existing != talklee_call_id:
            logger.warning(
                "talklee_call_id mismatch for call %s (existing=%s incoming=%s)",
                call_id,
                existing,
                talklee_call_id,
            )
            return
        self._call_bindings[call_id] = talklee_call_id

    def _resolve_talklee_call_id(self, call_id: str, talklee_call_id: Optional[str]) -> Optional[str]:
        if talklee_call_id:
            self.bind_call_identity(call_id, talklee_call_id)
        return self._call_bindings.get(call_id, talklee_call_id)
    
    def get_turns(self, call_id: str) -> List[TranscriptTurn]:
        """Get all turns for a call."""
        self._evict_failed_finalizations()
        return self._buffers.get(call_id, [])
    
    def get_transcript_text(self, call_id: str) -> str:
        """
        Get plain text version of transcript.
        
        Format:
        User: Hello, I'm calling about...
        Assistant: Hi! I'd be happy to help...
        
        Args:
            call_id: Call identifier
            
        Returns:
            Formatted transcript text
        """
        turns = self.get_turns(call_id)
        if not turns:
            return ""
        
        return transcript_text_from_turns([turn.to_dict() for turn in turns])
    
    def get_transcript_json(self, call_id: str) -> List[Dict[str, Any]]:
        """
        Get JSON-serializable transcript data.
        
        Args:
            call_id: Call identifier
            
        Returns:
            List of turn dictionaries
        """
        turns = self.get_turns(call_id)
        return conversation_turns([turn.to_dict() for turn in turns])
    
    def get_metrics(self, call_id: str) -> Dict[str, int]:
        """
        Get transcript metrics.
        
        Args:
            call_id: Call identifier
            
        Returns:
            Dictionary with word counts and turn counts
        """
        turns_for_text = [t for t in self.get_transcript_json(call_id)
                          if t.get("include_in_plaintext", True) and t.get("content")]
        
        user_words = sum(
            len(t["content"].split()) for t in turns_for_text if t["role"] == "user"
        )
        assistant_words = sum(
            len(t["content"].split()) for t in turns_for_text if t["role"] == "assistant"
        )
        
        return {
            "turn_count": len(turns_for_text),
            "word_count": user_words + assistant_words,
            "user_word_count": user_words,
            "assistant_word_count": assistant_words
        }

    def build_integrity_report(
        self,
        call_id: str,
        expected_talklee_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build transcript integrity report for Day 7 gating."""
        turns = self.get_turns(call_id)
        event_counts: Dict[str, int] = {}
        missing_talklee = 0
        missing_turn_index = 0
        mismatched_talklee = 0

        for turn in turns:
            event_counts[turn.event_type] = event_counts.get(turn.event_type, 0) + 1
            if not turn.talklee_call_id:
                missing_talklee += 1
            if turn.turn_index is None:
                missing_turn_index += 1
            if (
                expected_talklee_call_id
                and turn.talklee_call_id
                and turn.talklee_call_id != expected_talklee_call_id
            ):
                mismatched_talklee += 1

        final_user_turns = sum(
            1
            for turn in turns
            if turn.role == "user" and turn.event_type in {"end_of_turn", "utterance"} and bool(turn.content.strip())
        )

        return {
            "call_id": call_id,
            "talklee_call_id": self._call_bindings.get(call_id),
            "expected_talklee_call_id": expected_talklee_call_id,
            "total_turns": len(turns),
            "final_user_turns": final_user_turns,
            "missing_talklee_call_id_turns": missing_talklee,
            "missing_turn_index_turns": missing_turn_index,
            "mismatched_talklee_call_id_turns": mismatched_talklee,
            "event_type_counts": event_counts,
            "is_valid": missing_talklee == 0 and missing_turn_index == 0 and mismatched_talklee == 0,
        }
    
    async def flush_to_database(
        self, call_id: str, db_client=None, tenant_id: Optional[str] = None,
        talklee_call_id: Optional[str] = None, *, target_call_id: Optional[str] = None,
        db_pool=None,
    ) -> bool:
        """Save current progress under its tenant; never report a zero-row success.

        Snapshot inside the lock so queued flushes cannot overwrite a later
        revision with text captured before waiting. Failed writes keep memory
        for the next turn/finalizer; they are not a durable recovery promise.
        """
        pool = self._resolve_pool(db_client, db_pool)
        if pool is None or not tenant_id:
            return False
        async with self.persistence_lock(call_id):
            if call_id in self._sealed or not self.get_turns(call_id):
                return False
            try:
                saved = await asyncio.wait_for(self._write_calls_transcript(
                    pool, str(target_call_id or call_id), self.get_transcript_text(call_id),
                    self.get_transcript_json(call_id),
                    self._resolve_talklee_call_id(call_id, talklee_call_id) if not target_call_id else None,
                    tenant_id=str(tenant_id),
                ), timeout=3.0)
                return bool(saved)
            except Exception as exc:
                logger.warning("transcript_flush_failed call=%s error_type=%s", call_id, type(exc).__name__)
                return False

    async def save_transcript(
        self, call_id: str, db_client=None, tenant_id: Optional[str] = None,
        talklee_call_id: Optional[str] = None, *, target_call_id: Optional[str] = None,
        db_pool=None,
    ) -> Optional[str]:
        """Atomically save the final call and child snapshot, retrying boundedly.

        Successful commit seals and clears memory. Exhausted retries freeze and
        retain a bounded in-memory copy for another existing finalizer attempt.
        Eviction/process death loses unsaved text; the DB remains partial or
        unknown, never falsely complete.
        """
        pool = self._resolve_pool(db_client, db_pool)
        async with self.persistence_lock(call_id):
            self.freeze(call_id)
            try:
                if pool is None or not tenant_id:
                    self.retain_failed_finalization(call_id)
                    return None
                row_id = str(target_call_id or call_id)
                for attempt in range(3):
                    try:
                        saved = await asyncio.wait_for(self._write_calls_transcript(
                            pool, row_id, self.get_transcript_text(call_id),
                            self.get_transcript_json(call_id),
                            self._resolve_talklee_call_id(call_id, talklee_call_id) if not target_call_id else None,
                            tenant_id=str(tenant_id), final=True, metrics=self.get_metrics(call_id),
                        ), timeout=2.0)
                        if saved:
                            self.seal(call_id)
                            return saved
                        if not self.get_turns(call_id):
                            self.seal(call_id)
                            return None
                        break  # missing/foreign row is not a transient write error
                    except Exception as exc:
                        logger.warning("transcript_final_save_failed call=%s attempt=%d error_type=%s",
                                       call_id, attempt + 1, type(exc).__name__)
                        if attempt < 2:
                            await asyncio.sleep(0.05 * (attempt + 1))
                self.retain_failed_finalization(call_id)
                try:
                    from app.core.db_utils import acquire_with_tenant
                    async def mark_failed():
                        async with acquire_with_tenant(pool, str(tenant_id)) as conn:
                            await conn.execute(
                                "UPDATE calls SET transcript_save_state='failed' "
                                "WHERE id=$1::uuid AND tenant_id=$2::uuid AND transcript_save_state<>'complete'",
                                row_id, str(tenant_id),
                            )
                    await asyncio.wait_for(mark_failed(), timeout=1.0)
                except Exception:
                    pass  # prior partial/unknown is already the truthful durable floor
                return None
            finally:
                # Cancellation also retains only bounded, frozen process memory.
                if self._buffers.get(call_id):
                    self.retain_failed_finalization(call_id)

    def clear_buffer(self, call_id: str) -> None:
        """
        Clear transcript buffer for a call.
        
        Called after saving to free memory.
        
        Args:
            call_id: Call identifier
        """
        self._failed_finalizations.pop(call_id, None)
        if call_id in self._buffers:
            del self._buffers[call_id]
            self._call_bindings.pop(call_id, None)
            logger.debug(f"Transcript buffer cleared for call {call_id}")
    
    def seal(self, call_id: str) -> None:
        """Mark a call's transcript final and drop its buffer (see _sealed)."""
        self.clear_buffer(call_id)
        self.freeze(call_id)

    @classmethod
    def clear_all_buffers(cls) -> None:
        """Clear all transcript buffers (for testing/cleanup)."""
        cls._buffers.clear()
        cls._call_bindings.clear()
        cls._sealed.clear()
        cls._failed_finalizations.clear()
        cls._persist_locks.clear()
