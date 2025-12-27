"""
Unit tests for SIP Bridge Server
Tests SIP signaling, RTP handling, and call management.

Day 18: MicroSIP integration testing
"""
import pytest
import asyncio
import struct
import socket
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime

from app.infrastructure.telephony.sip_bridge_server import (
    SIPBridgeServer,
    SIPCall,
    RTPSession,
    RTP_HEADER_FORMAT
)


class TestRTPSession:
    """Test RTPSession dataclass"""
    
    def test_rtp_session_creation(self):
        """Test creating an RTP session"""
        session = RTPSession(
            ssrc=0x12345678,
            remote_addr=("192.168.1.100", 5060),
            local_rtp_port=10000,
            remote_rtp_port=10002
        )
        
        assert session.ssrc == 0x12345678
        assert session.remote_addr == ("192.168.1.100", 5060)
        assert session.local_rtp_port == 10000
        assert session.remote_rtp_port == 10002
        assert session.codec == "PCMU"  # Default
        assert session.seq_number == 0
        assert session.timestamp == 0


class TestSIPCall:
    """Test SIPCall dataclass"""
    
    def test_sip_call_creation(self):
        """Test creating a SIP call record"""
        call = SIPCall(
            call_id="abc123@host",
            from_uri="sip:agent001@localhost",
            to_uri="sip:100@localhost"
        )
        
        assert call.call_id == "abc123@host"
        assert call.from_uri == "sip:agent001@localhost"
        assert call.to_uri == "sip:100@localhost"
        assert call.state == "ringing"  # Default
        assert call.rtp_session is None
    
    def test_sip_call_with_rtp(self):
        """Test SIP call with RTP session"""
        rtp = RTPSession(
            ssrc=0xABCDEF01,
            remote_addr=("10.0.0.1", 5060),
            local_rtp_port=10000,
            remote_rtp_port=10002
        )
        
        call = SIPCall(
            call_id="xyz789@host",
            from_uri="sip:test@localhost",
            to_uri="sip:agent@localhost",
            rtp_session=rtp,
            state="active"
        )
        
        assert call.state == "active"
        assert call.rtp_session is not None
        assert call.rtp_session.ssrc == 0xABCDEF01


