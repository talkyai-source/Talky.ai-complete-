"""
Deepgram Real-Time Transcription Test v2
Optimized for low-latency real-time transcription.

Uses non-blocking audio capture with proper async handling.
"""
import os
import asyncio
import json
import sys
import time
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

try:
    import websockets
    import pyaudio
except ImportError as e:
    print(f"Missing: {e}. Install: pip install websockets pyaudio")
    sys.exit(1)

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
if not DEEPGRAM_API_KEY:
    print("ERROR: DEEPGRAM_API_KEY not set")
    sys.exit(1)

# Audio Config
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_DURATION_MS = 100  # Send audio every 100ms for low latency
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION_MS / 1000)  # 1600 samples = 100ms

# Deepgram URL with interim_results for real-time transcription
DEEPGRAM_URL = (
    f"wss://api.deepgram.com/v1/listen?"
    f"model=nova-2&"
    f"language=en&"
    f"encoding=linear16&"
    f"sample_rate={SAMPLE_RATE}&"
    f"channels={CHANNELS}&"
    f"punctuate=true&"
    f"interim_results=true&"  # Get partial results
    f"endpointing=300&"       # Faster end-of-speech detection
    f"utterance_end_ms=1000"  # 1s silence = utterance end
)


def print_colored(text, color="white"):
    """Print with colors"""
    colors = {
        "blue": "\033[94m",
        "green": "\033[92m", 
        "yellow": "\033[93m",
        "red": "\033[91m",
        "cyan": "\033[96m",
        "white": "\033[0m",
        "bold": "\033[1m",
    }
    end = "\033[0m"
    print(f"{colors.get(color, '')}{text}{end}")


class DeepgramRealtimeTest:
    def __init__(self):
        self.audio_queue = asyncio.Queue(maxsize=50)
        self.running = True
        self.speech_start_time = None
        self.last_transcript = ""
        
        # Metrics
        self.chunks_sent = 0
        self.transcripts_received = 0
        self.latencies = []
    
    def audio_callback(self, in_data, frame_count, time_info, status):
        """PyAudio callback - runs in separate thread"""
        if self.running:
            # Track when speech starts (simple energy detection)
            audio_bytes = in_data
            samples = [int.from_bytes(audio_bytes[i:i+2], 'little', signed=True) 
                      for i in range(0, min(len(audio_bytes), 200), 2)]
            energy = sum(abs(s) for s in samples) / len(samples) if samples else 0
            
            if energy > 500 and self.speech_start_time is None:
                self.speech_start_time = time.time()
            
            # Put audio in queue (non-blocking)
            try:
                self.audio_queue.put_nowait((audio_bytes, time.time()))
            except asyncio.QueueFull:
                pass  # Drop if queue is full
        
        return (None, pyaudio.paContinue)
    
    async def send_audio(self, ws):
        """Send audio to Deepgram WebSocket"""
        try:
            while self.running:
                try:
                    audio_data, timestamp = await asyncio.wait_for(
                        self.audio_queue.get(), 
                        timeout=0.1
                    )
                    await ws.send(audio_data)
                    self.chunks_sent += 1
                except asyncio.TimeoutError:
                    continue
        except Exception as e:
            print_colored(f"\nSend error: {e}", "red")
    
    async def receive_transcripts(self, ws):
        """Receive transcripts from Deepgram"""
        try:
            async for message in ws:
                data = json.loads(message)
                receive_time = time.time()
                
                if data.get("type") == "Results":
                    self.transcripts_received += 1
                    
                    channel = data.get("channel", {})
                    alternatives = channel.get("alternatives", [{}])
                    transcript = alternatives[0].get("transcript", "").strip()
                    is_final = data.get("is_final", False)
                    speech_final = data.get("speech_final", False)
                    
                    if transcript:
                        # Calculate latency from speech start
                        latency = 0
                        if self.speech_start_time:
                            latency = (receive_time - self.speech_start_time) * 1000
                            if is_final:
                                self.latencies.append(latency)
                        
                        # Clear previous partial and display
                        clear_line = "\r" + " " * 80 + "\r"
                        
                        if speech_final:
                            # Final - user stopped speaking
                            print(clear_line, end="")
                            print_colored(f"✓ YOU: {transcript}", "green")
                            print_colored(f"  └─ Latency: {latency:.0f}ms | Chunks: {self.chunks_sent}", "cyan")
                            print()
                            self.speech_start_time = None  # Reset for next utterance
                            self.chunks_sent = 0
                        elif is_final:
                            # Interim final
                            print(clear_line, end="")
                            print_colored(f"○ {transcript} ({latency:.0f}ms)", "yellow")
                        else:
                            # Partial
                            print(f"{clear_line}🎤 {transcript}...", end="", flush=True)
                
                elif data.get("type") == "Metadata":
                    model = data.get("model_info", {}).get("name", "unknown")
                    print_colored(f"✓ Model: {model}", "green")
                    print()
        
        except Exception as e:
            print_colored(f"\nReceive error: {e}", "red")
    
    async def run(self):
        """Main test loop"""
        print_colored("\n=== Deepgram Real-Time Test v2 ===", "bold")
        print(f"API Key: {DEEPGRAM_API_KEY[:10]}...{DEEPGRAM_API_KEY[-4:]}")
        print(f"Audio: {SAMPLE_RATE}Hz, {CHUNK_DURATION_MS}ms chunks ({CHUNK_SIZE} samples)")
        print()
        
        # Initialize PyAudio
        p = pyaudio.PyAudio()
        
        try:
            # Open stream with callback
            stream = p.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
                stream_callback=self.audio_callback
            )
            stream.start_stream()
            
            print_colored("🎤 Microphone ready! Speak now...", "green")
            print_colored("   Press Ctrl+C to stop\n", "yellow")
            print("-" * 50)
            
            # Connect to Deepgram
            headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}
            
            async with websockets.connect(DEEPGRAM_URL, additional_headers=headers) as ws:
                print_colored("✓ Connected to Deepgram", "green")
                
                # Run send and receive concurrently
                await asyncio.gather(
                    self.send_audio(ws),
                    self.receive_transcripts(ws)
                )
        
        except websockets.exceptions.ConnectionClosed as e:
            print_colored(f"\nConnection closed: {e}", "red")
        except Exception as e:
            print_colored(f"\nError: {e}", "red")
        finally:
            self.running = False
            stream.stop_stream()
            stream.close()
            p.terminate()
            
            # Summary
            print("\n" + "-" * 50)
            print_colored("Summary:", "bold")
            print(f"  Transcripts: {self.transcripts_received}")
            if self.latencies:
                avg_latency = sum(self.latencies) / len(self.latencies)
                print(f"  Avg latency: {avg_latency:.0f}ms")
                print(f"  Min latency: {min(self.latencies):.0f}ms")
                print(f"  Max latency: {max(self.latencies):.0f}ms")


if __name__ == "__main__":
    try:
        test = DeepgramRealtimeTest()
        asyncio.run(test.run())
    except KeyboardInterrupt:
        print("\n\nTest stopped.")
