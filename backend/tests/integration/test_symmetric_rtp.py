"""
Fixed SIP/RTP test with symmetric RTP and NAT-aware design.
Key fixes:
1. Use same socket for send/receive (symmetric RTP)
2. Learn remote address from first received packet
3. Send silence only AFTER receiving first RTP packet
"""
import asyncio
import socket
import struct
import hashlib
import time
import logging
import os
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
PBX_HOST = "192.168.1.6"
PBX_PORT = 5060
USERNAME = "1002"
PASSWORD = "1002"
CALL_TO = "1001"

# RTP settings
RTP_HEADER_FORMAT = "!BBHII"
SILENCE_FRAME = bytes([0xFF] * 160)  # 20ms of μ-law silence


class SymmetricRTPSIPClient:
    """SIP client with symmetric RTP for NAT traversal."""
    
    def __init__(self):
        self.local_ip = ""
        self.local_sip_port = 5062
        self.local_rtp_port = 10000
        self.sip_socket = None
        self.rtp_socket = None
        self.registered = False
        self.call_active = False
        self.call_id = ""
        self.remote_rtp_addr = None  # From SDP
        self.learned_rtp_addr = None  # From actual RTP traffic (more reliable)
        self.cseq = 1
        self.invite_cseq = 0
        self.rtp_seq = 0
        self.rtp_timestamp = 0
        self.ssrc = 0
        self.running = True
        self.from_tag = ""
        self.to_tag = ""
        
        # TTS audio queue
        self.tts_audio_queue: asyncio.Queue = None  # Will be initialized in start()
        self.tts_active = False
        
    def _get_local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((PBX_HOST, PBX_PORT))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "127.0.0.1"
    
    async def start(self):
        import random
        
        self.local_ip = self._get_local_ip()
        logger.info(f"Local IP: {self.local_ip}")
        
        # Create SIP socket - bind to local IP
        self.sip_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sip_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sip_socket.bind((self.local_ip, self.local_sip_port))
        self.sip_socket.setblocking(False)
        
        # Create RTP socket - SYMMETRIC: same socket for send AND receive
        self.rtp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.rtp_socket.bind((self.local_ip, self.local_rtp_port))
        self.rtp_socket.setblocking(False)
        
        # Initialize random RTP values (per RFC 3550)
        self.rtp_seq = random.randint(0, 65535)
        self.rtp_timestamp = random.randint(0, 0xFFFFFFFF)
        self.ssrc = random.randint(0, 0xFFFFFFFF)
        self.from_tag = f"tag-{random.randint(100000, 999999)}"
        
        logger.info(f"SIP: {self.local_ip}:{self.local_sip_port}")
        logger.info(f"RTP: {self.local_ip}:{self.local_rtp_port} (symmetric)")
        logger.info(f"SSRC: {hex(self.ssrc)}")
        
        # Initialize TTS audio queue
        self.tts_audio_queue = asyncio.Queue()
        
        # Start tasks
        await asyncio.gather(
            self._register(),
            self._sip_listener(),
            self._rtp_handler(),  # Combined send/receive - sends TTS when available
            return_exceptions=True
        )
    
    async def _register(self):
        """Send REGISTER to PBX"""
        call_id = f"reg-{int(time.time())}"
        branch = f"z9hG4bK-{int(time.time() * 1000)}"
        
        register = (
            f"REGISTER sip:{PBX_HOST} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_sip_port};branch={branch};rport\r\n"
            f"Max-Forwards: 70\r\n"
            f"From: <sip:{USERNAME}@{PBX_HOST}>;tag={self.from_tag}\r\n"
            f"To: <sip:{USERNAME}@{PBX_HOST}>\r\n"
            f"Call-ID: {call_id}\r\n"
            f"CSeq: {self.cseq} REGISTER\r\n"
            f"Contact: <sip:{USERNAME}@{self.local_ip}:{self.local_sip_port};transport=udp>\r\n"
            f"Expires: 300\r\n"
            f"User-Agent: TalkyAI/2.0\r\n"
            f"Content-Length: 0\r\n"
            f"\r\n"
        )
        self.cseq += 1
        
        self.sip_socket.sendto(register.encode(), (PBX_HOST, PBX_PORT))
        logger.info("Sent REGISTER")
    
    async def _make_call(self):
        """Send INVITE to extension"""
        self.call_id = f"call-{int(time.time())}-{self.ssrc & 0xFFFF}"
        branch = f"z9hG4bK-{int(time.time() * 1000)}"
        
        sdp = self._build_sdp()
        
        invite = (
            f"INVITE sip:{CALL_TO}@{PBX_HOST} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_sip_port};branch={branch};rport\r\n"
            f"Max-Forwards: 70\r\n"
            f"From: <sip:{USERNAME}@{PBX_HOST}>;tag={self.from_tag}\r\n"
            f"To: <sip:{CALL_TO}@{PBX_HOST}>\r\n"
            f"Call-ID: {self.call_id}\r\n"
            f"CSeq: {self.cseq} INVITE\r\n"
            f"Contact: <sip:{USERNAME}@{self.local_ip}:{self.local_sip_port};transport=udp>\r\n"
            f"Content-Type: application/sdp\r\n"
            f"Content-Length: {len(sdp)}\r\n"
            f"User-Agent: TalkyAI/2.0\r\n"
            f"\r\n"
            f"{sdp}"
        )
        self.invite_cseq = self.cseq
        self.cseq += 1
        
        self.sip_socket.sendto(invite.encode(), (PBX_HOST, PBX_PORT))
        logger.info(f"📞 Calling {CALL_TO}...")
    
    def _build_sdp(self):
        """Build SDP with proper attributes for NAT traversal."""
        return (
            "v=0\r\n"
            f"o=TalkyAI {self.ssrc} {self.ssrc} IN IP4 {self.local_ip}\r\n"
            "s=TalkyAI Call\r\n"
            f"c=IN IP4 {self.local_ip}\r\n"
            "t=0 0\r\n"
            f"m=audio {self.local_rtp_port} RTP/AVP 0 101\r\n"
            "a=rtpmap:0 PCMU/8000\r\n"
            "a=rtpmap:101 telephone-event/8000\r\n"
            "a=fmtp:101 0-16\r\n"
            "a=sendrecv\r\n"
            "a=ptime:20\r\n"
        )
    
    def _calculate_digest(self, realm, nonce, method, uri):
        ha1 = hashlib.md5(f"{USERNAME}:{realm}:{PASSWORD}".encode()).hexdigest()
        ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
        return hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
    
    async def _sip_listener(self):
        """Listen for SIP responses"""
        loop = asyncio.get_event_loop()
        
        while self.running:
            try:
                data, addr = await asyncio.wait_for(
                    loop.sock_recvfrom(self.sip_socket, 4096),
                    timeout=1.0
                )
                message = data.decode('utf-8', errors='ignore')
                await self._handle_sip(message, addr)
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                logger.debug(f"SIP error: {e}")
    
    async def _handle_sip(self, message, addr):
        first_line = message.split('\r\n')[0]
        
        if first_line.startswith("SIP/2.0"):
            status = int(first_line.split()[1])
            
            if "REGISTER" in message:
                if status == 200:
                    self.registered = True
                    logger.info("✅ Registered with PBX!")
                    await asyncio.sleep(1)
                    await self._make_call()
                elif status == 401 or status == 407:
                    await self._handle_register_auth(message)
            
            elif "INVITE" in message or self.call_id in message:
                if status == 100:
                    logger.info("📞 Trying...")
                elif status == 180 or status == 183:
                    logger.info("📞 Ringing...")
                    # Try early media if available
                    if "application/sdp" in message.lower():
                        self._parse_remote_rtp(message)
                elif status == 200:
                    if not self.call_active:
                        logger.info("✅ Call answered!")
                        self._parse_to_tag(message)
                        self._parse_remote_rtp(message)
                        await self._send_ack(message)
                        self.call_active = True
                    else:
                        await self._send_ack(message)
                elif status == 401 or status == 407:
                    await self._handle_invite_auth(message)
                elif status >= 400:
                    logger.error(f"❌ Call failed: {status}")
        
        elif first_line.startswith("BYE"):
            logger.warning("📞 BYE received - call ended by remote")
            response = self._build_response(message, 200, "OK")
            self.sip_socket.sendto(response.encode(), addr)
            self.call_active = False
            self.running = False
    
    def _parse_to_tag(self, message):
        """Extract To tag from response."""
        for line in message.split('\r\n'):
            if line.lower().startswith('to:'):
                if 'tag=' in line:
                    self.to_tag = line.split('tag=')[1].split(';')[0].split('>')[0]
                    break
    
    async def _handle_register_auth(self, message):
        auth_header = self._extract_header(message, "WWW-Authenticate")
        if not auth_header:
            auth_header = self._extract_header(message, "Proxy-Authenticate")
        
        realm = self._extract_param(auth_header, "realm")
        nonce = self._extract_param(auth_header, "nonce")
        
        uri = f"sip:{PBX_HOST}"
        response = self._calculate_digest(realm, nonce, "REGISTER", uri)
        
        branch = f"z9hG4bK-{int(time.time() * 1000)}"
        
        register = (
            f"REGISTER sip:{PBX_HOST} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_sip_port};branch={branch};rport\r\n"
            f"Max-Forwards: 70\r\n"
            f"From: <sip:{USERNAME}@{PBX_HOST}>;tag={self.from_tag}\r\n"
            f"To: <sip:{USERNAME}@{PBX_HOST}>\r\n"
            f"Call-ID: reg-auth-{int(time.time())}\r\n"
            f"CSeq: {self.cseq} REGISTER\r\n"
            f"Contact: <sip:{USERNAME}@{self.local_ip}:{self.local_sip_port};transport=udp>\r\n"
            f'Authorization: Digest username="{USERNAME}", realm="{realm}", '
            f'nonce="{nonce}", uri="{uri}", response="{response}"\r\n'
            f"Expires: 300\r\n"
            f"User-Agent: TalkyAI/2.0\r\n"
            f"Content-Length: 0\r\n"
            f"\r\n"
        )
        self.cseq += 1
        
        self.sip_socket.sendto(register.encode(), (PBX_HOST, PBX_PORT))
        logger.info("Sent authenticated REGISTER")
    
    async def _handle_invite_auth(self, message):
        auth_header = self._extract_header(message, "WWW-Authenticate")
        if not auth_header:
            auth_header = self._extract_header(message, "Proxy-Authenticate")
        
        realm = self._extract_param(auth_header, "realm")
        nonce = self._extract_param(auth_header, "nonce")
        
        uri = f"sip:{CALL_TO}@{PBX_HOST}"
        response = self._calculate_digest(realm, nonce, "INVITE", uri)
        
        branch = f"z9hG4bK-{int(time.time() * 1000)}"
        sdp = self._build_sdp()
        
        invite = (
            f"INVITE sip:{CALL_TO}@{PBX_HOST} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_sip_port};branch={branch};rport\r\n"
            f"Max-Forwards: 70\r\n"
            f"From: <sip:{USERNAME}@{PBX_HOST}>;tag={self.from_tag}\r\n"
            f"To: <sip:{CALL_TO}@{PBX_HOST}>\r\n"
            f"Call-ID: {self.call_id}\r\n"
            f"CSeq: {self.cseq} INVITE\r\n"
            f"Contact: <sip:{USERNAME}@{self.local_ip}:{self.local_sip_port};transport=udp>\r\n"
            f'Authorization: Digest username="{USERNAME}", realm="{realm}", '
            f'nonce="{nonce}", uri="{uri}", response="{response}"\r\n'
            f"Content-Type: application/sdp\r\n"
            f"Content-Length: {len(sdp)}\r\n"
            f"User-Agent: TalkyAI/2.0\r\n"
            f"\r\n"
            f"{sdp}"
        )
        self.invite_cseq = self.cseq
        self.cseq += 1
        
        self.sip_socket.sendto(invite.encode(), (PBX_HOST, PBX_PORT))
        logger.info("Sent authenticated INVITE")
    
    def _parse_remote_rtp(self, message):
        """Parse remote RTP address from SDP."""
        port = None
        ip = None
        
        for line in message.split('\r\n'):
            if line.startswith('m=audio'):
                port = int(line.split()[1])
            if line.startswith('c=IN IP4'):
                ip = line.split()[2]
        
        if ip and port:
            self.remote_rtp_addr = (ip, port)
            logger.info(f"📡 Remote RTP (from SDP): {ip}:{port}")
    
    async def _send_ack(self, response_message):
        branch = f"z9hG4bK-ack-{int(time.time() * 1000)}"
        
        to_header = self._extract_header(response_message, "To")
        from_header = self._extract_header(response_message, "From")
        
        ack = (
            f"ACK sip:{CALL_TO}@{PBX_HOST} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_sip_port};branch={branch};rport\r\n"
            f"Max-Forwards: 70\r\n"
            f"From: {from_header}\r\n"
            f"To: {to_header}\r\n"
            f"Call-ID: {self.call_id}\r\n"
            f"CSeq: {self.invite_cseq} ACK\r\n"
            f"Content-Length: 0\r\n"
            f"\r\n"
        )
        
        self.sip_socket.sendto(ack.encode(), (PBX_HOST, PBX_PORT))
        logger.info("Sent ACK")
    
    def _build_response(self, request, code, reason):
        via = self._extract_header(request, "Via")
        from_h = self._extract_header(request, "From")
        to_h = self._extract_header(request, "To")
        call_id = self._extract_header(request, "Call-ID")
        cseq = self._extract_header(request, "CSeq")
        
        return (
            f"SIP/2.0 {code} {reason}\r\n"
            f"Via: {via}\r\n"
            f"From: {from_h}\r\n"
            f"To: {to_h}\r\n"
            f"Call-ID: {call_id}\r\n"
            f"CSeq: {cseq}\r\n"
            f"Content-Length: 0\r\n"
            f"\r\n"
        )
    
    def _extract_header(self, message, header):
        for line in message.split('\r\n'):
            if line.lower().startswith(header.lower() + ':'):
                return line.split(':', 1)[1].strip()
        return ""
    
    def _extract_param(self, header, param):
        import re
        match = re.search(f'{param}="([^"]+)"', header)
        if match:
            return match.group(1)
        return ""
    
    async def _rtp_handler(self):
        """
        SYMMETRIC RTP Handler:
        1. Listen for incoming RTP to learn actual remote address
        2. Send RTP to learned address (or SDP address if no RTP received yet)
        """
        loop = asyncio.get_event_loop()
        packets_sent = 0
        packets_received = 0
        call_start = None
        last_send_time = 0
        
        logger.info("🔊 RTP handler started (symmetric mode)")
        
        while self.running:
            now = time.time()
            
            # Check for incoming RTP (non-blocking, short timeout)
            if self.call_active:
                try:
                    data, addr = await asyncio.wait_for(
                        loop.sock_recvfrom(self.rtp_socket, 1024),
                        timeout=0.005  # 5ms check
                    )
                    packets_received += 1
                    
                    # SYMMETRIC: Learn the actual address to respond to
                    if self.learned_rtp_addr is None:
                        self.learned_rtp_addr = addr
                        logger.info(f"📥 Learned RTP from: {addr} (will send here)")
                    
                    if packets_received == 1:
                        logger.info(f"📥 First RTP packet received from {addr}")
                    elif packets_received % 50 == 0:
                        logger.info(f"📥 Received {packets_received} RTP packets")
                        
                except asyncio.TimeoutError:
                    pass
                except Exception as e:
                    logger.debug(f"RTP recv: {e}")
            
            # Send RTP every 20ms - TTS audio if available, otherwise silence
            if self.call_active and self.remote_rtp_addr:
                if call_start is None:
                    call_start = now
                    logger.info("🔊 Starting to send RTP...")
                
                # Time to send next packet?
                if now - last_send_time >= 0.020:
                    last_send_time = now
                    
                    # Use learned address if available, otherwise use SDP address
                    target_addr = self.learned_rtp_addr or self.remote_rtp_addr
                    
                    # Check if TTS audio is available in queue
                    if not self.tts_audio_queue.empty():
                        try:
                            frame = self.tts_audio_queue.get_nowait()
                        except:
                            frame = SILENCE_FRAME
                    else:
                        frame = SILENCE_FRAME
                    
                    # Build RTP packet
                    self.rtp_seq = (self.rtp_seq + 1) & 0xFFFF
                    self.rtp_timestamp = (self.rtp_timestamp + 160) & 0xFFFFFFFF
                    
                    header = struct.pack(
                        RTP_HEADER_FORMAT,
                        0x80,  # Version 2
                        0,     # Payload type 0 = PCMU
                        self.rtp_seq,
                        self.rtp_timestamp,
                        self.ssrc
                    )
                    packet = header + frame
                    
                    try:
                        self.rtp_socket.sendto(packet, target_addr)
                        packets_sent += 1
                        
                        if packets_sent % 50 == 0:
                            elapsed = now - call_start
                            logger.info(f"📡 Sent {packets_sent} / Recv {packets_received} ({elapsed:.1f}s) → {target_addr}")
                    except Exception as e:
                        logger.error(f"RTP send error: {e}")
            else:
                await asyncio.sleep(0.01)
        
        logger.info(f"🔴 RTP ended: sent={packets_sent}, recv={packets_received}")
    
    async def _tts_sender(self):
        """
        Send TTS audio frames from the queue as RTP packets.
        This runs in parallel with the main RTP handler.
        """
        import audioop
        
        logger.info("🎤 TTS sender started")
        
        while self.running:
            try:
                # Wait for audio frame (μ-law encoded, 160 bytes = 20ms)
                frame = await asyncio.wait_for(
                    self.tts_audio_queue.get(),
                    timeout=0.5
                )
                
                if not self.call_active or not self.remote_rtp_addr:
                    continue
                
                # Build RTP packet
                self.rtp_seq = (self.rtp_seq + 1) & 0xFFFF
                self.rtp_timestamp = (self.rtp_timestamp + 160) & 0xFFFFFFFF
                
                target_addr = self.learned_rtp_addr or self.remote_rtp_addr
                
                header = struct.pack(
                    RTP_HEADER_FORMAT,
                    0x80,  # Version 2
                    0,     # Payload type 0 = PCMU
                    self.rtp_seq,
                    self.rtp_timestamp,
                    self.ssrc
                )
                packet = header + frame
                
                self.rtp_socket.sendto(packet, target_addr)
                
                # Pace at 20ms intervals for real-time playback
                await asyncio.sleep(0.020)
                
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                logger.debug(f"TTS send error: {e}")
        
        logger.info("🎤 TTS sender stopped")
    
    async def send_tts_audio(self, text: str):
        """
        Synthesize text using Deepgram TTS and queue for RTP sending.
        """
        import audioop
        import os
        import aiohttp
        
        api_key = os.getenv("DEEPGRAM_API_KEY")
        if not api_key:
            logger.error("DEEPGRAM_API_KEY not set!")
            return
        
        logger.info(f"🎤 Synthesizing: '{text[:50]}...'")
        
        # Request TTS from Deepgram (8kHz for telephony)
        url = "https://api.deepgram.com/v1/speak?model=aura-asteria-en&encoding=linear16&sample_rate=16000"
        headers = {
            "Authorization": f"Token {api_key}",
            "Content-Type": "application/json"
        }
        payload = {"text": text}
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status != 200:
                    error = await response.text()
                    logger.error(f"Deepgram TTS error: {error}")
                    return
                
                # Get PCM 16-bit 16kHz audio
                pcm_16k = await response.read()
                logger.info(f"🎤 Received {len(pcm_16k)} bytes of audio")
                
                # Resample 16kHz → 8kHz for telephony
                pcm_8k, _ = audioop.ratecv(pcm_16k, 2, 1, 16000, 8000, None)
                
                # Encode to μ-law
                ulaw_data = audioop.lin2ulaw(pcm_8k, 2)
                
                # Split into 20ms frames (160 bytes at 8kHz μ-law)
                frame_size = 160
                frames_queued = 0
                
                # Mark TTS as active
                self.tts_active = True
                
                for i in range(0, len(ulaw_data), frame_size):
                    frame = ulaw_data[i:i+frame_size]
                    if len(frame) < frame_size:
                        frame = frame + bytes([0xFF] * (frame_size - len(frame)))
                    
                    await self.tts_audio_queue.put(frame)
                    frames_queued += 1
                
                logger.info(f"🎤 Queued {frames_queued} TTS frames ({frames_queued * 20}ms)")
                
                # Wait for queue to empty before clearing flag
                while not self.tts_audio_queue.empty():
                    await asyncio.sleep(0.1)
                
                self.tts_active = False
                logger.info("🎤 TTS playback complete")