class TestSIPBridgeServer:
    """Test SIPBridgeServer class"""
    
    @pytest.fixture
    def server(self):
        """Create a SIP bridge server instance"""
        return SIPBridgeServer(
            host="127.0.0.1",
            sip_port=5061,  # Use non-standard port for testing
            rtp_port_start=20000
        )
    
    def test_server_initialization(self, server):
        """Test server initialization"""
        assert server.host == "127.0.0.1"
        assert server.sip_port == 5061
        assert server.rtp_port_start == 20000
        assert server._running is False
        assert len(server._calls) == 0
    
    def test_allocate_rtp_port(self, server):
        """Test RTP port allocation"""
        port1 = server._allocate_rtp_port()
        port2 = server._allocate_rtp_port()
        port3 = server._allocate_rtp_port()
        
        assert port1 == 20000
        assert port2 == 20002  # +2 for RTP/RTCP pair
        assert port3 == 20004
    
    def test_extract_header(self, server):
        """Test SIP header extraction"""
        message = (
            "INVITE sip:100@localhost SIP/2.0\r\n"
            "Via: SIP/2.0/UDP 192.168.1.100:5060\r\n"
            "From: <sip:agent001@localhost>;tag=abc123\r\n"
            "To: <sip:100@localhost>\r\n"
            "Call-ID: 12345@192.168.1.100\r\n"
            "CSeq: 1 INVITE\r\n"
            "\r\n"
        )
        
        via = server._extract_header(message, "Via")
        from_h = server._extract_header(message, "From")
        call_id = server._extract_header(message, "Call-ID")
        cseq = server._extract_header(message, "CSeq")
        
        assert "192.168.1.100:5060" in via
        assert "agent001@localhost" in from_h
        assert call_id == "12345@192.168.1.100"
        assert cseq == "1 INVITE"
    
    def test_extract_header_case_insensitive(self, server):
        """Test header extraction is case-insensitive"""
        message = (
            "REGISTER sip:localhost SIP/2.0\r\n"
            "call-id: test123\r\n"
            "FROM: <sip:user@localhost>\r\n"
            "\r\n"
        )
        
        call_id = server._extract_header(message, "Call-ID")
        from_h = server._extract_header(message, "From")
        
        assert call_id == "test123"
        assert "user@localhost" in from_h
    
    def test_extract_sdp_port(self, server):
        """Test RTP port extraction from SDP"""
        message = (
            "INVITE sip:100@localhost SIP/2.0\r\n"
            "Content-Type: application/sdp\r\n"
            "\r\n"
            "v=0\r\n"
            "o=user 1 1 IN IP4 192.168.1.100\r\n"
            "s=MicroSIP\r\n"
            "c=IN IP4 192.168.1.100\r\n"
            "t=0 0\r\n"
            "m=audio 10500 RTP/AVP 0 101\r\n"
            "a=rtpmap:0 PCMU/8000\r\n"
        )
        
        port = server._extract_sdp_port(message)
        
        assert port == 10500
    
    def test_extract_sdp_port_not_found(self, server):
        """Test SDP port extraction when not present"""
        message = (
            "REGISTER sip:localhost SIP/2.0\r\n"
            "\r\n"
        )
        
        port = server._extract_sdp_port(message)
        
        assert port is None
    
    def test_build_sdp(self, server):
        """Test SDP response generation"""
        sdp = server._build_sdp(10000)
        
        assert "v=0" in sdp
        assert "m=audio 10000 RTP/AVP" in sdp
        assert "PCMU/8000" in sdp
        assert "telephone-event/8000" in sdp
        assert "sendrecv" in sdp
    
    def test_build_sip_response(self, server):
        """Test SIP response building"""
        request = (
            "INVITE sip:100@localhost SIP/2.0\r\n"
            "Via: SIP/2.0/UDP 192.168.1.100:5060;branch=z9hG4bK1234\r\n"
            "From: <sip:agent001@localhost>;tag=abc123\r\n"
            "To: <sip:100@localhost>\r\n"
            "Call-ID: 12345@192.168.1.100\r\n"
            "CSeq: 1 INVITE\r\n"
            "\r\n"
        )
        
        response = server._build_sip_response(
            request, 200, "OK", ("192.168.1.100", 5060)
        )
        
        assert "SIP/2.0 200 OK" in response
        assert "Via:" in response
        assert "From:" in response
        assert "To:" in response
        assert "Call-ID: 12345@192.168.1.100" in response
        assert "CSeq: 1 INVITE" in response
        assert "User-Agent: Talky.ai" in response


class TestRTPPacket:
    """Test RTP packet construction"""
    
    def test_rtp_header_format(self):
        """Test RTP header structure"""
        # Build RTP header
        version_flags = 0x80  # Version 2, no padding/extension
        payload_type = 0      # PCMU
        seq = 1
        timestamp = 160
        ssrc = 0x12345678
        
        header = struct.pack(
            RTP_HEADER_FORMAT,
            version_flags,
            payload_type,
            seq,
            timestamp,
            ssrc
        )
        
        assert len(header) == 12  # RTP fixed header is 12 bytes
        
        # Verify unpacking
        unpacked = struct.unpack(RTP_HEADER_FORMAT, header)
        assert unpacked[0] == 0x80
        assert unpacked[1] == 0
        assert unpacked[2] == 1
        assert unpacked[3] == 160
        assert unpacked[4] == 0x12345678
    
    def test_rtp_packet_with_payload(self):
        """Test RTP packet with audio payload"""
        header = struct.pack(
            RTP_HEADER_FORMAT,
            0x80, 0, 100, 16000, 0xABCDEF01
        )
        
        # 20ms of μ-law audio at 8kHz = 160 bytes
        payload = bytes([0xFF] * 160)
        
        packet = header + payload
        
        assert len(packet) == 172  # 12 + 160


