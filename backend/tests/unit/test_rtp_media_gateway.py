"""
Tests for Day 42: RTP Media Gateway — Default Linux Media Path

Tests cover:
- RTPFlowMonitor dataclass (first packet, silence, flow status)
- RTPMediaGateway lifecycle (init, call start/end, cleanup)
- RTP flow monitoring wired into on_audio_received
- media_started callback fires on first packet
- check_media_flow() status dict
- send_audio pipeline (PCM → G.711 → RTP → UDP)
"""
import asyncio
import struct
import time
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from app.infrastructure.telephony.rtp_media_gateway import (
    RTPMediaGateway,
    RTPFlowMonitor,
    RTPSession,
)
from app.utils.rtp_builder import RTPPacket, RTPPacketBuilder, PayloadType


# =============================================================================
# RTPFlowMonitor
# =============================================================================


class TestRTPFlowMonitor:
    """Tests for the RTPFlowMonitor dataclass."""

    def test_initial_state(self):
        """New monitor should report no flow and silent call."""
        fm = RTPFlowMonitor()
        assert fm.first_packet_at is None
        assert fm.last_packet_at is None
        assert fm.is_silent_call is True
        assert fm.is_media_flowing is False

    def test_record_first_packet(self):
        """First call to record_packet should return True and set timestamps."""
        fm = RTPFlowMonitor()
        is_first = fm.record_packet()
        assert is_first is True
        assert fm.first_packet_at is not None
        assert fm.last_packet_at is not None
        assert fm.is_silent_call is False

    def test_record_subsequent_packet(self):
        """Second call to record_packet should return False."""
        fm = RTPFlowMonitor()
        fm.record_packet()  # first
        is_first = fm.record_packet()  # second
        assert is_first is False

    def test_is_media_flowing_after_packet(self):
        """Media should be flowing immediately after a packet."""
        fm = RTPFlowMonitor()
        fm.record_packet()
        assert fm.is_media_flowing is True

    def test_is_media_flowing_after_threshold(self):
        """Media should stop flowing after silence_threshold_ms expires."""
        fm = RTPFlowMonitor(silence_threshold_ms=100.0)
        fm.record_packet()
        # Simulate the threshold having elapsed by back-dating last_packet_at
        fm.last_packet_at = datetime.now(timezone.utc) - timedelta(milliseconds=200)
        assert fm.is_media_flowing is False

    def test_custom_threshold(self):
        """Custom threshold should be respected."""
        fm = RTPFlowMonitor(silence_threshold_ms=10_000.0)
        fm.record_packet()
        fm.last_packet_at = datetime.now(timezone.utc) - timedelta(milliseconds=5000)
        # 5s < 10s threshold — still flowing
        assert fm.is_media_flowing is True


# =============================================================================
# RTPMediaGateway — Lifecycle
# =============================================================================


@pytest.mark.asyncio
class TestRTPMediaGatewayLifecycle:
    """Tests for RTPMediaGateway init / call lifecycle."""

    async def test_initialization(self):
        """Gateway initialises with config values."""
        gw = RTPMediaGateway()
        await gw.initialize({
            "remote_ip": "10.0.0.1",
            "remote_port": 6000,
            "codec": "alaw",
            "source_sample_rate": 16000,
            "source_format": "pcm_s16le",
        })
        assert gw._default_remote_ip == "10.0.0.1"
        assert gw._default_remote_port == 6000
        assert gw._default_codec == "alaw"
        assert gw._source_sample_rate == 16000
        assert gw.name == "rtp"

    async def test_call_started_creates_session(self):
        """on_call_started should create an RTPSession with a flow monitor."""
        gw = RTPMediaGateway()
        await gw.initialize({})

        await gw.on_call_started("call-1", {"remote_ip": "127.0.0.1", "remote_port": 5004})

        session = gw.get_session("call-1")
        assert session is not None
        assert session.call_id == "call-1"
        assert isinstance(session.flow_monitor, RTPFlowMonitor)
        assert session.flow_monitor.is_silent_call is True

    async def test_call_ended_removes_session(self):
        """on_call_ended should clean up the session and close socket."""
        gw = RTPMediaGateway()
        await gw.initialize({})
        await gw.on_call_started("call-1", {})

        assert gw.get_session("call-1") is not None
        await gw.on_call_ended("call-1", "hangup")
        assert gw.get_session("call-1") is None

    async def test_cleanup_closes_all_sessions(self):
        """cleanup() should close all sockets and clear sessions."""
        gw = RTPMediaGateway()
        await gw.initialize({})
        await gw.on_call_started("call-1", {})
        await gw.on_call_started("call-2", {})

        assert len(gw._sessions) == 2
        await gw.cleanup()
        assert len(gw._sessions) == 0

    async def test_get_audio_queue(self):
        """get_audio_queue returns the session input queue."""
        gw = RTPMediaGateway()
        await gw.initialize({})
        await gw.on_call_started("call-1", {})

        q = gw.get_audio_queue("call-1")
        assert q is not None
        assert isinstance(q, asyncio.Queue)

    async def test_get_audio_queue_unknown_call(self):
        """get_audio_queue returns None for unknown calls."""
        gw = RTPMediaGateway()
        await gw.initialize({})
        assert gw.get_audio_queue("nonexistent") is None


