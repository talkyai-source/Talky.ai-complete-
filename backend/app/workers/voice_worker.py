"""
Voice Pipeline Worker
Background worker for handling voice AI pipeline (STT -> LLM -> TTS)

Run as separate process:
    python -m app.workers.voice_worker
"""
import asyncio
import logging
import os
import signal
import json
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

try:
    import redis.asyncio as redis
except ImportError:
    raise ImportError("redis package not installed")

from app.domain.services.session_manager import SessionManager
from app.domain.services.voice_pipeline_service import VoicePipelineService
from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider
from app.infrastructure.llm.groq import GroqLLMProvider
from app.infrastructure.tts.cartesia import CartesiaTTSProvider
from app.infrastructure.telephony.vonage_media_gateway import VonageMediaGateway


logger = logging.getLogger(__name__)

# Configure logging for worker
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


class VoicePipelineWorker:
    """
    Background worker for voice pipeline processing.
    
    Responsibilities:
    - Listen for new call events via Redis pub/sub
    - Run STT -> LLM -> TTS pipeline for each active call
    - Handle call lifecycle (start, process, end)
    
    Architecture:
    - Runs as separate process from FastAPI and Dialer Worker
    - Subscribes to Redis channel for call events
    - Manages concurrent voice pipelines
    """
    
    # Redis pub/sub channel for call events
    CALL_CHANNEL = "voice:calls:active"
    
    # Max concurrent pipelines
    MAX_CONCURRENT_PIPELINES = 50
    
    def __init__(self):
        self._redis: Optional[redis.Redis] = None
        self._session_manager: Optional[SessionManager] = None
        
        # Provider instances (lazy initialized)
        self._stt_provider: Optional[DeepgramFluxSTTProvider] = None
        self._llm_provider: Optional[GroqLLMProvider] = None
        self._tts_provider: Optional[CartesiaTTSProvider] = None
        self._media_gateway: Optional[VonageMediaGateway] = None
        
        # Active pipelines
        self._active_pipelines: dict[str, asyncio.Task] = {}
        
        self.running = False
        
        # Stats
        self._calls_handled = 0
        self._calls_failed = 0
    
    async def initialize(self) -> None:
        """Initialize connections and providers."""
        logger.info("Initializing Voice Pipeline Worker...")
        
        # Initialize Redis for pub/sub
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        self._redis = await redis.from_url(redis_url, decode_responses=True)
        
        # Get session manager instance
        self._session_manager = await SessionManager.get_instance()
        
        # Initialize providers (lazy - they'll connect when needed)
        await self._initialize_providers()
        
        logger.info("Voice Pipeline Worker initialized successfully")
    
    async def _initialize_providers(self) -> None:
        """Initialize AI providers."""
        try:
            # STT Provider (Deepgram Flux)
            self._stt_provider = DeepgramFluxSTTProvider()
            await self._stt_provider.initialize({
                "api_key": os.getenv("DEEPGRAM_API_KEY"),
                "model": "flux-general-en"
            })
            
            # LLM Provider (Groq)
            self._llm_provider = GroqLLMProvider()
            await self._llm_provider.initialize({
                "api_key": os.getenv("GROQ_API_KEY"),
                "model": "llama-3.1-8b-instant"
            })
            
            # TTS Provider (Cartesia)
            self._tts_provider = CartesiaTTSProvider()
            await self._tts_provider.initialize({
                "api_key": os.getenv("CARTESIA_API_KEY")
            })
            
            # Media Gateway
            self._media_gateway = VonageMediaGateway()
            await self._media_gateway.initialize({})
            
            logger.info("AI providers initialized")
            
        except Exception as e:
            logger.error(f"Failed to initialize providers: {e}")
            raise
    
    async def run(self) -> None:
        """
        Main worker loop.
        
        Subscribes to Redis pub/sub and handles call events.
        """
        await self.initialize()
        
        self.running = True
        
        # Subscribe to call events channel
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(self.CALL_CHANNEL)
        
        logger.info(f"Voice Pipeline Worker started - listening on {self.CALL_CHANNEL}")
        
        try:
            async for message in pubsub.listen():
                if not self.running:
                    break
                
                if message["type"] == "message":
                    try:
                        event = json.loads(message["data"])
                        await self._handle_event(event)
                    except json.JSONDecodeError as e:
                        logger.error(f"Invalid JSON in message: {e}")
                    except Exception as e:
                        logger.error(f"Error handling event: {e}", exc_info=True)
                        
        except asyncio.CancelledError:
            logger.info("Worker received cancellation signal")
        finally:
            await pubsub.unsubscribe(self.CALL_CHANNEL)
            await self.shutdown()
    
    async def _handle_event(self, event: dict) -> None:
        """Handle a call event."""
        event_type = event.get("event")
        call_id = event.get("call_id")
        
        if not call_id:
            logger.warning(f"Event missing call_id: {event}")
            return
        
        logger.info(f"Received event: {event_type} for call {call_id}")
        
        if event_type == "call_initiated":
            await self._start_pipeline(call_id, event)
        elif event_type == "call_answered":
            # Pipeline should already be started, just log
            logger.info(f"Call {call_id} answered")
        elif event_type == "call_ended":
            await self._stop_pipeline(call_id, event.get("reason", "unknown"))
        else:
            logger.debug(f"Unhandled event type: {event_type}")
    
    async def _start_pipeline(self, call_id: str, event: dict) -> None:
        """Start voice pipeline for a new call."""
        if call_id in self._active_pipelines:
            logger.warning(f"Pipeline already active for call {call_id}")
            return
        
        if len(self._active_pipelines) >= self.MAX_CONCURRENT_PIPELINES:
            logger.error(f"Max concurrent pipelines reached ({self.MAX_CONCURRENT_PIPELINES})")
            return
        
        # Create pipeline task
        task = asyncio.create_task(self._run_pipeline(call_id, event))
        self._active_pipelines[call_id] = task
        
        logger.info(f"Started pipeline for call {call_id}")
    
    async def _run_pipeline(self, call_id: str, event: dict) -> None:
        """Run the voice pipeline for a call."""
        try:
            # Get or create session
            session = await self._session_manager.get_session(call_id)
            
            if not session:
                # Create new session from event data
                session = await self._session_manager.create_session(
                    call_id=call_id,
                    campaign_id=event.get("campaign_id", ""),
                    lead_id=event.get("lead_id", ""),
                    vonage_call_uuid=call_id,  # Use call_id as vonage UUID
                    system_prompt="You are a helpful AI assistant.",  # Default, should come from campaign
                    voice_id=os.getenv("DEFAULT_VOICE_ID", "sonic"),
                    websocket=None,  # Will be set when WebSocket connects
                    tenant_id=event.get("tenant_id")
                )
            
            # Create pipeline service
            pipeline = VoicePipelineService(
                stt_provider=self._stt_provider,
                llm_provider=self._llm_provider,
                tts_provider=self._tts_provider,
                media_gateway=self._media_gateway
            )
            
            # Run pipeline (this will block until call ends)
            await pipeline.start_pipeline(session)
            
            self._calls_handled += 1
            logger.info(f"Pipeline completed for call {call_id}")
            
        except asyncio.CancelledError:
            logger.info(f"Pipeline cancelled for call {call_id}")
        except Exception as e:
            self._calls_failed += 1
            logger.error(f"Pipeline error for call {call_id}: {e}", exc_info=True)
        finally:
            # Cleanup
            if call_id in self._active_pipelines:
                del self._active_pipelines[call_id]
            
            # End session
            await self._session_manager.end_session(call_id, reason="pipeline_completed")
    
    async def _stop_pipeline(self, call_id: str, reason: str) -> None:
        """Stop voice pipeline for a call."""
        if call_id not in self._active_pipelines:
            logger.debug(f"No active pipeline for call {call_id}")
            return
        
        # Cancel the pipeline task
        task = self._active_pipelines[call_id]
        task.cancel()
        
        try:
            await task
        except asyncio.CancelledError:
            pass
        
        logger.info(f"Stopped pipeline for call {call_id}: {reason}")
    
    async def shutdown(self) -> None:
        """Graceful shutdown."""
        logger.info("Shutting down Voice Pipeline Worker...")
        self.running = False
        
        # Cancel all active pipelines
        for call_id, task in list(self._active_pipelines.items()):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        self._active_pipelines.clear()
        
        # Close connections
        if self._redis:
            await self._redis.close()
        
        if self._session_manager:
            await self._session_manager.shutdown()
        
        # Cleanup providers
        if self._stt_provider:
            await self._stt_provider.cleanup()
        if self._llm_provider:
            await self._llm_provider.cleanup()
        if self._tts_provider:
            await self._tts_provider.cleanup()
        if self._media_gateway:
            await self._media_gateway.cleanup()
        
        logger.info(
            f"Voice Pipeline Worker shutdown complete. "
            f"Handled: {self._calls_handled}, Failed: {self._calls_failed}"
        )
    
    def get_stats(self) -> dict:
        """Get worker statistics."""
        return {
            "running": self.running,
            "active_pipelines": len(self._active_pipelines),
            "calls_handled": self._calls_handled,
            "calls_failed": self._calls_failed,
            "active_call_ids": list(self._active_pipelines.keys())
        }


async def main():
    """Entry point for running voice worker as separate process."""
    worker = VoicePipelineWorker()
    
    # Handle shutdown signals
    loop = asyncio.get_event_loop()
    
    def signal_handler():
        logger.info("Received shutdown signal")
        worker.running = False
    
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass
    
    try:
        await worker.run()
    except KeyboardInterrupt:
        logger.info("Worker interrupted by user")
    finally:
        await worker.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