class TestSIPMessages:
    """Test SIP message parsing and generation"""
    
    @pytest.fixture
    def server(self):
        return SIPBridgeServer(host="127.0.0.1", sip_port=5061)
    
    def test_register_message_parsing(self, server):
        """Test REGISTER message parsing"""
        register = (
            "REGISTER sip:localhost SIP/2.0\r\n"
            "Via: SIP/2.0/UDP 192.168.1.100:5060\r\n"
            "From: <sip:agent001@localhost>;tag=123\r\n"
            "To: <sip:agent001@localhost>\r\n"
            "Call-ID: register-001\r\n"
            "CSeq: 1 REGISTER\r\n"
            "Contact: <sip:agent001@192.168.1.100:5060>\r\n"
            "Expires: 3600\r\n"
            "\r\n"
        )
        
        call_id = server._extract_header(register, "Call-ID")
        cseq = server._extract_header(register, "CSeq")
        contact = server._extract_header(register, "Contact")
        
        assert call_id == "register-001"
        assert "REGISTER" in cseq
        assert "192.168.1.100:5060" in contact
    
    def test_invite_message_with_sdp(self, server):
        """Test INVITE message with SDP body"""
        invite = (
            "INVITE sip:100@localhost SIP/2.0\r\n"
            "Via: SIP/2.0/UDP 192.168.1.100:5060\r\n"
            "From: <sip:agent001@localhost>;tag=abc\r\n"
            "To: <sip:100@localhost>\r\n"
            "Call-ID: invite-001\r\n"
            "CSeq: 1 INVITE\r\n"
            "Content-Type: application/sdp\r\n"
            "Content-Length: 150\r\n"
            "\r\n"
            "v=0\r\n"
            "o=- 1 1 IN IP4 192.168.1.100\r\n"
            "s=MicroSIP\r\n"
            "c=IN IP4 192.168.1.100\r\n"
            "t=0 0\r\n"
            "m=audio 10500 RTP/AVP 0 8 101\r\n"
            "a=rtpmap:0 PCMU/8000\r\n"
            "a=rtpmap:8 PCMA/8000\r\n"
        )
        
        rtp_port = server._extract_sdp_port(invite)
        content_type = server._extract_header(invite, "Content-Type")
        
        assert rtp_port == 10500
        assert content_type == "application/sdp"
    
    def test_bye_message(self, server):
        """Test BYE message parsing"""
        bye = (
            "BYE sip:100@localhost SIP/2.0\r\n"
            "Via: SIP/2.0/UDP 192.168.1.100:5060\r\n"
            "From: <sip:agent001@localhost>;tag=abc\r\n"
            "To: <sip:100@localhost>;tag=xyz\r\n"
            "Call-ID: call-001\r\n"
            "CSeq: 2 BYE\r\n"
            "\r\n"
        )
        
        call_id = server._extract_header(bye, "Call-ID")
        cseq = server._extract_header(bye, "CSeq")
        
        assert call_id == "call-001"
        assert "BYE" in cseq


class TestServerCallManagement:
    """Test call management in SIP bridge server"""
    
    @pytest.fixture
    def server(self):
        return SIPBridgeServer(host="127.0.0.1", sip_port=5061)
    
    @pytest.mark.asyncio
    async def test_end_call(self, server):
        """Test ending a call and cleanup"""
        # Manually add a call
        rtp = RTPSession(
            ssrc=0x12345678,
            remote_addr=("192.168.1.100", 5060),
            local_rtp_port=20000,
            remote_rtp_port=10500
        )
        
        call = SIPCall(
            call_id="test-end-001",
            from_uri="sip:agent@localhost",
            to_uri="sip:100@localhost",
            rtp_session=rtp,
            state="active"
        )
        
        server._calls["test-end-001"] = call
        
        # End the call
        await server._end_call("test-end-001", "user_hangup")
        
        # Call should be removed
        assert "test-end-001" not in server._calls
    
    @pytest.mark.asyncio
    async def test_end_nonexistent_call(self, server):
        """Test ending a call that doesn't exist"""
        # Should not raise
        await server._end_call("nonexistent-call", "cleanup")
        
        assert len(server._calls) == 0
