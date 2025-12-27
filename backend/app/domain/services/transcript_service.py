"""
Transcript Service
Handles transcript accumulation and storage for call conversations.
Provider-agnostic - works with any voice pipeline.
"""
import logging
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
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "confidence": self.confidence
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
    
    def accumulate_turn(
        self, 
        call_id: str, 
        role: str, 
        content: str,
        confidence: Optional[float] = None
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
        
        turn = TranscriptTurn(
            role=role,
            content=content.strip(),
            timestamp=datetime.utcnow().isoformat(),
            confidence=confidence
        )
        
        self._buffers[call_id].append(turn)
        
        logger.debug(
            f"Transcript turn added for call {call_id}: "
            f"{role}: {content[:50]}..."
        )
    
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
        
        user_words = sum(
            len(t.content.split()) for t in turns if t.role == "user"
        )
        assistant_words = sum(
            len(t.content.split()) for t in turns if t.role == "assistant"
        )
        
        return {
            "turn_count": len(turns),
            "word_count": user_words + assistant_words,
            "user_word_count": user_words,
            "assistant_word_count": assistant_words
        }
    
    async def flush_to_database(
        self,
        call_id: str,
        supabase_client,
        tenant_id: Optional[str] = None
    ) -> None:
        """
        Incrementally update calls.transcript with current buffer.
        
        Day 17: Called after each completed turn to persist progress.
        Does NOT clear the buffer (unlike save_transcript).
        
        Args:
            call_id: Call identifier
            supabase_client: Supabase client
            tenant_id: Optional tenant identifier
        """
        turns = self.get_turns(call_id)
        if not turns:
            return
        
        try:
            transcript_text = self.get_transcript_text(call_id)
            transcript_json = self.get_transcript_json(call_id)
            
            supabase_client.table("calls").update({
                "transcript": transcript_text,
                "transcript_json": transcript_json,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", call_id).execute()
            
            logger.debug(
                f"Flushed transcript for call {call_id}: {len(turns)} turns"
            )
            
        except Exception as e:
            logger.warning(f"Failed to flush transcript for {call_id}: {e}")
    
    async def save_transcript(
        self, 
        call_id: str, 
        supabase_client,
        tenant_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Save accumulated transcript to database.
        
        Performs:
        1. Insert into transcripts table (structured)
        2. Update calls.transcript (plain text)
        3. Update calls.transcript_json (JSONB)
        
        Args:
            call_id: Call identifier
            supabase_client: Supabase client
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
            
            # Step 1: Insert into transcripts table
            transcript_result = supabase_client.table("transcripts").insert({
                "call_id": call_id,
                "tenant_id": tenant_id,
                "turns": transcript_json,
                "full_text": transcript_text,
                "word_count": metrics["word_count"],
                "turn_count": metrics["turn_count"],
                "user_word_count": metrics["user_word_count"],
                "assistant_word_count": metrics["assistant_word_count"]
            }).execute()
            
            transcript_id = None
            if transcript_result.data and len(transcript_result.data) > 0:
                transcript_id = transcript_result.data[0].get("id")
            
            # Step 2 & 3: Update calls table with transcript
            supabase_client.table("calls").update({
                "transcript": transcript_text,
                "transcript_json": transcript_json,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", call_id).execute()
            
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
            logger.debug(f"Transcript buffer cleared for call {call_id}")
    
    @classmethod
    def clear_all_buffers(cls) -> None:
        """Clear all transcript buffers (for testing/cleanup)."""
        cls._buffers.clear()
