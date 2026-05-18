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

    def test_ws_d_stage_latencies(self):
        """Test WS-D stage latency calculations."""
        now = datetime.utcnow()

        metrics = LatencyMetrics(
            call_id="test-call",
            turn_id=1,
            listening_start_time=now,
            stt_first_transcript_time=now + timedelta(milliseconds=120),
            speech_end_time=now + timedelta(milliseconds=700),
            llm_start_time=now + timedelta(milliseconds=710),
            llm_first_token_time=now + timedelta(milliseconds=860),
            tts_start_time=now + timedelta(milliseconds=980),
            tts_first_chunk_time=now + timedelta(milliseconds=1110),
            response_start_time=now + timedelta(milliseconds=1110),
        )

        assert metrics.stt_first_transcript_ms == pytest.approx(120, abs=1)
        assert metrics.llm_first_token_ms == pytest.approx(150, abs=1)
        assert metrics.tts_first_chunk_ms == pytest.approx(130, abs=1)
        assert metrics.response_start_latency_ms == pytest.approx(410, abs=1)


class TestLatencyTracker:
    """Tests for LatencyTracker class."""
    
    def test_start_turn(self):
        """Test starting a new turn — speech_end_time must NOT be pre-populated."""
        tracker = LatencyTracker()

        tracker.start_turn("call-1", turn_id=1)

        metrics = tracker.get_metrics("call-1")
        assert metrics is not None
        assert metrics.call_id == "call-1"
        assert metrics.turn_id == 1
        assert metrics.listening_start_time is not None
        # speech_end_time must only be set via mark_speech_end, never by start_turn
        assert metrics.speech_end_time is None, (
            "start_turn must not pre-populate speech_end_time — "
            "total_latency_ms must measure from actual speech-end, not listening-start"
        )

    def test_start_turn_same_turn_preserves_existing_metrics(self):
        """Restarting the same turn must not wipe timestamps already captured."""
        tracker = LatencyTracker()
        tracker.start_turn("call-1", turn_id=1)

        metrics = tracker.get_metrics("call-1")
        assert metrics is not None
        original_listening_start = metrics.listening_start_time
        metrics.stt_first_transcript_time = datetime.utcnow()

        tracker.start_turn("call-1", turn_id=1)

        metrics = tracker.get_metrics("call-1")
        assert metrics is not None
        assert metrics.listening_start_time == original_listening_start
        assert metrics.stt_first_transcript_time is not None

    def test_mark_stages(self):
        """Test marking various pipeline stages."""
        tracker = LatencyTracker()

        tracker.start_turn("call-1", turn_id=1)
        tracker.mark_speech_end("call-1")    # must be called explicitly
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

    def test_interrupted_turn_outcome_is_preserved(self):
        """Interrupted turns should not later be overwritten as completed."""
        tracker = LatencyTracker()

        tracker.start_turn("call-1", turn_id=1)
        tracker.mark_interrupted("call-1", reason="barge_in")
        tracker.mark_completed("call-1")

        metrics = tracker.get_metrics("call-1")
        assert metrics is not None
        assert metrics.turn_outcome == "interrupted"
        assert metrics.interruption_reason == "barge_in"
    
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

    def test_percentiles_and_baseline_snapshot(self):
        """Test WS-D percentile helpers for baseline reporting."""
        tracker = LatencyTracker()
        now = datetime.utcnow()

        values = [300, 500, 900]
        for idx, response_latency in enumerate(values, start=1):
            tracker.start_turn("call-wsd", turn_id=idx)
            metrics = tracker.get_metrics("call-wsd")
            assert metrics is not None
            metrics.listening_start_time = now
            metrics.stt_first_transcript_time = now + timedelta(milliseconds=100)
            metrics.speech_end_time = now
            metrics.llm_start_time = now + timedelta(milliseconds=10)
            metrics.llm_first_token_time = now + timedelta(milliseconds=160)
            metrics.tts_start_time = now + timedelta(milliseconds=170)
            metrics.tts_first_chunk_time = now + timedelta(milliseconds=290)
            metrics.response_start_time = now + timedelta(milliseconds=response_latency)
            metrics.audio_start_time = metrics.response_start_time
            tracker.log_metrics("call-wsd")

        percentiles = tracker.get_percentiles("call-wsd", "response_start_latency_ms")
        assert percentiles[50] == pytest.approx(500, abs=1)
        assert percentiles[95] == pytest.approx(860, abs=1)

        snapshot = tracker.build_baseline_snapshot("call-wsd")
        assert "response_start_latency_ms" in snapshot
        assert snapshot["response_start_latency_ms"][50] == pytest.approx(500, abs=1)