# =============================================================================
# RTP Flow Monitoring — on_audio_received
# =============================================================================


@pytest.mark.asyncio
class TestRTPFlowMonitoring:
    """Tests for flow monitoring integrated into on_audio_received."""

    async def test_on_audio_received_records_packet(self):
        """Receiving audio should update the flow monitor."""
        gw = RTPMediaGateway()
        await gw.initialize({})
        await gw.on_call_started("call-1", {})

        # Send raw PCM data (not an RTP packet — ≤12 bytes triggers raw path)
        await gw.on_audio_received("call-1", b"\x00" * 10)

        session = gw.get_session("call-1")
        assert session.flow_monitor.first_packet_at is not None
        assert session.flow_monitor.is_silent_call is False

    async def test_media_started_callback_fires_once(self):
        """Callback should fire on the first packet only."""
        gw = RTPMediaGateway()
        await gw.initialize({})
        await gw.on_call_started("call-1", {})

        callback = AsyncMock()
        gw.set_media_started_callback(callback)

        # First audio — callback fires
        await gw.on_audio_received("call-1", b"\x00" * 10)
        callback.assert_awaited_once_with("call-1")

        callback.reset_mock()

        # Second audio — callback should NOT fire again
        await gw.on_audio_received("call-1", b"\x00" * 10)
        callback.assert_not_awaited()

    async def test_media_started_callback_error_is_swallowed(self):
        """A failing callback should not break audio processing."""
        gw = RTPMediaGateway()
        await gw.initialize({})
        await gw.on_call_started("call-1", {})

        callback = AsyncMock(side_effect=RuntimeError("boom"))
        gw.set_media_started_callback(callback)

        # Should not raise
        await gw.on_audio_received("call-1", b"\x00" * 10)

        # Audio should still land in the queue
        q = gw.get_audio_queue("call-1")
        assert q.qsize() == 1

    async def test_on_audio_received_queues_pcm(self):
        """Decoded audio should be placed into the input queue."""
        gw = RTPMediaGateway()
        await gw.initialize({})
        await gw.on_call_started("call-1", {})

        pcm = b"\x80" * 8  # small chunk
        await gw.on_audio_received("call-1", pcm)

        q = gw.get_audio_queue("call-1")
        assert q.qsize() == 1

    async def test_on_audio_received_unknown_call(self):
        """Audio for an unknown call should be silently ignored."""
        gw = RTPMediaGateway()
        await gw.initialize({})

        # Should not raise
        await gw.on_audio_received("nonexistent", b"\x00" * 10)


# =============================================================================
# check_media_flow
# =============================================================================


@pytest.mark.asyncio
class TestCheckMediaFlow:
    """Tests for check_media_flow() status API."""

    async def test_unknown_call(self):
        gw = RTPMediaGateway()
        await gw.initialize({})
        result = gw.check_media_flow("nonexistent")
        assert result == {"error": "unknown_call"}

    async def test_silent_call(self):
        """A call with no audio should report is_silent_call=True."""
        gw = RTPMediaGateway()
        await gw.initialize({})
        await gw.on_call_started("call-1", {})

        result = gw.check_media_flow("call-1")
        assert result["is_silent_call"] is True
        assert result["is_media_flowing"] is False
        assert result["first_packet_at"] is None
        assert result["packets_received"] == 0

    async def test_active_call(self):
        """A call with recent audio should report is_media_flowing=True."""
        gw = RTPMediaGateway()
        await gw.initialize({})
        await gw.on_call_started("call-1", {})
        await gw.on_audio_received("call-1", b"\x00" * 10)

        result = gw.check_media_flow("call-1")
        assert result["is_silent_call"] is False
        assert result["is_media_flowing"] is True
        assert result["first_packet_at"] is not None
        assert result["last_packet_at"] is not None
        # Note: packets_received only counts parsed RTP packets (>12 bytes),
        # not raw PCM chunks — so it's 0 for this test with short data.
        assert result["packets_received"] == 0


# =============================================================================
# send_audio (TTS over RTP)
# =============================================================================


@pytest.mark.asyncio
class TestSendAudioRTP:
    """Tests for RTPMediaGateway.send_audio pipeline."""

    async def test_send_audio_transmits_udp(self):
        """send_audio should convert audio and send via UDP socket."""
        gw = RTPMediaGateway()
        await gw.initialize({
            "source_sample_rate": 8000,
            "source_format": "pcm_s16le",
            "codec": "ulaw",
        })
        await gw.on_call_started("call-1", {
            "remote_ip": "127.0.0.1",
            "remote_port": 9999,
        })

        session = gw.get_session("call-1")
        # Replace real socket with a mock
        mock_sock = MagicMock()
        mock_sock.sendto = MagicMock()
        session.udp_socket = mock_sock

        # 160 samples * 2 bytes = 320 bytes of 8kHz PCM16
        pcm16_audio = b"\x00\x01" * 160

        await gw.send_audio("call-1", pcm16_audio)

        # Socket should have been called at least once
        assert mock_sock.sendto.call_count >= 1
        assert session.packets_sent >= 1

    async def test_send_audio_unknown_call(self):
        """send_audio for unknown call should not crash."""
        gw = RTPMediaGateway()
        await gw.initialize({})

        # Should not raise
        await gw.send_audio("nonexistent", b"\x00" * 320)
