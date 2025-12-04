"""
Deepgram Voice Agent Prototype
Uses Deepgram's Voice Agent API (SDK v5+)
Based on official documentation: https://developers.deepgram.com/docs/voice-agent
"""
import os
import asyncio
import pyaudio
import threading
import time
from dotenv import load_dotenv

from deepgram import (
    DeepgramClient,
    AgentWebSocketEvents,
    AgentKeepAlive,
)
from deepgram.clients.agent.v1.websocket.options import SettingsOptions

load_dotenv()


class VoiceAgentDemo:
    """
    Full-duplex voice agent using Deepgram's Voice Agent API
    Handles STT + LLM + TTS automatically
    """
    
    def __init__(self):
        self.api_key = os.getenv("DEEPGRAM_API_KEY")
        if not self.api_key:
            raise ValueError("DEEPGRAM_API_KEY not found in environment")
        
        # Initialize client (SDK v5 pattern from docs)
        self.client = DeepgramClient(self.api_key)
        
        self.connection = None
        self.audio_buffer = bytearray()
        self.file_counter = 0
        self.processing_complete = False
        
        # PyAudio setup
        self.pyaudio_instance = pyaudio.PyAudio()
        self.input_stream = None
        self.output_stream = None
    
    async def start(self):
        """Start the voice agent"""
        print("\n" + "="*70)
        print("  🎙️  DEEPGRAM VOICE AGENT")
        print("  Full-Duplex Voice Conversation")
        print("="*70 + "\n")
        
        # Create connection
        self.connection = self.client.agent.v1.connect()
        print("✓ Created Voice Agent connection")
        
        # Configure the agent
        options = SettingsOptions()
        
        # Audio input configuration (from microphone)
        options.audio.input.encoding = "linear16"
        options.audio.input.sample_rate = 16000
        
        # Audio output configuration (to speakers)
        options.audio.output.encoding = "linear16"
        options.audio.output.sample_rate = 24000
        options.audio.output.container = "none"  # Raw PCM
        
        # Agent configuration
        options.agent.language = "en"
        
        # STT: Use Deepgram Nova-3
        options.agent.listen.provider.type = "deepgram"
        options.agent.listen.provider.model = "nova-3"
        
        # LLM: Use OpenAI GPT-4o-mini
        options.agent.think.provider.type = "open_ai"
        options.agent.think.provider.model = "gpt-4o-mini"
        options.agent.think.prompt = "You are a helpful voice assistant. Keep responses brief and conversational."
        
        # TTS: Use Deepgram Aura
        options.agent.speak.provider.type = "deepgram"
        options.agent.speak.provider.model = "aura-2-asteria-en"
        
        # Greeting
        options.agent.greeting = "Hello! I'm your voice assistant. How can I help you today?"
        
        print("✓ Agent configured (Nova-3 STT + GPT-4o-mini + Aura TTS)")
        
        # Register event handlers
        self._setup_event_handlers()
        
        # Start the connection
        print("✓ Starting Voice Agent connection...")
        if not self.connection.start(options):
            print("❌ Failed to start connection")
            return
        
        print("✓ Voice Agent started successfully!\n")
        print("🎤 Speak into your microphone...")
        print("   (Press Ctrl+C to stop)\n")
        
        # Start audio I/O tasks
        try:
            await asyncio.gather(
                self._audio_input_task(),
                self._audio_output_task(),
                self._keep_alive_task()
            )
        except KeyboardInterrupt:
            print("\n\n🛑 Stopping Voice Agent...")
        finally:
            await self.stop()
    
    def _setup_event_handlers(self):
        """Setup event handlers for Voice Agent"""
        
        def on_audio_data(self, data, **kwargs):
            """Receive audio from agent"""
            self.audio_buffer.extend(data)
        
        def on_agent_audio_done(self, agent_audio_done, **kwargs):
            """Agent finished speaking"""
            print(f"\n🔊 Agent finished speaking (buffer: {len(self.audio_buffer)} bytes)")
            self.audio_buffer = bytearray()
            self.file_counter += 1
        
        def on_conversation_text(self, conversation_text, **kwargs):
            """Display conversation text"""
            role = conversation_text.role if hasattr(conversation_text, 'role') else 'unknown'
            content = conversation_text.content if hasattr(conversation_text, 'content') else str(conversation_text)
            
            if role == 'user':
                print(f"\n👤 You: {content}")
            elif role == 'assistant':
                print(f"\n🤖 Agent: {content}")
        
        def on_welcome(self, welcome, **kwargs):
            """Connection established"""
            print(f"✓ Welcome message received")
        
        def on_settings_applied(self, settings_applied, **kwargs):
            """Settings confirmed"""
            print(f"✓ Settings applied")
        
        def on_user_started_speaking(self, user_started_speaking, **kwargs):
            """User started speaking"""
            print(f"\n🎤 Listening...")
        
        def on_agent_thinking(self, agent_thinking, **kwargs):
            """Agent is processing"""
            print(f"\n🤔 Agent is thinking...")
        
        def on_agent_started_speaking(self, agent_started_speaking, **kwargs):
            """Agent started speaking"""
            print(f"\n🔊 Agent is responding...")
            self.audio_buffer = bytearray()  # Reset buffer
        
        def on_error(self, error, **kwargs):
            """Handle errors"""
            print(f"\n❌ Error: {error}")
        
        # Register all handlers
        self.connection.on(AgentWebSocketEvents.AudioData, on_audio_data)
        self.connection.on(AgentWebSocketEvents.AgentAudioDone, on_agent_audio_done)
        self.connection.on(AgentWebSocketEvents.ConversationText, on_conversation_text)
        self.connection.on(AgentWebSocketEvents.Welcome, on_welcome)
        self.connection.on(AgentWebSocketEvents.SettingsApplied, on_settings_applied)
        self.connection.on(AgentWebSocketEvents.UserStartedSpeaking, on_user_started_speaking)
        self.connection.on(AgentWebSocketEvents.AgentThinking, on_agent_thinking)
        self.connection.on(AgentWebSocketEvents.AgentStartedSpeaking, on_agent_started_speaking)
        self.connection.on(AgentWebSocketEvents.Error, on_error)
    
    async def _audio_input_task(self):
        """Capture audio from microphone and send to agent"""
        try:
            # Open microphone stream
            self.input_stream = self.pyaudio_instance.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                input=True,
                frames_per_buffer=1280  # 80ms chunks
            )
            
            while True:
                # Read audio from microphone
                audio_data = self.input_stream.read(1280, exception_on_overflow=False)
                
                # Send to Voice Agent
                self.connection.send(audio_data)
                
                await asyncio.sleep(0.001)  # Small delay
                
        except Exception as e:
            print(f"Audio input error: {e}")
        finally:
            if self.input_stream:
                self.input_stream.stop_stream()
                self.input_stream.close()
    
    async def _audio_output_task(self):
        """Play audio from agent through speakers"""
        try:
            # Open speaker stream
            self.output_stream = self.pyaudio_instance.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=24000,
                output=True
            )
            
            while True:
                # Check if we have audio to play
                if len(self.audio_buffer) > 0:
                    # Play audio chunk
                    chunk = bytes(self.audio_buffer[:4800])  # ~100ms at 24kHz
                    self.audio_buffer = self.audio_buffer[4800:]
                    
                    try:
                        self.output_stream.write(chunk)
                    except:
                        pass
                
                await asyncio.sleep(0.01)
                
        except Exception as e:
            print(f"Audio output error: {e}")
        finally:
            if self.output_stream:
                self.output_stream.stop_stream()
                self.output_stream.close()
    
    async def _keep_alive_task(self):
        """Send keep-alive messages every 5 seconds"""
        while True:
            await asyncio.sleep(5)
            try:
                self.connection.send(str(AgentKeepAlive()))
            except:
                pass
    
    async def stop(self):
        """Stop the voice agent"""
        print("\n🛑 Stopping Voice Agent...")
        
        if self.connection:
            try:
                self.connection.finish()
            except:
                pass
        
        if self.input_stream:
            try:
                self.input_stream.stop_stream()
                self.input_stream.close()
            except:
                pass
        
        if self.output_stream:
            try:
                self.output_stream.stop_stream()
                self.output_stream.close()
            except:
                pass
        
        if self.pyaudio_instance:
            try:
                self.pyaudio_instance.terminate()
            except:
                pass
        
        print("✓ Voice Agent stopped\n")


async def main():
    """Run the voice agent demo"""
    agent = VoiceAgentDemo()
    await agent.start()


if __name__ == "__main__":
    asyncio.run(main())