class TestFirstTurnLatencyLog:
    """Tests for log_first_turn_if_applicable — the once-per-call signal
    that operators use to slice cold-start latency from steady state."""

    def _completed_metrics(self, tracker: LatencyTracker, call_id: str) -> None:
        """Walk a call through the marks needed for a 'completed' first turn."""
        tracker.start_turn(call_id, turn_id=0)
        m = tracker.get_metrics(call_id)
        # Use synthetic timestamps so the log-line numbers are deterministic.
        base = datetime.utcnow()
        m.speech_end_time = base
        m.llm_start_time = base + timedelta(milliseconds=10)
        m.llm_first_token_time = base + timedelta(milliseconds=120)
        m.llm_end_time = base + timedelta(milliseconds=300)
        m.tts_start_time = base + timedelta(milliseconds=130)
        m.tts_first_chunk_time = base + timedelta(milliseconds=250)
        m.tts_end_time = base + timedelta(milliseconds=600)
        m.audio_start_time = base + timedelta(milliseconds=260)
        tracker.mark_completed(call_id)

    def test_emits_once_then_silent(self, caplog):
        tracker = LatencyTracker()
        self._completed_metrics(tracker, "call-x")

        with caplog.at_level("INFO", logger="app.domain.services.latency_tracker"):
            tracker.log_first_turn_if_applicable(
                "call-x", mode="user", prompt_kind="inbound",
            )
            tracker.log_first_turn_if_applicable(
                "call-x", mode="user", prompt_kind="inbound",
            )

        first_turn_logs = [r for r in caplog.records if "first_turn_latency" in r.getMessage()]
        assert len(first_turn_logs) == 1
        msg = first_turn_logs[0].getMessage()
        assert "mode=user" in msg
        assert "prompt_kind=inbound" in msg
        # Latency math: speech_end → audio_start = 260ms.
        assert "speech_to_audio_ms=260" in msg

    def test_skips_interrupted_turn(self, caplog):
        tracker = LatencyTracker()
        self._completed_metrics(tracker, "call-y")
        tracker.mark_interrupted("call-y", reason="barge_in")  # overrides outcome

        with caplog.at_level("INFO", logger="app.domain.services.latency_tracker"):
            tracker.log_first_turn_if_applicable(
                "call-y", mode="agent", prompt_kind="outbound",
            )

        first_turn_logs = [r for r in caplog.records if "first_turn_latency" in r.getMessage()]
        assert first_turn_logs == []

    def test_skips_when_no_audio_was_sent(self, caplog):
        """A turn that produced no TTS audio is not first-turn-loggable —
        we want the signal to mean 'caller actually heard a reply'."""
        tracker = LatencyTracker()
        tracker.start_turn("call-z", turn_id=0)
        tracker.mark_speech_end("call-z")
        tracker.mark_completed("call-z")  # but no audio_start set

        with caplog.at_level("INFO", logger="app.domain.services.latency_tracker"):
            tracker.log_first_turn_if_applicable(
                "call-z", mode="user", prompt_kind="outbound",
            )

        first_turn_logs = [r for r in caplog.records if "first_turn_latency" in r.getMessage()]
        assert first_turn_logs == []

    def test_cleanup_call_resets_first_turn_state(self, caplog):
        """A recycled call_id (rare but possible) must be allowed to log
        again after cleanup_call clears its bookkeeping."""
        tracker = LatencyTracker()
        self._completed_metrics(tracker, "call-r")

        with caplog.at_level("INFO", logger="app.domain.services.latency_tracker"):
            tracker.log_first_turn_if_applicable("call-r", mode="user", prompt_kind="inbound")
            tracker.cleanup_call("call-r")
            self._completed_metrics(tracker, "call-r")
            tracker.log_first_turn_if_applicable("call-r", mode="user", prompt_kind="inbound")

        first_turn_logs = [r for r in caplog.records if "first_turn_latency" in r.getMessage()]
        assert len(first_turn_logs) == 2


class TestGlobalTracker:
    """Tests for global tracker singleton."""

    def test_get_latency_tracker_returns_same_instance(self):
        """Test singleton pattern."""
        tracker1 = get_latency_tracker()
        tracker2 = get_latency_tracker()

        assert tracker1 is tracker2
