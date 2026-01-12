"""
SIP Bridge Server
Handles SIP signaling and RTP audio transport for MicroSIP integration.

Day 18: Uses pyVoIP for SIP/RTP handling, bridges to VoicePipelineService.

Usage:
    python -m app.infrastructure.telephony.sip_bridge_server

Architecture:
    MicroSIP ──SIP──► SIPBridgeServer ──► SIPMediaGateway ──► VoicePipeline
             ◄─RTP──                  ◄──                  ◄──
"""
import asyncio
import logging
import socket
import struct
import audioop
from typing import Dict, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
import yaml

logger = logging.getLogger(__name__)

# RTP Header format (12 bytes fixed header)
RTP_HEADER_FORMAT = "!BBHII"  # version/flags, payload_type, seq, timestamp, ssrc


@dataclass
class RTPSession:
    """RTP session state"""
    ssrc: int
    remote_addr: tuple
    local_rtp_port: int
    remote_rtp_port: int
    codec: str = "PCMU"
    seq_number: int = 0
    timestamp: int = 0
    last_activity: datetime = field(default_factory=datetime.utcnow)


@dataclass
class SIPCall:
    """Active SIP call state"""
    call_id: str
    from_uri: str
    to_uri: str
    rtp_session: Optional[RTPSession] = None
    state: str = "ringing"  # ringing, active, ended
    created_at: datetime = field(default_factory=datetime.utcnow)


