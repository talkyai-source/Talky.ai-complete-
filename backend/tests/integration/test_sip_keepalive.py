"""
Minimal SIP test - registers, calls, and ONLY sends silence packets.
No TTS, no voice pipeline - just pure RTP keep-alive.

This tests if our basic RTP implementation keeps the call alive.
"""
import asyncio
import socket
import struct
import hashlib
import time
import logging

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


class SimpleSIPClient:
    def __init__(self):
        self.local_ip = ""
        self.local_port = 5062
        self.rtp_port = 10000
        self.sip_socket = None
        self.rtp_socket = None
        self.registered = False
        self.call_active = False
        self.call_id = ""
        self.remote_rtp_addr = None
        self.cseq = 1
        self.invite_cseq = 0  # Track INVITE CSeq for ACK
        self.rtp_seq = 0
        self.rtp_timestamp = 0
        self.ssrc = 0x12345678
        self.running = True
        
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
        self.local_ip = self._get_local_ip()
        logger.info(f"Local IP: {self.local_ip}")
        
        # Create SIP socket - bind to specific IP
        self.sip_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sip_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sip_socket.bind((self.local_ip, self.local_port))  # Bind to specific IP
        self.sip_socket.setblocking(False)
        
        # Create RTP socket - bind to specific IP
        self.rtp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.rtp_socket.bind((self.local_ip, self.rtp_port))  # Bind to specific IP
        self.rtp_socket.setblocking(False)
        
        # Initialize random RTP values (per RFC 3550)
        import random
        self.rtp_seq = random.randint(0, 65535)
        self.rtp_timestamp = random.randint(0, 0xFFFFFFFF)
        self.ssrc = random.randint(0, 0xFFFFFFFF)
        
        logger.info(f"SIP on {self.local_ip}:{self.local_port}, RTP on {self.local_ip}:{self.rtp_port}")
        logger.info(f"RTP init: seq={self.rtp_seq}, ts={self.rtp_timestamp}, ssrc={hex(self.ssrc)}")
        
        # Start tasks
        await asyncio.gather(
            self._register(),
            self._sip_listener(),
            self._rtp_sender(),
            self._rtp_receiver(),
            return_exceptions=True
        )
    
    async def _register(self):
        """Send REGISTER to PBX"""
        call_id = f"reg-{int(time.time())}"
        branch = f"z9hG4bK-{int(time.time() * 1000)}"
        tag = f"tag-{int(time.time())}"
        
        register = (
            f"REGISTER sip:{PBX_HOST} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_port};branch={branch}\r\n"
            f"Max-Forwards: 70\r\n"
            f"From: <sip:{USERNAME}@{PBX_HOST}>;tag={tag}\r\n"
            f"To: <sip:{USERNAME}@{PBX_HOST}>\r\n"
            f"Call-ID: {call_id}\r\n"
            f"CSeq: {self.cseq} REGISTER\r\n"
            f"Contact: <sip:{USERNAME}@{self.local_ip}:{self.local_port}>\r\n"
            f"Expires: 300\r\n"
            f"User-Agent: TestSIP/1.0\r\n"
            f"Content-Length: 0\r\n"
            f"\r\n"
        )
        self.cseq += 1
        
        self.sip_socket.sendto(register.encode(), (PBX_HOST, PBX_PORT))
        logger.info("Sent REGISTER")
    
    async def _make_call(self):
        """Send INVITE to extension"""
        self.call_id = f"call-{int(time.time())}"
        branch = f"z9hG4bK-{int(time.time() * 1000)}"
        tag = f"tag-{int(time.time())}"
        
        sdp = self._build_sdp()
        
        invite = (
            f"INVITE sip:{CALL_TO}@{PBX_HOST} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_port};branch={branch}\r\n"
            f"Max-Forwards: 70\r\n"
            f"From: <sip:{USERNAME}@{PBX_HOST}>;tag={tag}\r\n"
            f"To: <sip:{CALL_TO}@{PBX_HOST}>\r\n"
            f"Call-ID: {self.call_id}\r\n"
            f"CSeq: {self.cseq} INVITE\r\n"
            f"Contact: <sip:{USERNAME}@{self.local_ip}:{self.local_port}>\r\n"
            f"Content-Type: application/sdp\r\n"
            f"Content-Length: {len(sdp)}\r\n"
            f"User-Agent: TestSIP/1.0\r\n"
            f"\r\n"
            f"{sdp}"
        )
        self.invite_cseq = self.cseq  # Store for ACK
        self.cseq += 1
        
        self.sip_socket.sendto(invite.encode(), (PBX_HOST, PBX_PORT))
        logger.info(f"📞 Calling {CALL_TO}...")
    
    def _build_sdp(self):
        return (
            "v=0\r\n"
            f"o=test 1 1 IN IP4 {self.local_ip}\r\n"
            "s=Test Call\r\n"
            f"c=IN IP4 {self.local_ip}\r\n"
            "t=0 0\r\n"
            f"m=audio {self.rtp_port} RTP/AVP 0\r\n"
            "a=rtpmap:0 PCMU/8000\r\n"
            "a=sendrecv\r\n"
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
                    # Make call after registration
                    await asyncio.sleep(1)
                    await self._make_call()
                elif status == 401 or status == 407:
                    await self._handle_register_auth(message)
            
            elif "INVITE" in message:
                if status == 100:
                    logger.info("📞 Trying...")
                elif status == 180 or status == 183:
                    logger.info("📞 Ringing...")
                elif status == 200:
                    if not self.call_active:  # Only process first 200 OK
                        logger.info("✅ Call answered!")
                        self._parse_remote_rtp(message)
                        await self._send_ack(message)
                        self.call_active = True
                    else:
                        # Retransmit ACK for repeated 200 OK
                        await self._send_ack(message)
                elif status == 401 or status == 407:
                    await self._handle_invite_auth(message)
                elif status >= 400:
                    logger.error(f"❌ Call failed: {status}")
        
        elif first_line.startswith("BYE"):
            logger.warning("📞 BYE received - call ended by remote")
            # Send 200 OK
            response = self._build_response(message, 200, "OK")
            self.sip_socket.sendto(response.encode(), addr)
            self.call_active = False
            self.running = False
    
    async def _handle_register_auth(self, message):
        auth_header = self._extract_header(message, "WWW-Authenticate")
        if not auth_header:
            auth_header = self._extract_header(message, "Proxy-Authenticate")
        
        realm = self._extract_param(auth_header, "realm")
        nonce = self._extract_param(auth_header, "nonce")
        
        uri = f"sip:{PBX_HOST}"
        response = self._calculate_digest(realm, nonce, "REGISTER", uri)
        
        call_id = f"reg-auth-{int(time.time())}"
        branch = f"z9hG4bK-{int(time.time() * 1000)}"
        tag = f"tag-{int(time.time())}"
        
        register = (
            f"REGISTER sip:{PBX_HOST} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_port};branch={branch}\r\n"
            f"Max-Forwards: 70\r\n"
            f"From: <sip:{USERNAME}@{PBX_HOST}>;tag={tag}\r\n"
            f"To: <sip:{USERNAME}@{PBX_HOST}>\r\n"
            f"Call-ID: {call_id}\r\n"
            f"CSeq: {self.cseq} REGISTER\r\n"
            f"Contact: <sip:{USERNAME}@{self.local_ip}:{self.local_port}>\r\n"
            f'Authorization: Digest username="{USERNAME}", realm="{realm}", '
            f'nonce="{nonce}", uri="{uri}", response="{response}"\r\n'
            f"Expires: 300\r\n"
            f"User-Agent: TestSIP/1.0\r\n"
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
        tag = f"tag-{int(time.time())}"
        sdp = self._build_sdp()
        
        invite = (
            f"INVITE sip:{CALL_TO}@{PBX_HOST} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_port};branch={branch}\r\n"
            f"Max-Forwards: 70\r\n"
            f"From: <sip:{USERNAME}@{PBX_HOST}>;tag={tag}\r\n"
            f"To: <sip:{CALL_TO}@{PBX_HOST}>\r\n"
            f"Call-ID: {self.call_id}\r\n"
            f"CSeq: {self.cseq} INVITE\r\n"
            f"Contact: <sip:{USERNAME}@{self.local_ip}:{self.local_port}>\r\n"
            f'Authorization: Digest username="{USERNAME}", realm="{realm}", '
            f'nonce="{nonce}", uri="{uri}", response="{response}"\r\n'
            f"Content-Type: application/sdp\r\n"
            f"Content-Length: {len(sdp)}\r\n"
            f"User-Agent: TestSIP/1.0\r\n"
            f"\r\n"
            f"{sdp}"
        )
        self.cseq += 1
        
        self.sip_socket.sendto(invite.encode(), (PBX_HOST, PBX_PORT))
        logger.info("Sent authenticated INVITE")
    
    def _parse_remote_rtp(self, message):
        for line in message.split('\r\n'):
            if line.startswith('m=audio'):
                port = int(line.split()[1])
                break
        for line in message.split('\r\n'):
            if line.startswith('c=IN IP4'):
                ip = line.split()[2]
                break
        self.remote_rtp_addr = (ip, port)
        logger.info(f"📡 Remote RTP: {ip}:{port}")
    
    async def _send_ack(self, response_message=None):
        branch = f"z9hG4bK-ack-{int(time.time() * 1000)}"
        
        # Get To header with tag from response
        to_header = self._extract_header(response_message, "To") if response_message else f"<sip:{CALL_TO}@{PBX_HOST}>"
        from_header = self._extract_header(response_message, "From") if response_message else f"<sip:{USERNAME}@{PBX_HOST}>;tag=tag-{int(time.time())}"
        
        # ACK uses same CSeq number as INVITE (stored in invite_cseq)
        ack = (
            f"ACK sip:{CALL_TO}@{PBX_HOST} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_port};branch={branch}\r\n"
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
    
    async def _rtp_sender(self):
        """Send silence RTP packets to keep call alive"""
        packets_sent = 0
        call_start = None
        
        while self.running:
            if self.call_active and self.remote_rtp_addr:
                if call_start is None:
                    call_start = time.time()
                    logger.info("🔊 Starting to send silence packets...")
                
                # Build RTP packet
                self.rtp_seq = (self.rtp_seq + 1) & 0xFFFF
                self.rtp_timestamp += 160
                
                header = struct.pack(
                    RTP_HEADER_FORMAT,
                    0x80,  # Version 2
                    0,     # Payload type 0 = PCMU
                    self.rtp_seq,
                    self.rtp_timestamp,
                    self.ssrc
                )
                packet = header + SILENCE_FRAME
                
                try:
                    self.rtp_socket.sendto(packet, self.remote_rtp_addr)
                    packets_sent += 1
                    
                    if packets_sent % 50 == 0:
                        elapsed = time.time() - call_start
                        logger.info(f"📡 Sent {packets_sent} packets ({elapsed:.1f}s elapsed)")
                except Exception as e:
                    logger.error(f"RTP send error: {e}")
            
            await asyncio.sleep(0.020)  # 20ms
        
        logger.info(f"🔴 Total packets sent: {packets_sent}")
    
    async def _rtp_receiver(self):
        """Receive RTP packets from remote"""
        loop = asyncio.get_event_loop()
        packets_received = 0
        
        while self.running:
            if self.call_active:
                try:
                    data, addr = await asyncio.wait_for(
                        loop.sock_recvfrom(self.rtp_socket, 1024),
                        timeout=0.1
                    )
                    packets_received += 1
                    if packets_received == 1:
                        logger.info(f"📥 Receiving RTP from {addr}")
                    if packets_received % 50 == 0:
                        logger.info(f"📥 Received {packets_received} RTP packets")
                except asyncio.TimeoutError:
                    pass
                except Exception as e:
                    logger.debug(f"RTP recv error: {e}")
            else:
                await asyncio.sleep(0.1)
        
        logger.info(f"📥 Total packets received: {packets_received}")


async def main():
    logger.info("=" * 50)
    logger.info("MINIMAL SIP KEEP-ALIVE TEST")
    logger.info("This sends ONLY silence - no TTS, no audio processing")
    logger.info("=" * 50)
    
    client = SimpleSIPClient()
    
    try:
        await client.start()
    except KeyboardInterrupt:
        logger.info("Stopped by user")


if __name__ == "__main__":
    asyncio.run(main())
