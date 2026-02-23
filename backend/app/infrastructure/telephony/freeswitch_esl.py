"""
FreeSWITCH ESL (Event Socket Library) Client
Provides call control for Talky.ai AI agent via FreeSWITCH.

Uses TWO separate ESL connections:
  - Event connection: subscribes to events and processes them
  - API connection: sends commands and reads responses

This avoids the deadlock where the event listener blocks API calls.

Usage:
    esl_client = FreeSwitchESL()
    await esl_client.connect()
    await esl_client.answer_call(call_uuid)
    await esl_client.play_audio(call_uuid, audio_file_path)
"""
import asyncio
import logging
import os
import socket
from typing import Optional, Callable, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass
class ESLConfig:
    """FreeSWITCH ESL connection configuration. Reads from environment variables."""
    host: str = field(default_factory=lambda: os.getenv("FREESWITCH_ESL_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.getenv("FREESWITCH_ESL_PORT", "8021")))
    password: str = field(default_factory=lambda: os.getenv("FREESWITCH_ESL_PASSWORD", "ClueCon"))
    timeout: float = 10.0


@dataclass
class CallInfo:
    """Active call information from FreeSWITCH."""
    uuid: str
    caller_id: str
    destination: str
    state: str
    direction: str  # inbound or outbound
    created_at: datetime
    answered_at: Optional[datetime] = None


class TransferMode(str, Enum):
    BLIND = "blind"
    ATTENDED = "attended"
    DEFLECT = "deflect"


class TransferLeg(str, Enum):
    ALEG = "aleg"
    BLEG = "bleg"
    BOTH = "both"


class TransferStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass
class TransferRequest:
    uuid: str
    destination: str
    mode: TransferMode = TransferMode.BLIND
    leg: TransferLeg = TransferLeg.ALEG
    context: str = "default"
    timeout_seconds: float = 12.0
    attended_cancel_key: str = "*"
    attended_complete_key: str = "#"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.uuid or not self.uuid.strip():
            raise ValueError("uuid is required")
        if not self.destination or not self.destination.strip():
            raise ValueError("destination is required")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")


@dataclass
class TransferResult:
    attempt_id: str
    uuid: str
    mode: TransferMode
    destination: str
    leg: TransferLeg
    status: TransferStatus
    started_at: datetime
    finished_at: Optional[datetime] = None
    reason: Optional[str] = None
    command: Optional[str] = None
    response: Optional[str] = None
    context: str = "default"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "uuid": self.uuid,
            "mode": self.mode.value,
            "destination": self.destination,
            "leg": self.leg.value,
            "status": self.status.value,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "reason": self.reason,
            "command": self.command,
            "response": self.response,
            "context": self.context,
        }


class _ESLConnection:
    """A single ESL TCP connection with auth."""
    
    def __init__(self, config: ESLConfig, name: str = "default"):
        self.config = config
        self.name = name
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
    
    async def connect(self) -> bool:
        """Connect and authenticate."""
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.config.host, self.config.port),
                timeout=self.config.timeout
            )
            
            # Read auth/request header
            header = await self._read_response()
            if "Content-Type: auth/request" not in header:
                logger.error(f"ESL[{self.name}] Unexpected header: {header}")
                return False
            
            # Authenticate
            self._writer.write(f"auth {self.config.password}\n\n".encode())
            await self._writer.drain()
            
            response = await self._read_response()
            if "+OK accepted" not in response:
                logger.error(f"ESL[{self.name}] Auth failed: {response}")
                return False
            
            logger.info(f"ESL[{self.name}] authenticated")
            return True
        except Exception as e:
            logger.error(f"ESL[{self.name}] connect error: {e}")
            return False
    
    async def send(self, command: str) -> None:
        """Send a command."""
        if not self._writer:
            raise RuntimeError(f"ESL[{self.name}] not connected")
        self._writer.write(f"{command}\n\n".encode())
        await self._writer.drain()
        logger.debug(f"ESL[{self.name}] → {command}")
    
    async def _read_response(self) -> str:
        """Read until double newline."""
        if not self._reader:
            raise RuntimeError(f"ESL[{self.name}] not connected")
        
        response = ""
        while True:
            line = await self._reader.readline()
            if not line:
                break
            decoded = line.decode('utf-8', errors='ignore')
            response += decoded
            if decoded == "\n":
                break
        return response
    
    async def read_full_response(self) -> str:
        """Read headers + body for an API response."""
        headers = await self._read_response()
        
        # Extract content-length and read body
        content_length = 0
        for line in headers.split('\n'):
            if line.startswith('Content-Length:'):
                content_length = int(line.split(':')[1].strip())
                break
        
        if content_length > 0 and self._reader:
            body = await self._reader.read(content_length)
            return body.decode('utf-8', errors='ignore')
        
        return headers
    
    async def read_event(self) -> Optional[Dict[str, str]]:
        """Read and parse a FreeSWITCH event.

        ESL plain-text events arrive as::

            Content-Length: 1234
            Content-Type: text/event-plain

            Event-Name: CHANNEL_ANSWER
            Unique-ID: <call-uuid>
            ...

        The first block (before the blank line) contains envelope
        headers.  The body (after the blank line, sized by
        Content-Length) contains the actual event fields.  We parse
        *both* blocks into the returned dict so callers can simply do
        ``event.get('Event-Name')``.
        """
        if not self._reader:
            return None

        headers: Dict[str, str] = {}
        while True:
            line = await asyncio.wait_for(
                self._reader.readline(),
                timeout=30.0,
            )
            if not line:
                return None

            decoded = line.decode('utf-8', errors='ignore').strip()
            if not decoded:
                break

            if ': ' in decoded:
                key, value = decoded.split(': ', 1)
                headers[key] = value

        # Read body if present
        content_length = int(headers.get('Content-Length', 0))
        if content_length > 0:
            body = await self._reader.read(content_length)
            body_text = body.decode('utf-8', errors='ignore')
            headers['_body'] = body_text

            # ── Parse body key-value pairs into the dict ──
            # For text/event-plain the body is newline-separated
            # "Key: Value" pairs, just like HTTP headers.  Values may
            # be URL-encoded (%XX); we decode them.
            from urllib.parse import unquote
            for body_line in body_text.split('\n'):
                body_line = body_line.strip()
                if not body_line:
                    continue
                if ': ' in body_line:
                    k, v = body_line.split(': ', 1)
                    headers[k] = unquote(v)

        return headers if headers else None
    
    async def close(self) -> None:
        """Close the connection."""
        if self._writer:
            try:
                self._writer.write(b"exit\n\n")
                await self._writer.drain()
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None