class SIPBridgeServer:
    """
    Simple SIP/RTP server for MicroSIP integration.
    
    Handles:
    - SIP REGISTER (for phone registration)
    - SIP INVITE (incoming calls)
    - SIP ACK (call confirmation)
    - SIP BYE (call termination)
    - RTP audio streaming
    
    Note: This is a minimal implementation for local testing.
    For production, use FreeSWITCH or Asterisk.
    """
    
    def __init__(
        self,
        host: str = "0.0.0.0",
        sip_port: int = 5060,
        rtp_port_start: int = 10000,
        on_audio_callback: Optional[Callable] = None
    ):
        self.host = host
        self.sip_port = sip_port
        self.rtp_port_start = rtp_port_start
        self._next_rtp_port = rtp_port_start
        
        self._sip_socket: Optional[socket.socket] = None
        self._rtp_sockets: Dict[int, socket.socket] = {}
        self._calls: Dict[str, SIPCall] = {}
        self._running = False
        
        # Callback for audio processing
        self._on_audio_callback = on_audio_callback
        
        # Call lifecycle callbacks (set by sip_bridge.py for voice pipeline)
        self.on_call_started: Optional[Callable] = None
        self.on_call_ended: Optional[Callable] = None
        
        # Audio conversion state
        self._resample_states: Dict[str, any] = {}
    
    async def start(self) -> None:
        """Start the SIP bridge server"""
        # Create SIP UDP socket
        self._sip_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sip_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sip_socket.bind((self.host, self.sip_port))
        self._sip_socket.setblocking(False)
        
        self._running = True
        
        logger.info(
            f"SIP Bridge Server started on {self.host}:{self.sip_port}",
            extra={"sip_port": self.sip_port, "rtp_port_start": self.rtp_port_start}
        )
        
        # Start listening tasks
        await asyncio.gather(
            self._sip_listener(),
            return_exceptions=True
        )
    
    async def stop(self) -> None:
        """Stop the server and cleanup"""
        self._running = False
        
        # Close SIP socket
        if self._sip_socket:
            self._sip_socket.close()
        
        # Close all RTP sockets
        for port, sock in self._rtp_sockets.items():
            sock.close()
        
        # End all calls
        for call_id in list(self._calls.keys()):
            await self._end_call(call_id, "server_shutdown")
        
        logger.info("SIP Bridge Server stopped")
    
    async def _sip_listener(self) -> None:
        """Listen for SIP messages"""
        loop = asyncio.get_event_loop()
        
        while self._running:
            try:
                # Receive SIP message
                data, addr = await loop.sock_recvfrom(self._sip_socket, 4096)
                message = data.decode('utf-8', errors='ignore')
                
                await self._handle_sip_message(message, addr)
                
            except Exception as e:
                if self._running:
                    logger.error(f"SIP listener error: {e}")
                await asyncio.sleep(0.01)
    
    async def _handle_sip_message(self, message: str, addr: tuple) -> None:
        """Handle incoming SIP message"""
        lines = message.split('\r\n')
        if not lines:
            return
        
        request_line = lines[0]
        
        # Parse SIP method
        if request_line.startswith("REGISTER"):
            await self._handle_register(message, addr)
        elif request_line.startswith("INVITE"):
            await self._handle_invite(message, addr)
        elif request_line.startswith("ACK"):
            await self._handle_ack(message, addr)
        elif request_line.startswith("BYE"):
            await self._handle_bye(message, addr)
        elif request_line.startswith("OPTIONS"):
            await self._handle_options(message, addr)
        else:
            logger.debug(f"Unhandled SIP message: {request_line[:50]}")
    
    async def _handle_register(self, message: str, addr: tuple) -> None:
        """Handle SIP REGISTER - phone registration"""
        call_id = self._extract_header(message, "Call-ID")
        from_header = self._extract_header(message, "From")
        
        logger.info(
            f"SIP REGISTER from {addr[0]}:{addr[1]}",
            extra={"from": from_header, "call_id": call_id}
        )
        
        # Send 200 OK response
        response = self._build_sip_response(message, 200, "OK", addr)
        await self._send_sip(response, addr)
    
    async def _handle_invite(self, message: str, addr: tuple) -> None:
        """Handle SIP INVITE - incoming call"""
        call_id = self._extract_header(message, "Call-ID")
        from_header = self._extract_header(message, "From")
        to_header = self._extract_header(message, "To")
        
        logger.info(
            f"SIP INVITE from {addr[0]}:{addr[1]}",
            extra={"call_id": call_id, "from": from_header, "to": to_header}
        )
        
        # Extract remote RTP port from SDP
        remote_rtp_port = self._extract_sdp_port(message)
        
        # Allocate local RTP port
        local_rtp_port = self._allocate_rtp_port()
        
        # Create call record
        call = SIPCall(
            call_id=call_id,
            from_uri=from_header,
            to_uri=to_header,
            rtp_session=RTPSession(
                ssrc=0x12345678,
                remote_addr=addr,
                local_rtp_port=local_rtp_port,
                remote_rtp_port=remote_rtp_port or 0
            )
        )
        self._calls[call_id] = call
        
        # Send 180 Ringing
        ringing = self._build_sip_response(message, 180, "Ringing", addr)
        await self._send_sip(ringing, addr)
        
        # Auto-answer with 200 OK + SDP
        await asyncio.sleep(0.5)  # Brief ring delay
        
        sdp = self._build_sdp(local_rtp_port)
        ok_response = self._build_sip_response(
            message, 200, "OK", addr,
            content_type="application/sdp",
            body=sdp
        )
        await self._send_sip(ok_response, addr)
        
        call.state = "active"
        
        # Start RTP listener for this call
        asyncio.create_task(self._rtp_listener(call_id, local_rtp_port))
        
        # Notify voice pipeline that call has started
        if self.on_call_started:
            asyncio.create_task(self.on_call_started(call_id))
        
        logger.info(f"Call {call_id} answered, RTP on port {local_rtp_port}")
    
    async def _handle_ack(self, message: str, addr: tuple) -> None:
        """Handle SIP ACK - call confirmation"""
        call_id = self._extract_header(message, "Call-ID")
        logger.debug(f"SIP ACK received for call {call_id}")
    
    async def _handle_bye(self, message: str, addr: tuple) -> None:
        """Handle SIP BYE - call termination"""
        call_id = self._extract_header(message, "Call-ID")
        
        logger.info(f"SIP BYE received for call {call_id}")
        
        # Send 200 OK
        response = self._build_sip_response(message, 200, "OK", addr)
        await self._send_sip(response, addr)
        
        await self._end_call(call_id, "bye_received")
    
    async def _handle_options(self, message: str, addr: tuple) -> None:
        """Handle SIP OPTIONS - keep-alive/capability query"""
        response = self._build_sip_response(message, 200, "OK", addr)
        await self._send_sip(response, addr)
    
    async def _rtp_listener(self, call_id: str, local_port: int) -> None:
        """Listen for RTP audio on a specific port"""
        call = self._calls.get(call_id)
        if not call or not call.rtp_session:
            return
        
        # Create RTP socket
        rtp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rtp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        rtp_socket.bind((self.host, local_port))
        rtp_socket.setblocking(False)
        self._rtp_sockets[local_port] = rtp_socket
        
        loop = asyncio.get_event_loop()
        resample_state = None
        
        logger.info(f"RTP listener started on port {local_port} for call {call_id}")
        
        while self._running and call_id in self._calls:
            try:
                data, addr = await asyncio.wait_for(
                    loop.sock_recvfrom(rtp_socket, 1024),
                    timeout=1.0
                )
                
                # Parse RTP header (first 12 bytes)
                if len(data) < 12:
                    continue
                
                header = struct.unpack(RTP_HEADER_FORMAT, data[:12])
                payload_type = header[1] & 0x7F  # Lower 7 bits
                seq = header[2]
                timestamp = header[3]
                
                # Extract audio payload
                audio_payload = data[12:]
                
                # Convert G.711 μ-law → PCM 16kHz
                if payload_type == 0:  # PCMU
                    pcm_8k = audioop.ulaw2lin(audio_payload, 2)
                elif payload_type == 8:  # PCMA
                    pcm_8k = audioop.alaw2lin(audio_payload, 2)
                else:
                    continue
                
                # Resample 8kHz → 16kHz
                pcm_16k, resample_state = audioop.ratecv(
                    pcm_8k, 2, 1, 8000, 16000, resample_state
                )
                
                # Callback for audio processing
                if self._on_audio_callback:
                    await self._on_audio_callback(call_id, pcm_16k)
                
                call.rtp_session.last_activity = datetime.utcnow()
                
            except asyncio.TimeoutError:
                # Check for inactivity
                if call.rtp_session:
                    inactive = (datetime.utcnow() - call.rtp_session.last_activity).seconds
                    if inactive > 30:
                        logger.warning(f"RTP inactivity timeout for call {call_id}")
                        await self._end_call(call_id, "rtp_timeout")
                        break
            except Exception as e:
                if self._running:
                    logger.error(f"RTP listener error: {e}")
        
        # Cleanup socket
        rtp_socket.close()
        if local_port in self._rtp_sockets:
            del self._rtp_sockets[local_port]
        
        logger.info(f"RTP listener stopped for call {call_id}")
    
    async def send_rtp_audio(self, call_id: str, audio_data: bytes) -> None:
        """
        Send audio to the SIP phone via RTP.
        
        Args:
            call_id: Call identifier
            audio_data: PCM audio (16kHz, 16-bit, mono)
        """
        call = self._calls.get(call_id)
        if not call or not call.rtp_session:
            return
        
        rtp = call.rtp_session
        local_port = rtp.local_rtp_port
        
        rtp_socket = self._rtp_sockets.get(local_port)
        if not rtp_socket:
            return
        
        try:
            # Resample 16kHz → 8kHz
            pcm_8k, _ = audioop.ratecv(audio_data, 2, 1, 16000, 8000, None)
            
            # Encode PCM → G.711 μ-law
            ulaw_data = audioop.lin2ulaw(pcm_8k, 2)
            
            # Build RTP packet
            rtp.seq_number = (rtp.seq_number + 1) & 0xFFFF
            rtp.timestamp += 160  # 20ms at 8kHz
            
            header = struct.pack(
                RTP_HEADER_FORMAT,
                0x80,  # Version 2, no padding/extension
                0,     # Payload type 0 (PCMU)
                rtp.seq_number,
                rtp.timestamp,
                rtp.ssrc
            )
            
            packet = header + ulaw_data
            
            # Send to remote RTP port
            remote = (call.rtp_session.remote_addr[0], rtp.remote_rtp_port)
            rtp_socket.sendto(packet, remote)
            
        except Exception as e:
            logger.error(f"RTP send error: {e}", extra={"call_id": call_id})
    
    async def _end_call(self, call_id: str, reason: str) -> None:
        """End a call and cleanup resources"""
        call = self._calls.pop(call_id, None)
        if not call:
            return
        
        call.state = "ended"
        
        # Close RTP socket
        if call.rtp_session:
            port = call.rtp_session.local_rtp_port
            if port in self._rtp_sockets:
                self._rtp_sockets[port].close()
                del self._rtp_sockets[port]
        
        # Notify voice pipeline that call has ended
        if self.on_call_ended:
            asyncio.create_task(self.on_call_ended(call_id))
        
        logger.info(
            f"Call ended: {call_id}",
            extra={"reason": reason, "duration": (datetime.utcnow() - call.created_at).seconds}
        )
    
    def _allocate_rtp_port(self) -> int:
        """Allocate next available RTP port"""
        port = self._next_rtp_port
        self._next_rtp_port += 2  # RTP uses even ports, RTCP uses odd
        return port
    
    async def _send_sip(self, message: str, addr: tuple) -> None:
        """Send SIP message"""
        if self._sip_socket:
            self._sip_socket.sendto(message.encode('utf-8'), addr)
    
    def _extract_header(self, message: str, header: str) -> str:
        """Extract SIP header value"""
        for line in message.split('\r\n'):
            if line.lower().startswith(header.lower() + ':'):
                return line.split(':', 1)[1].strip()
        return ""
    
    def _extract_sdp_port(self, message: str) -> Optional[int]:
        """Extract RTP port from SDP m= line"""
        for line in message.split('\r\n'):
            if line.startswith('m=audio'):
                parts = line.split()
                if len(parts) >= 2:
                    return int(parts[1])
        return None
    
    def _build_sdp(self, rtp_port: int) -> str:
        """Build SDP answer"""
        return (
            "v=0\r\n"
            f"o=talkyai 1 1 IN IP4 {self.host}\r\n"
            "s=Talky.ai Voice Agent\r\n"
            f"c=IN IP4 {self.host}\r\n"
            "t=0 0\r\n"
            f"m=audio {rtp_port} RTP/AVP 0 101\r\n"
            "a=rtpmap:0 PCMU/8000\r\n"
            "a=rtpmap:101 telephone-event/8000\r\n"
            "a=sendrecv\r\n"
        )
    
    def _build_sip_response(
        self,
        request: str,
        code: int,
        reason: str,
        addr: tuple,
        content_type: str = None,
        body: str = None
    ) -> str:
        """Build SIP response message"""
        # Extract headers from request
        via = self._extract_header(request, "Via")
        from_h = self._extract_header(request, "From")
        to_h = self._extract_header(request, "To")
        call_id = self._extract_header(request, "Call-ID")
        cseq = self._extract_header(request, "CSeq")
        
        # Add tag to To header if not present
        if "tag=" not in to_h:
            to_h = f"{to_h};tag=talkyai-{call_id[:8]}"
        
        lines = [
            f"SIP/2.0 {code} {reason}",
            f"Via: {via}",
            f"From: {from_h}",
            f"To: {to_h}",
            f"Call-ID: {call_id}",
            f"CSeq: {cseq}",
            "User-Agent: Talky.ai/1.0",
        ]
        
        if content_type and body:
            lines.append(f"Content-Type: {content_type}")
            lines.append(f"Content-Length: {len(body)}")
            lines.append("")
            lines.append(body)
        else:
            lines.append("Content-Length: 0")
            lines.append("")
        
        return "\r\n".join(lines)


async def main():
    """Test the SIP bridge server"""
    logging.basicConfig(level=logging.INFO)
    
    async def on_audio(call_id: str, audio: bytes):
        logger.info(f"Received {len(audio)} bytes of audio from call {call_id}")
    
    server = SIPBridgeServer(
        host="0.0.0.0",
        sip_port=5060,
        rtp_port_start=10000,
        on_audio_callback=on_audio
    )
    
    try:
        await server.start()
    except KeyboardInterrupt:
        await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
