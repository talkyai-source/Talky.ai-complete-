"""
Transcript Service
Handles transcript accumulation and storage for call conversations.
Provider-agnostic - works with any voice pipeline.
"""
import logging
import inspect
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

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

    async def _run_execute(self, query):
        """
        Execute a PostgreSQL query builder in sync/async compatible fashion.

        Some client variants return an awaitable from `.execute()`, while
        others return the response directly.
        """
        result = query.execute()
        if inspect.isawaitable(result):
            return await result
        return result
    
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
    ) -> None:
        """
        Add a turn to the transcript buffer.
        
        Args:
            call_id: Call identifier
            role: Speaker role ("user" or "assistant")
            content: The spoken/generated text
            confidence: Optional STT confidence score
        """
        if not content or not content.strip():
            return  # Skip empty content
        
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
            f"{role}: {content[:50]}..."
        )

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
        
        lines = []
        for turn in turns:
            if not turn.include_in_plaintext:
                continue
            role_label = "User" if turn.role == "user" else "Assistant"
            lines.append(f"{role_label}: {turn.content}")
        
        return "\n".join(lines)
    
    def get_transcript_json(self, call_id: str) -> List[Dict[str, Any]]:
        """
        Get JSON-serializable transcript data.
        
        Args:
            call_id: Call identifier
            
        Returns:
            List of turn dictionaries
        """
        turns = self.get_turns(call_id)
        return [turn.to_dict() for turn in turns]
    
    def get_metrics(self, call_id: str) -> Dict[str, int]:
        """
        Get transcript metrics.
        
        Args:
            call_id: Call identifier
            
        Returns:
            Dictionary with word counts and turn counts
        """
        turns = self.get_turns(call_id)
        turns_for_text = [t for t in turns if t.include_in_plaintext]
        
        user_words = sum(
            len(t.content.split()) for t in turns_for_text if t.role == "user"
        )
        assistant_words = sum(
            len(t.content.split()) for t in turns_for_text if t.role == "assistant"
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
        self,
        call_id: str,
        db_client,
        tenant_id: Optional[str] = None,
        talklee_call_id: Optional[str] = None,
    ) -> None:
        """
        Incrementally update calls.transcript with current buffer.
        
        Day 17: Called after each completed turn to persist progress.
        Does NOT clear the buffer (unlike save_transcript).
        
        Args:
            call_id: Call identifier
            db_client: PostgreSQL client
            tenant_id: Optional tenant identifier
        """
        turns = self.get_turns(call_id)
        if not turns:
            return
        
        try:
            transcript_text = self.get_transcript_text(call_id)
            transcript_json = self.get_transcript_json(call_id)

            resolved_talklee_call_id = self._resolve_talklee_call_id(call_id, talklee_call_id)
            call_update = {
                "transcript": transcript_text,
                "transcript_json": transcript_json,
                "updated_at": datetime.utcnow().isoformat(),
            }
            if resolved_talklee_call_id:
                call_update["talklee_call_id"] = resolved_talklee_call_id

            await self._run_execute(db_client.table("calls").update(call_update).eq("id", call_id))
            
            logger.debug(
                f"Flushed transcript for call {call_id}: {len(turns)} turns"
            )
            
        except Exception as e:
            logger.warning(f"Failed to flush transcript for {call_id}: {e}")
    
    async def save_transcript(
        self, 
        call_id: str, 
        db_client,
        tenant_id: Optional[str] = None,
        talklee_call_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Save accumulated transcript to database.
        
        Performs:
        1. Insert into transcripts table (structured)
        2. Update calls.transcript (plain text)
        3. Update calls.transcript_json (JSONB)
        
        Args:
            call_id: Call identifier
            db_client: PostgreSQL client
            tenant_id: Optional tenant identifier
            
        Returns:
            Transcript ID if successful, None otherwise
        """
        turns = self.get_turns(call_id)
        
        if not turns:
            logger.warning(f"No transcript to save for call {call_id}")
            return None
        
        try:
            # Get transcript data
            transcript_text = self.get_transcript_text(call_id)
            transcript_json = self.get_transcript_json(call_id)
            metrics = self.get_metrics(call_id)
            resolved_talklee_call_id = self._resolve_talklee_call_id(call_id, talklee_call_id)
            
            # Step 1: Insert into transcripts table
            transcript_insert = {
                "call_id": call_id,
                "tenant_id": tenant_id,
                "turns": transcript_json,
                "full_text": transcript_text,
                "word_count": metrics["word_count"],
                "turn_count": metrics["turn_count"],
                "user_word_count": metrics["user_word_count"],
                "assistant_word_count": metrics["assistant_word_count"],
            }
            if resolved_talklee_call_id:
                transcript_insert["talklee_call_id"] = resolved_talklee_call_id

            try:
                transcript_result = await self._run_execute(
                    db_client.table("transcripts").insert(transcript_insert)
                )
            except Exception as insert_error:
                if "talklee_call_id" in str(insert_error):
                    transcript_insert.pop("talklee_call_id", None)
                    transcript_result = await self._run_execute(
                        db_client.table("transcripts").insert(transcript_insert)
                    )
                else:
                    raise
            
            transcript_id = None
            if transcript_result.data and len(transcript_result.data) > 0:
                transcript_id = transcript_result.data[0].get("id")
            
            # Step 2 & 3: Update calls table with transcript
            call_update = {
                "transcript": transcript_text,
                "transcript_json": transcript_json,
                "updated_at": datetime.utcnow().isoformat(),
            }
            if resolved_talklee_call_id:
                call_update["talklee_call_id"] = resolved_talklee_call_id

            await self._run_execute(db_client.table("calls").update(call_update).eq("id", call_id))
            
            logger.info(
                f"Transcript saved for call {call_id}: "
                f"{metrics['turn_count']} turns, {metrics['word_count']} words"
            )
            
            return transcript_id
            
        except Exception as e:
            logger.error(f"Failed to save transcript for call {call_id}: {e}")
            return None
    
    def clear_buffer(self, call_id: str) -> None:
        """
        Clear transcript buffer for a call.
        
        Called after saving to free memory.
        
        Args:
            call_id: Call identifier
        """
        if call_id in self._buffers:
            del self._buffers[call_id]
            self._call_bindings.pop(call_id, None)
            logger.debug(f"Transcript buffer cleared for call {call_id}")
    
    @classmethod
    def clear_all_buffers(cls) -> None:
        """Clear all transcript buffers (for testing/cleanup)."""
        cls._buffers.clear()
        cls._call_bindings.clear()