class FreeSwitchESL:
    """
    Async client for FreeSWITCH Event Socket.
    
    Uses two connections:
    - _event_conn: Subscribes to events, runs event listener loop
    - _api_conn: Sends commands (api, bgapi), reads responses
    
    This prevents the event listener from blocking API calls.
    """
    
    def __init__(self, config: Optional[ESLConfig] = None):
        self.config = config or ESLConfig()
        self._event_conn: Optional[_ESLConnection] = None
        self._api_conn: Optional[_ESLConnection] = None
        self._connected = False
        self._running = False
        
        # Lock for API commands (serialize command+response pairs)
        self._api_lock = asyncio.Lock()
        
        # Active calls tracked by UUID
        self._calls: Dict[str, CallInfo] = {}
        
        # Event callbacks
        self._on_call_start: Optional[Callable] = None
        self._on_call_end: Optional[Callable] = None
        self._on_dtmf: Optional[Callable] = None
        
        # Event listener task
        self._event_task: Optional[asyncio.Task] = None

        # WS-C transfer tracking
        self._transfer_attempts: Dict[str, TransferResult] = {}
        self._transfer_waiters: Dict[str, asyncio.Event] = {}
        self._active_transfer_by_uuid: Dict[str, str] = {}
    
    @property
    def connected(self) -> bool:
        return self._connected
    
    @property
    def calls(self) -> Dict[str, CallInfo]:
        return self._calls.copy()
    
    async def connect(self) -> bool:
        """Connect both ESL connections and start event listener."""
        try:
            logger.info(f"Connecting to FreeSWITCH ESL at {self.config.host}:{self.config.port}")
            
            # Connection 1: Events
            self._event_conn = _ESLConnection(self.config, "events")
            if not await self._event_conn.connect():
                return False
            
            # Connection 2: API commands
            self._api_conn = _ESLConnection(self.config, "api")
            if not await self._api_conn.connect():
                await self._event_conn.close()
                return False
            
            self._connected = True
            logger.info("✓ Connected to FreeSWITCH ESL (dual-connection)")
            
            # Subscribe to events on the event connection
            await self._subscribe_events()
            
            # Start event listener
            self._running = True
            self._event_task = asyncio.create_task(self._event_listener())
            
            return True
            
        except asyncio.TimeoutError:
            logger.error(f"ESL connection timeout to {self.config.host}:{self.config.port}")
            return False
        except Exception as e:
            logger.error(f"ESL connection error: {e}")
            return False
    
    async def disconnect(self) -> None:
        """Disconnect from FreeSWITCH ESL."""
        self._running = False
        self._connected = False
        
        if self._event_task:
            self._event_task.cancel()
            try:
                await self._event_task
            except asyncio.CancelledError:
                pass
        
        if self._event_conn:
            await self._event_conn.close()
        if self._api_conn:
            await self._api_conn.close()
        
        logger.info("Disconnected from FreeSWITCH ESL")
    
    async def _subscribe_events(self) -> None:
        """Subscribe to call-related events on the event connection."""
        events = [
            "CHANNEL_CREATE",
            "CHANNEL_ANSWER", 
            "CHANNEL_BRIDGE",
            "CHANNEL_UNBRIDGE",
            "CHANNEL_EXECUTE_COMPLETE",
            "CHANNEL_HANGUP",
            "CHANNEL_DESTROY",
            "DTMF",
            "CUSTOM",
            "HEARTBEAT"
        ]
        await self._event_conn.send(f"event plain {' '.join(events)}")
        await self._event_conn.read_full_response()
        logger.info(f"Subscribed to ESL events: {events}")
    
    async def _event_listener(self) -> None:
        """Background task listening for FreeSWITCH events on event connection."""
        logger.info("ESL event listener started")
        
        while self._running and self._event_conn:
            try:
                event = await self._event_conn.read_event()
                if event:
                    await self._handle_event(event)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    logger.debug(f"ESL event read: {e}")
                await asyncio.sleep(0.1)
        
        logger.info("ESL event listener stopped")
    
    async def _handle_event(self, event: Dict[str, str]) -> None:
        """Process a FreeSWITCH event."""
        event_name = event.get('Event-Name', '')
        call_uuid = event.get('Unique-ID', '')

        # Log every non-heartbeat event for debugging
        if event_name and event_name != 'HEARTBEAT':
            logger.debug(
                f"ESL event: {event_name} uuid={call_uuid[:8] if call_uuid else '?'}"
            )

        if event_name == 'CHANNEL_CREATE':
            call_info = CallInfo(
                uuid=call_uuid,
                caller_id=event.get('Caller-Caller-ID-Number', 'unknown'),
                destination=event.get('Caller-Destination-Number', 'unknown'),
                state='ringing',
                direction=event.get('Call-Direction', 'unknown'),
                created_at=datetime.utcnow()
            )
            self._calls[call_uuid] = call_info
            logger.info(f"📞 New call: {call_info.caller_id} → {call_info.destination} ({call_uuid[:8]})")
            
        elif event_name == 'CHANNEL_ANSWER':
            if call_uuid in self._calls:
                self._calls[call_uuid].state = 'answered'
                self._calls[call_uuid].answered_at = datetime.utcnow()
                logger.info(f"✓ Call answered: {call_uuid[:8]}")
                
                if self._on_call_start:
                    asyncio.create_task(self._on_call_start(call_uuid))
                    
        elif event_name in ('CHANNEL_HANGUP', 'CHANNEL_DESTROY'):
            if call_uuid in self._calls:
                call_info = self._calls.pop(call_uuid)
                logger.info(f"📞 Call ended: {call_uuid[:8]}")
                
                if self._on_call_end:
                    asyncio.create_task(self._on_call_end(call_uuid))
                    
        elif event_name == 'DTMF':
            digit = event.get('DTMF-Digit', '')
            if digit and self._on_dtmf:
                asyncio.create_task(self._on_dtmf(call_uuid, digit))

        # Always run WS-C transfer correlation logic for every call event.
        self._update_transfer_state_from_event(event_name, event)

    def _set_transfer_status(
        self,
        attempt_id: str,
        status: TransferStatus,
        reason: Optional[str] = None,
        response: Optional[str] = None,
    ) -> None:
        result = self._transfer_attempts.get(attempt_id)
        if not result:
            return
        # Don't mutate finalized attempts.
        if result.status in {
            TransferStatus.SUCCESS,
            TransferStatus.FAILED,
            TransferStatus.CANCELLED,
            TransferStatus.TIMED_OUT,
        }:
            return

        result.status = status
        if reason:
            result.reason = reason
        if response:
            result.response = response
        if status in {
            TransferStatus.SUCCESS,
            TransferStatus.FAILED,
            TransferStatus.CANCELLED,
            TransferStatus.TIMED_OUT,
        }:
            result.finished_at = datetime.now(timezone.utc)
            waiter = self._transfer_waiters.get(attempt_id)
            if waiter:
                waiter.set()

    def _get_transfer_attempt_for_event(self, event: Dict[str, str]) -> Optional[str]:
        ids = [
            event.get("Unique-ID"),
            event.get("Other-Leg-Unique-ID"),
            event.get("Bridge-A-Unique-ID"),
            event.get("Bridge-B-Unique-ID"),
        ]
        for call_uuid in ids:
            if call_uuid and call_uuid in self._active_transfer_by_uuid:
                return self._active_transfer_by_uuid[call_uuid]
        return None

    def _update_transfer_state_from_event(self, event_name: str, event: Dict[str, str]) -> None:
        attempt_id = self._get_transfer_attempt_for_event(event)
        if not attempt_id:
            return

        result = self._transfer_attempts.get(attempt_id)
        if not result:
            return

        if event_name == "CHANNEL_BRIDGE":
            self._set_transfer_status(attempt_id, TransferStatus.SUCCESS, "bridge_established")
            return

        if event_name == "CHANNEL_EXECUTE_COMPLETE":
            app = (event.get("Application") or "").lower()
            app_resp = (event.get("Application-Response") or "").lower()
            if app in {"transfer", "uuid_transfer", "att_xfer", "deflect"}:
                if "err" in app_resp or "-err" in app_resp:
                    self._set_transfer_status(attempt_id, TransferStatus.FAILED, "execute_error", app_resp)
                elif "cancel" in app_resp:
                    self._set_transfer_status(attempt_id, TransferStatus.CANCELLED, "execute_cancel", app_resp)
                elif "ok" in app_resp or "+ok" in app_resp:
                    self._set_transfer_status(attempt_id, TransferStatus.SUCCESS, "execute_ok", app_resp)
            return

        if event_name in {"CHANNEL_HANGUP", "CHANNEL_DESTROY"}:
            if result.status not in {TransferStatus.SUCCESS, TransferStatus.CANCELLED}:
                self._set_transfer_status(
                    attempt_id,
                    TransferStatus.FAILED,
                    f"{event_name.lower()}_before_transfer_complete",
                )

    @staticmethod
    def _leg_to_flag(leg: TransferLeg) -> str:
        if leg == TransferLeg.BLEG:
            return "-bleg"
        if leg == TransferLeg.BOTH:
            return "-both"
        return ""

    async def _is_call_answered(self, uuid: str) -> bool:
        call = self._calls.get(uuid)
        if call and call.state == "answered":
            return True

        # Fallback: query FreeSWITCH var for active calls not yet cached.
        try:
            answer_state = await self.api(f"uuid_getvar {uuid} answer_state")
            if "answered" in answer_state.lower():
                return True
        except Exception:
            return False
        return False

    def _build_transfer_command(self, request: TransferRequest) -> str:
        leg_flag = self._leg_to_flag(request.leg)
        if request.mode == TransferMode.DEFLECT:
            return f"uuid_deflect {request.uuid} {request.destination}"
        if request.mode == TransferMode.ATTENDED:
            # Attended transfer via att_xfer dialplan app in inline context.
            attended_payload = f"att_xfer::{request.destination}"
            parts = ["uuid_transfer", request.uuid]
            if leg_flag:
                parts.append(leg_flag)
            parts.extend([attended_payload, "inline"])
            return " ".join(parts)

        # Blind transfer default.
        parts = ["uuid_transfer", request.uuid]
        if leg_flag:
            parts.append(leg_flag)
        parts.extend([request.destination, "XML", request.context])
        return " ".join(parts)

    async def request_transfer(self, request: TransferRequest) -> TransferResult:
        """WS-C transfer API with deterministic terminal status."""
        request.validate()
        attempt_id = uuid4().hex
        started_at = datetime.now(timezone.utc)
        result = TransferResult(
            attempt_id=attempt_id,
            uuid=request.uuid,
            mode=request.mode,
            destination=request.destination,
            leg=request.leg,
            status=TransferStatus.PENDING,
            started_at=started_at,
            context=request.context,
        )
        self._transfer_attempts[attempt_id] = result
        self._active_transfer_by_uuid[request.uuid] = attempt_id
        waiter = asyncio.Event()
        self._transfer_waiters[attempt_id] = waiter

        try:
            if request.mode == TransferMode.DEFLECT and not await self._is_call_answered(request.uuid):
                self._set_transfer_status(
                    attempt_id,
                    TransferStatus.FAILED,
                    "deflect_requires_answered_call",
                )
                return self._transfer_attempts[attempt_id]

            if request.mode in {TransferMode.BLIND, TransferMode.ATTENDED}:
                # Official FS behavior: avoid bridge hangup limbo on transfer.
                await self.api(f"uuid_setvar {request.uuid} hangup_after_bridge false")

            if request.mode == TransferMode.ATTENDED:
                await self.api(f"uuid_setvar {request.uuid} attxfer_cancel_key {request.attended_cancel_key}")
                await self.api(f"uuid_setvar {request.uuid} attxfer_complete_key {request.attended_complete_key}")

            command = self._build_transfer_command(request)
            response = await self.api(command)
            result.command = command
            result.response = response

            if "-ERR" in response.upper():
                self._set_transfer_status(attempt_id, TransferStatus.FAILED, "command_rejected", response)
                return self._transfer_attempts[attempt_id]

            self._set_transfer_status(attempt_id, TransferStatus.ACCEPTED, "command_accepted", response)

            try:
                await asyncio.wait_for(waiter.wait(), timeout=request.timeout_seconds)
            except asyncio.TimeoutError:
                self._set_transfer_status(
                    attempt_id,
                    TransferStatus.TIMED_OUT,
                    f"no_terminal_event_within_{request.timeout_seconds}s",
                )
            return self._transfer_attempts[attempt_id]
        finally:
            self._transfer_waiters.pop(attempt_id, None)
            # Keep attempt records for API lookup, but clear active map if this call still points to this attempt.
            if self._active_transfer_by_uuid.get(request.uuid) == attempt_id:
                self._active_transfer_by_uuid.pop(request.uuid, None)

    def get_transfer_result(self, attempt_id: str) -> Optional[TransferResult]:
        return self._transfer_attempts.get(attempt_id)

    def list_transfer_results(self) -> Dict[str, TransferResult]:
        return dict(self._transfer_attempts)
    
    # =========================================================================
    # Call Control API (uses the dedicated API connection)
    # =========================================================================
    
    async def api(self, command: str) -> str:
        """Execute a FreeSWITCH API command and return result."""
        async with self._api_lock:
            await self._api_conn.send(f"api {command}")
            return await self._api_conn.read_full_response()
    
    async def bgapi(self, command: str) -> str:
        """Execute a FreeSWITCH API command in background."""
        async with self._api_lock:
            await self._api_conn.send(f"bgapi {command}")
            return await self._api_conn.read_full_response()
    
    async def answer_call(self, uuid: str) -> bool:
        """Answer an incoming call."""
        result = await self.api(f"uuid_answer {uuid}")
        success = "+OK" in result
        if success:
            logger.info(f"Answered call {uuid[:8]}")
        return success
    
    async def hangup_call(self, uuid: str, cause: str = "NORMAL_CLEARING") -> bool:
        """Hang up a call."""
        result = await self.api(f"uuid_kill {uuid} {cause}")
        success = "+OK" in result
        if success:
            logger.info(f"Hung up call {uuid[:8]}")
        return success
    
    async def play_audio(self, uuid: str, file_path: str, leg: str = "aleg") -> bool:
        """Play an audio file to a call using uuid_broadcast."""
        result = await self.api(f"uuid_broadcast {uuid} {file_path} {leg}")
        success = "+OK" in result
        if success:
            logger.info(f"Playing audio to {uuid[:8]}: {file_path}")
        return success
    
    async def play_tts(self, uuid: str, text: str, engine: str = "flite") -> bool:
        """Play TTS directly via FreeSWITCH mod_flite or mod_tts_commandline."""
        result = await self.api(f"uuid_broadcast {uuid} 'say:{engine}:{text}' aleg")
        return "+OK" in result
    
    async def start_audio_fork(self, uuid: str, ws_url: str) -> bool:
        """Start audio streaming to WebSocket via mod_audio_fork."""
        result = await self.api(f"uuid_audio_fork {uuid} start {ws_url}")
        return "+OK" in result
    
    async def stop_audio_fork(self, uuid: str) -> bool:
        """Stop audio streaming."""
        result = await self.api(f"uuid_audio_fork {uuid} stop")
        return "+OK" in result
    
    async def originate_call(
        self,
        destination: str,
        gateway: str = "3cx-pbx",
        caller_id: str = "1001",
        timeout: int = 60
    ) -> Optional[str]:
        """
        Originate an outbound call via gateway.
        
        Returns call UUID if successful.
        """
        dial_string = f"sofia/gateway/{gateway}/{destination}"
        # CRITICAL: Use silence_stream playback, NOT &park!
        # &park sends NO RTP → PBX drops call after 30s (no media timeout)
        # silence_stream sends continuous silence RTP packets to keep PBX alive
        app_string = "'&playback(silence_stream://-1)'"
        
        command = f"originate {dial_string} {app_string}"
        
        logger.info(f"ESL originate command: {command}")
        result = await self.api(command)
        logger.info(f"ESL originate result: {result}")
        
        # Parse UUID from response
        if "+OK" in result:
            parts = result.strip().split()
            if len(parts) >= 2:
                uuid = parts[1]
                logger.info(f"📞 Originated call to {destination}: {uuid[:8]}")
                return uuid
        
        logger.error(f"Failed to originate call: {result}")
        return None
    
    async def originate_with_playback(
        self,
        destination: str,
        audio_file: str,
        gateway: str = "3cx-pbx",
        caller_id: str = "1001"
    ) -> Optional[str]:
        """Originate an outbound call and play audio file."""
        dial_string = f"sofia/gateway/{gateway}/{destination}"
        app_string = f"&playback({audio_file})"
        
        command = f"originate {dial_string} '{app_string}'"
        
        logger.info(f"ESL originate with playback: {command}")
        result = await self.api(command)
        logger.info(f"ESL originate result: {result}")
        
        if "+OK" in result:
            parts = result.strip().split()
            if len(parts) >= 2:
                uuid = parts[1]
                logger.info(f"📞 Originated call with playback to {destination}: {uuid[:8]}")
                return uuid
        
        logger.error(f"Failed to originate call with playback: {result}")
        return None
    
    async def transfer_call(self, uuid: str, destination: str, context: str = "default") -> bool:
        """Backwards-compatible blind transfer helper."""
        transfer = TransferRequest(
            uuid=uuid,
            destination=destination,
            context=context,
            mode=TransferMode.BLIND,
            leg=TransferLeg.ALEG,
            timeout_seconds=8.0,
        )
        result = await self.request_transfer(transfer)
        return result.status in {TransferStatus.SUCCESS, TransferStatus.ACCEPTED}
    
    async def get_sofia_status(self) -> str:
        """Get Sofia (SIP stack) status."""
        return await self.api("sofia status")
    
    async def get_gateway_status(self, gateway: str = "3cx-pbx") -> str:
        """Get gateway registration status."""
        return await self.api(f"sofia status gateway {gateway}")
    
    # =========================================================================
    # Callback Registration
    # =========================================================================
    
    def on_call_start(self, callback: Callable) -> None:
        """Register callback for when a call is answered."""
        self._on_call_start = callback
    
    def on_call_end(self, callback: Callable) -> None:
        """Register callback for when a call ends."""
        self._on_call_end = callback
    
    def on_dtmf(self, callback: Callable) -> None:
        """Register callback for DTMF digits."""
        self._on_dtmf = callback


async def main():
    """Test ESL client connection."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    esl = FreeSwitchESL()
    
    async def on_call(uuid: str):
        logger.info(f"Call started callback: {uuid}")
        await esl.play_audio(uuid, "/var/lib/freeswitch/sounds/custom/greeting.wav")
    
    esl.on_call_start(on_call)
    
    if await esl.connect():
        logger.info("ESL connected! Checking gateway status...")
        
        status = await esl.get_gateway_status()
        logger.info(f"Gateway status:\n{status}")
        
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            pass
        
        await esl.disconnect()
    else:
        logger.error("Failed to connect to ESL")


if __name__ == "__main__":
    asyncio.run(main())
