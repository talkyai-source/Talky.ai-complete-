"""
Latency Tracker Service
Tracks end-to-end latency metrics for voice pipeline turns
"""
import logging
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class LatencyStage(Enum):
    """Stages in the voice pipeline for latency tracking."""
    SPEECH_END = "speech_end"
    LLM_START = "llm_start"
    LLM_END = "llm_end"
    TTS_START = "tts_start"
    TTS_END = "tts_end"
    AUDIO_START = "audio_start"


@dataclass
class LatencyMetrics:
    """
    Latency metrics for a single turn in the conversation.
    
    Tracks time from user speech end to AI audio response start.
    Target: < 500-700ms total latency.
    """
    call_id: str
    turn_id: int
    speech_end_time: Optional[datetime] = None
    llm_start_time: Optional[datetime] = None
    llm_end_time: Optional[datetime] = None
    tts_start_time: Optional[datetime] = None
    tts_end_time: Optional[datetime] = None
    audio_start_time: Optional[datetime] = None
    
    @property
    def total_latency_ms(self) -> Optional[float]:
        """
        Time from speech end to audio start (total round-trip).
        This is the key metric for user experience.
        """
        if self.speech_end_time and self.audio_start_time:
            delta = self.audio_start_time - self.speech_end_time
            return delta.total_seconds() * 1000
        return None
    
    @property
    def llm_latency_ms(self) -> Optional[float]:
        """Time spent in LLM processing."""
        if self.llm_start_time and self.llm_end_time:
            delta = self.llm_end_time - self.llm_start_time
            return delta.total_seconds() * 1000
        return None
    
    @property
    def tts_latency_ms(self) -> Optional[float]:
        """Time spent in TTS synthesis."""
        if self.tts_start_time and self.tts_end_time:
            delta = self.tts_end_time - self.tts_start_time
            return delta.total_seconds() * 1000
        return None
    
    @property
    def time_to_first_audio_ms(self) -> Optional[float]:
        """Time from TTS start to first audio chunk."""
        if self.tts_start_time and self.audio_start_time:
            delta = self.audio_start_time - self.tts_start_time
            return delta.total_seconds() * 1000
        return None
    
    @property
    def is_within_target(self) -> bool:
        """Check if total latency is within target (< 700ms)."""
        total = self.total_latency_ms
        return total is not None and total < 700
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for logging/API response."""
        return {
            "call_id": self.call_id,
            "turn_id": self.turn_id,
            "total_latency_ms": self.total_latency_ms,
            "llm_latency_ms": self.llm_latency_ms,
            "tts_latency_ms": self.tts_latency_ms,
            "time_to_first_audio_ms": self.time_to_first_audio_ms,
            "is_within_target": self.is_within_target,
            "timestamps": {
                "speech_end": self.speech_end_time.isoformat() if self.speech_end_time else None,
                "llm_start": self.llm_start_time.isoformat() if self.llm_start_time else None,
                "llm_end": self.llm_end_time.isoformat() if self.llm_end_time else None,
                "tts_start": self.tts_start_time.isoformat() if self.tts_start_time else None,
                "audio_start": self.audio_start_time.isoformat() if self.audio_start_time else None
            }
        }


class LatencyTracker:
    """
    Tracks latency metrics across voice pipeline stages.
    
    Usage:
        tracker = LatencyTracker()
        tracker.start_turn(call_id, turn_id)
        tracker.mark_llm_start(call_id)
        tracker.mark_llm_end(call_id)
        tracker.mark_tts_start(call_id)
        tracker.mark_audio_start(call_id)
        tracker.log_metrics(call_id)
    """
    
    def __init__(self):
        self._metrics: Dict[str, LatencyMetrics] = {}
        self._history: Dict[str, list] = {}  # call_id -> list of past metrics
    
    def start_turn(self, call_id: str, turn_id: int) -> None:
        """
        Start tracking a new turn (user finished speaking).
        
        Args:
            call_id: Call identifier
            turn_id: Turn number in the conversation
        """
        metrics = LatencyMetrics(
            call_id=call_id,
            turn_id=turn_id,
            speech_end_time=datetime.utcnow()
        )
        self._metrics[call_id] = metrics
        
        logger.debug(
            f"Latency tracking started for turn {turn_id}",
            extra={"call_id": call_id, "turn_id": turn_id}
        )
    
    def mark_llm_start(self, call_id: str) -> None:
        """Mark when LLM processing starts."""
        if call_id in self._metrics:
            self._metrics[call_id].llm_start_time = datetime.utcnow()
    
    def mark_llm_end(self, call_id: str) -> None:
        """Mark when LLM processing ends."""
        if call_id in self._metrics:
            self._metrics[call_id].llm_end_time = datetime.utcnow()
    
    def mark_tts_start(self, call_id: str) -> None:
        """Mark when TTS synthesis starts."""
        if call_id in self._metrics:
            self._metrics[call_id].tts_start_time = datetime.utcnow()
    
    def mark_tts_end(self, call_id: str) -> None:
        """Mark when TTS synthesis completes."""
        if call_id in self._metrics:
            self._metrics[call_id].tts_end_time = datetime.utcnow()
    
    def mark_audio_start(self, call_id: str) -> None:
        """Mark when first audio chunk is sent to caller."""
        if call_id in self._metrics:
            self._metrics[call_id].audio_start_time = datetime.utcnow()
    
    def get_metrics(self, call_id: str) -> Optional[LatencyMetrics]:
        """
        Get current latency metrics for a call.
        
        Args:
            call_id: Call identifier
            
        Returns:
            LatencyMetrics or None if not tracking
        """
        return self._metrics.get(call_id)
    
    def get_history(self, call_id: str) -> list[LatencyMetrics]:
        """
        Get historical latency metrics for a call.
        
        Args:
            call_id: Call identifier
            
        Returns:
            List of past LatencyMetrics
        """
        return self._history.get(call_id, [])
    
    def log_metrics(self, call_id: str) -> None:
        """
        Log latency metrics for the current turn.
        
        Archives current metrics and logs summary.
        
        Args:
            call_id: Call identifier
        """
        metrics = self._metrics.get(call_id)
        if not metrics:
            return
        
        # Archive to history
        if call_id not in self._history:
            self._history[call_id] = []
        self._history[call_id].append(metrics)
        
        # Log summary
        total = metrics.total_latency_ms
        status = "OK" if metrics.is_within_target else "SLOW"
        
        # Handle None values for formatting
        total_str = f"{total:.0f}" if total is not None else "N/A"
        llm_str = f"{metrics.llm_latency_ms:.0f}" if metrics.llm_latency_ms is not None else "0"
        tts_str = f"{metrics.tts_latency_ms:.0f}" if metrics.tts_latency_ms is not None else "0"
        
        logger.info(
            f"[{status}] Turn {metrics.turn_id} latency: {total_str}ms "
            f"(LLM: {llm_str}ms, TTS: {tts_str}ms)",
            extra={
                "call_id": call_id,
                "turn_id": metrics.turn_id,
                "total_latency_ms": total,
                "llm_latency_ms": metrics.llm_latency_ms,
                "tts_latency_ms": metrics.tts_latency_ms,
                "is_within_target": metrics.is_within_target
            }
        )
    
    def get_average_latency(self, call_id: str) -> Optional[float]:
        """
        Get average total latency across all turns for a call.
        
        Args:
            call_id: Call identifier
            
        Returns:
            Average latency in ms or None
        """
        history = self._history.get(call_id, [])
        if not history:
            return None
        
        latencies = [m.total_latency_ms for m in history if m.total_latency_ms]
        return sum(latencies) / len(latencies) if latencies else None
    
    def cleanup_call(self, call_id: str) -> None:
        """
        Clean up tracking data for a call.
        
        Args:
            call_id: Call identifier
        """
        if call_id in self._metrics:
            del self._metrics[call_id]
        if call_id in self._history:
            del self._history[call_id]
    
    def get_all_active_calls(self) -> Dict[str, LatencyMetrics]:
        """Get metrics for all active calls."""
        return self._metrics.copy()


# Global singleton instance
_tracker: Optional[LatencyTracker] = None


def get_latency_tracker() -> LatencyTracker:
    """Get the global latency tracker instance."""
    global _tracker
    if _tracker is None:
        _tracker = LatencyTracker()
    return _tracker
