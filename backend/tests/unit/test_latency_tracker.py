"""
Unit Tests for Latency Tracker
Tests latency metrics tracking and logging
"""
import pytest
from datetime import datetime, timedelta
from app.domain.services.latency_tracker import (
    LatencyMetrics,
    LatencyTracker,
    get_latency_tracker
)


class TestLatencyMetrics:
    """Tests for LatencyMetrics dataclass."""
    
    def test_total_latency_calculation(self):
        """Test total latency is correctly calculated."""
        now = datetime.utcnow()
        
        metrics = LatencyMetrics(
            call_id="test-call",
            turn_id=1,
            speech_end_time=now,
            audio_start_time=now + timedelta(milliseconds=500)
        )
        
        assert metrics.total_latency_ms == pytest.approx(500, abs=1)
    
    def test_llm_latency_calculation(self):
        """Test LLM latency is correctly calculated."""
        now = datetime.utcnow()
        
        metrics = LatencyMetrics(
            call_id="test-call",
            turn_id=1,
            llm_start_time=now,
            llm_end_time=now + timedelta(milliseconds=200)
        )
        
        assert metrics.llm_latency_ms == pytest.approx(200, abs=1)
    
    def test_tts_latency_calculation(self):
        """Test TTS latency is correctly calculated."""
        now = datetime.utcnow()
        
        metrics = LatencyMetrics(
            call_id="test-call",
            turn_id=1,
            tts_start_time=now,
            tts_end_time=now + timedelta(milliseconds=100)
        )
        
        assert metrics.tts_latency_ms == pytest.approx(100, abs=1)
    
    def test_within_target_true(self):
        """Test target check passes for fast response."""
        now = datetime.utcnow()
        
        metrics = LatencyMetrics(
            call_id="test-call",
            turn_id=1,
            speech_end_time=now,
            audio_start_time=now + timedelta(milliseconds=400)
        )
        
        assert metrics.is_within_target == True
    
    def test_within_target_false(self):
        """Test target check fails for slow response."""
        now = datetime.utcnow()
        
        metrics = LatencyMetrics(
            call_id="test-call",
            turn_id=1,
            speech_end_time=now,
            audio_start_time=now + timedelta(milliseconds=800)
        )
        
        assert metrics.is_within_target == False
    
    def test_to_dict(self):
        """Test dictionary conversion."""
        now = datetime.utcnow()
        
        metrics = LatencyMetrics(
            call_id="test-call",
            turn_id=1,
            speech_end_time=now,
            audio_start_time=now + timedelta(milliseconds=500)
        )
        
        d = metrics.to_dict()
        
        assert d["call_id"] == "test-call"
        assert d["turn_id"] == 1
        assert d["total_latency_ms"] == pytest.approx(500, abs=1)
        assert "timestamps" in d


class TestLatencyTracker:
    """Tests for LatencyTracker class."""
    
    def test_start_turn(self):
        """Test starting a new turn."""
        tracker = LatencyTracker()
        
        tracker.start_turn("call-1", turn_id=1)
        
        metrics = tracker.get_metrics("call-1")
        assert metrics is not None
        assert metrics.call_id == "call-1"
        assert metrics.turn_id == 1
        assert metrics.speech_end_time is not None
    
    def test_mark_stages(self):
        """Test marking various pipeline stages."""
        tracker = LatencyTracker()
        
        tracker.start_turn("call-1", turn_id=1)
        tracker.mark_llm_start("call-1")
        tracker.mark_llm_end("call-1")
        tracker.mark_tts_start("call-1")
        tracker.mark_audio_start("call-1")
        
        metrics = tracker.get_metrics("call-1")
        
        assert metrics.speech_end_time is not None
        assert metrics.llm_start_time is not None
        assert metrics.llm_end_time is not None
        assert metrics.tts_start_time is not None
        assert metrics.audio_start_time is not None
    
    def test_log_metrics(self):
        """Test logging metrics archives to history."""
        tracker = LatencyTracker()
        
        tracker.start_turn("call-1", turn_id=1)
        tracker.mark_audio_start("call-1")
        tracker.log_metrics("call-1")
        
        history = tracker.get_history("call-1")
        assert len(history) == 1
        assert history[0].turn_id == 1
    
    def test_average_latency(self):
        """Test average latency calculation."""
        tracker = LatencyTracker()
        now = datetime.utcnow()
        
        # Simulate 3 turns
        for i in range(3):
            tracker.start_turn("call-1", turn_id=i + 1)
            metrics = tracker.get_metrics("call-1")
            # Manually set timestamps for testing
            metrics.speech_end_time = now
            metrics.audio_start_time = now + timedelta(milliseconds=400 + i * 100)
            tracker.log_metrics("call-1")
        
        avg = tracker.get_average_latency("call-1")
        
        # (400 + 500 + 600) / 3 = 500
        assert avg == pytest.approx(500, abs=10)
    
    def test_cleanup_call(self):
        """Test cleaning up call data."""
        tracker = LatencyTracker()
        
        tracker.start_turn("call-1", turn_id=1)
        tracker.log_metrics("call-1")
        
        assert tracker.get_metrics("call-1") is not None
        
        tracker.cleanup_call("call-1")
        
        assert tracker.get_metrics("call-1") is None
        assert tracker.get_history("call-1") == []
    
    def test_multiple_calls(self):
        """Test tracking multiple calls simultaneously."""
        tracker = LatencyTracker()
        
        tracker.start_turn("call-1", turn_id=1)
        tracker.start_turn("call-2", turn_id=1)
        tracker.start_turn("call-3", turn_id=1)
        
        active = tracker.get_all_active_calls()
        
        assert len(active) == 3
        assert "call-1" in active
        assert "call-2" in active
        assert "call-3" in active


class TestGlobalTracker:
    """Tests for global tracker singleton."""
    
    def test_get_latency_tracker_returns_same_instance(self):
        """Test singleton pattern."""
        tracker1 = get_latency_tracker()
        tracker2 = get_latency_tracker()
        
        assert tracker1 is tracker2
