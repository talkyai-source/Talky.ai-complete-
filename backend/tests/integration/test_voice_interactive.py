"""
Interactive Voice Pipeline Test - Terminal Edition
===================================================
A full interactive voice test that works like the Ask AI frontend but in terminal.

Features:
- Choose voice agents (Sophia, Emma, Alex)
- Real microphone capture
- Live transcript display
- LLM response display
- Audio playback of TTS responses
- Full latency metrics

Requirements:
    pip install websockets colorama sounddevice numpy

Usage:
    python -m tests.integration.test_voice_interactive
"""

import asyncio
import json
import time
import struct
import sys
import os
import threading
import queue
from datetime import datetime
from typing import Optional

try:
    import websockets
    from colorama import init, Fore, Style, Back
    import sounddevice as sd
    import numpy as np
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("\nInstall required packages:")
    print("  pip install websockets colorama sounddevice numpy")
    sys.exit(1)

# Initialize colorama for Windows
init()

# Audio settings
SAMPLE_RATE = 16000
CHANNELS = 1
BLOCK_SIZE = 3200  # 200ms of audio at 16kHz

# WebSocket URL
WS_BASE_URL = "ws://localhost:8000/api/v1/ws/ai-test"

# Available voices
VOICES = {
    "1": {"id": "sophia", "name": "Sophia", "desc": "Warm & Professional (Female)"},
    "2": {"id": "emma", "name": "Emma", "desc": "Energetic & Friendly (Female)"},
    "3": {"id": "alex", "name": "Alex", "desc": "Confident & Clear (Male)"}
}


class AudioPlayer:
    """Plays Float32 audio chunks in real-time."""
    
    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.audio_queue = queue.Queue()
        self.is_playing = False
        self.stream = None
        self._stop_event = threading.Event()
    
    def start(self):
        """Start the audio player."""
        self._stop_event.clear()
        self.is_playing = True
        
        def audio_callback(outdata, frames, time_info, status):
            try:
                # Get audio from queue (non-blocking)
                data = self.audio_queue.get_nowait()
                # Convert bytes to numpy array
                samples = np.frombuffer(data, dtype=np.float32)
                # Ensure correct length
                if len(samples) < frames:
                    # Pad with zeros
                    samples = np.pad(samples, (0, frames - len(samples)))
                elif len(samples) > frames:
                    samples = samples[:frames]
                outdata[:, 0] = samples
            except queue.Empty:
                # No audio available, play silence
                outdata.fill(0)
        
        self.stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype=np.float32,
            callback=audio_callback,
            blocksize=4096  # ~256ms buffer
        )
        self.stream.start()
    
    def add_audio(self, data: bytes):
        """Add audio chunk to playback queue."""
        if self.is_playing:
            self.audio_queue.put(data)
    
    def stop(self):
        """Stop the audio player."""
        self._stop_event.set()
        self.is_playing = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
        # Clear queue
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except:
                pass