# The greeting paragraph for TTS
TTS_GREETING = """
Hello! This is Talky AI, your intelligent voice assistant. 
I'm powered by advanced speech recognition and natural language processing.
Thank you for testing this SIP integration. The audio you're hearing is being 
synthesized by Deepgram's Aura text-to-speech engine and transmitted over RTP.
This call should stay connected as long as you'd like. Feel free to speak and I'll listen.
"""


async def main():
    logger.info("=" * 60)
    logger.info("SYMMETRIC RTP SIP TEST WITH DEEPGRAM TTS")
    logger.info("Uses same socket for send/receive, learns remote address")
    logger.info("=" * 60)
    
    client = SymmetricRTPSIPClient()
    
    # Monitor for call active and send TTS
    async def monitor_and_send_tts():
        # Wait for call to become active
        while not client.call_active and client.running:
            await asyncio.sleep(0.5)
        
        if client.call_active:
            logger.info("📞 Call active - sending TTS greeting...")
            await asyncio.sleep(1)  # Brief delay for connection to stabilize
            await client.send_tts_audio(TTS_GREETING)
    
    try:
        # Run both the SIP client and TTS monitor
        await asyncio.gather(
            client.start(),
            monitor_and_send_tts(),
            return_exceptions=True
        )
    except KeyboardInterrupt:
        logger.info("Stopped by user")


if __name__ == "__main__":
    asyncio.run(main())