class InteractiveVoiceTest:
    """Interactive voice test with microphone and audio playback."""
    
    def __init__(self):
        self.ws = None
        self.voice_id = "sophia"
        self.voice_name = "Sophia"
        self.session_id = None
        self.is_recording = False
        self.is_connected = False
        self.audio_player = AudioPlayer(SAMPLE_RATE)
        
        # Metrics
        self.turn_start_time: Optional[float] = None
        self.last_transcript = ""
        self.audio_chunks_received = 0
        self.total_audio_bytes = 0
    
    def print_header(self):
        """Print the app header."""
        os.system('cls' if os.name == 'nt' else 'clear')
        print(f"\n{Fore.CYAN}{'='*70}")
        print(f"  🎤 TALKY.AI - INTERACTIVE VOICE TEST (Terminal Edition)")
        print(f"{'='*70}{Style.RESET_ALL}")
    
    def print_status(self, message: str, color=Fore.WHITE):
        """Print a status message."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"{Fore.WHITE}[{timestamp}] {color}{message}{Style.RESET_ALL}")
    
    def print_transcript(self, text: str, is_final: bool):
        """Print user transcript."""
        badge = f"{Back.GREEN} FINAL {Style.RESET_ALL}" if is_final else f"{Back.YELLOW} LIVE {Style.RESET_ALL}"
        print(f"\n{Fore.YELLOW}👤 YOU {badge}: {Fore.WHITE}{text}{Style.RESET_ALL}")
    
    def print_llm_response(self, text: str, latency_ms: float):
        """Print LLM response."""
        print(f"\n{Fore.MAGENTA}🤖 {self.voice_name} (LLM: {latency_ms:.0f}ms):{Style.RESET_ALL}")
        print(f"   {Fore.WHITE}\"{text}\"{Style.RESET_ALL}")
    
    def print_metrics(self, llm_latency: float, tts_latency: float, total_latency: float):
        """Print turn metrics."""
        print(f"\n{Fore.CYAN}📊 TURN METRICS:{Style.RESET_ALL}")
        print(f"   ├── LLM Latency:  {Fore.GREEN}{llm_latency:.0f}ms{Style.RESET_ALL}")
        print(f"   ├── TTS Latency:  {Fore.GREEN}{tts_latency:.0f}ms{Style.RESET_ALL}")
        print(f"   └── Total:        {Fore.CYAN}{total_latency:.0f}ms{Style.RESET_ALL}")
        print(f"   └── Audio:        {self.audio_chunks_received} chunks, {self.total_audio_bytes:,} bytes")
    
    async def select_voice(self):
        """Let user select a voice."""
        print(f"\n{Fore.CYAN}Select a Voice Agent:{Style.RESET_ALL}\n")
        for key, voice in VOICES.items():
            print(f"  {Fore.GREEN}[{key}]{Style.RESET_ALL} {voice['name']} - {voice['desc']}")
        
        while True:
            choice = input(f"\n{Fore.YELLOW}Enter choice (1-3): {Style.RESET_ALL}").strip()
            if choice in VOICES:
                self.voice_id = VOICES[choice]["id"]
                self.voice_name = VOICES[choice]["name"]
                print(f"\n{Fore.GREEN}✓ Selected: {self.voice_name}{Style.RESET_ALL}")
                return
            print(f"{Fore.RED}Invalid choice. Please enter 1, 2, or 3.{Style.RESET_ALL}")
    
    async def connect(self):
        """Connect to WebSocket."""
        self.session_id = f"terminal-test-{int(time.time() * 1000)}"
        ws_url = f"{WS_BASE_URL}/{self.session_id}"
        
        self.print_status(f"Connecting to {ws_url}...", Fore.YELLOW)
        
        try:
            self.ws = await websockets.connect(ws_url, ping_interval=30)
            self.is_connected = True
            self.print_status("Connected!", Fore.GREEN)
            
            # Send config
            config = {"type": "config", "voice_id": self.voice_id}
            await self.ws.send(json.dumps(config))
            self.print_status(f"Config sent (voice: {self.voice_id})", Fore.GREEN)
            
            return True
        except Exception as e:
            self.print_status(f"Connection failed: {e}", Fore.RED)
            return False
    
    async def handle_messages(self):
        """Handle incoming WebSocket messages."""
        try:
            while self.is_connected:
                message = await asyncio.wait_for(self.ws.recv(), timeout=60.0)
                
                if isinstance(message, bytes):
                    # Audio data - play it
                    self.audio_chunks_received += 1
                    self.total_audio_bytes += len(message)
                    self.audio_player.add_audio(message)
                    
                    # Show audio progress occasionally
                    if self.audio_chunks_received <= 3 or self.audio_chunks_received % 10 == 0:
                        self.print_status(f"🔊 Playing audio chunk #{self.audio_chunks_received} ({len(message)} bytes)", Fore.BLUE)
                
                else:
                    # JSON message
                    data = json.loads(message)
                    msg_type = data.get("type", "unknown")
                    
                    if msg_type == "ready":
                        self.print_status(f"Ready! Agent: {data.get('agent_name', 'Unknown')}", Fore.GREEN)
                        print(f"\n{Fore.CYAN}{'─'*70}{Style.RESET_ALL}")
                        print(f"{Fore.YELLOW}🎙️  Listening for {self.voice_name}'s introduction...{Style.RESET_ALL}")
                    
                    elif msg_type == "llm_response":
                        latency = data.get("latency_ms", 0)
                        self.print_llm_response(data.get("text", ""), latency)
                        # Reset audio counters for this turn
                        self.audio_chunks_received = 0
                        self.total_audio_bytes = 0
                    
                    elif msg_type == "transcript":
                        self.last_transcript = data.get("text", "")
                        self.print_transcript(self.last_transcript, data.get("is_final", False))
                    
                    elif msg_type == "turn_complete":
                        self.print_metrics(
                            data.get("llm_latency_ms", 0),
                            data.get("tts_latency_ms", 0),
                            data.get("total_latency_ms", 0)
                        )
                        print(f"\n{Fore.CYAN}{'─'*70}{Style.RESET_ALL}")
                        print(f"{Fore.GREEN}🎤 Your turn! Speak now... (Press CTRL+C to end){Style.RESET_ALL}")
                    
                    elif msg_type == "barge_in":
                        self.print_status("🛑 Barge-in detected - listening to you...", Fore.YELLOW)
                    
                    elif msg_type == "heartbeat":
                        pass  # Ignore heartbeats
                    
                    elif msg_type == "error":
                        self.print_status(f"Error: {data.get('message', 'Unknown')}", Fore.RED)
                    
                    else:
                        self.print_status(f"Event: {msg_type} - {json.dumps(data)}", Fore.WHITE)
        
        except asyncio.TimeoutError:
            self.print_status("Connection timeout - reconnecting...", Fore.YELLOW)
        except websockets.exceptions.ConnectionClosed:
            self.print_status("Connection closed", Fore.YELLOW)
            self.is_connected = False
        except Exception as e:
            self.print_status(f"Message handler error: {e}", Fore.RED)
    
    async def stream_microphone(self):
        """Stream microphone audio to WebSocket."""
        self.print_status("Starting microphone...", Fore.YELLOW)
        
        audio_queue = queue.Queue()
        
        def audio_callback(indata, frames, time_info, status):
            if status:
                pass  # Ignore status messages
            # Convert Float32 to Int16 for WebSocket
            int16_data = (indata[:, 0] * 32767).astype(np.int16)
            audio_queue.put(int16_data.tobytes())
        
        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=np.float32,
                blocksize=BLOCK_SIZE,
                callback=audio_callback
            ):
                self.print_status("🎤 Microphone active! Speak now...", Fore.GREEN)
                self.is_recording = True
                
                while self.is_connected and self.is_recording:
                    try:
                        # Get audio from queue
                        data = audio_queue.get(timeout=0.1)
                        # Send to WebSocket
                        await self.ws.send(data)
                    except queue.Empty:
                        await asyncio.sleep(0.01)
                    except Exception as e:
                        self.print_status(f"Audio send error: {e}", Fore.RED)
                        break
        
        except Exception as e:
            self.print_status(f"Microphone error: {e}", Fore.RED)
        finally:
            self.is_recording = False
    
    async def run(self):
        """Run the interactive test."""
        self.print_header()
        
        # Select voice
        await self.select_voice()
        
        # Connect to WebSocket
        if not await self.connect():
            return
        
        # Start audio player
        self.audio_player.start()
        
        try:
            # Run message handler and microphone streamer concurrently
            await asyncio.gather(
                self.handle_messages(),
                self.stream_microphone()
            )
        
        except KeyboardInterrupt:
            print(f"\n\n{Fore.YELLOW}Ending session...{Style.RESET_ALL}")
        
        finally:
            # Cleanup
            self.is_connected = False
            self.is_recording = False
            self.audio_player.stop()
            
            if self.ws:
                try:
                    await self.ws.send(json.dumps({"type": "end_call"}))
                    await self.ws.close()
                except:
                    pass
            
            print(f"\n{Fore.GREEN}✓ Session ended. Goodbye!{Style.RESET_ALL}\n")


async def main():
    """Main entry point."""
    print(f"\n{Fore.CYAN}{'='*70}")
    print(f"  🎤 TALKY.AI - INTERACTIVE VOICE TEST")
    print(f"  Talk to AI voice agents directly from your terminal!")
    print(f"{'='*70}{Style.RESET_ALL}")
    
    # Check audio devices
    try:
        devices = sd.query_devices()
        default_input = sd.query_devices(kind='input')
        print(f"\n{Fore.GREEN}✓ Audio device found: {default_input['name']}{Style.RESET_ALL}")
    except Exception as e:
        print(f"\n{Fore.RED}✗ No audio input device found: {e}{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}Please connect a microphone and try again.{Style.RESET_ALL}")
        return
    
    # Run the interactive test
    test = InteractiveVoiceTest()
    await test.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Interrupted.{Style.RESET_ALL}")
