"""
Test the ACTUAL Deepgram Flux Pipeline used in the app.
Uses the same DeepgramFluxSTTProvider from the voice pipeline.

This tests the exact same code path as dummy calls.
"""
import os
import sys
import asyncio
import time
from datetime import datetime
from dotenv import load_dotenv

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

load_dotenv()

try:
    import pyaudio
except ImportError:
    print("Install pyaudio: pip install pyaudio")
    sys.exit(1)

from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider
from app.domain.models.conversation import AudioChunk, BargeInSignal

# Audio Config (same as app)
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_SIZE = 4096  # Same as voice pipeline


def print_colored(text, color="white"):
    colors = {
        "blue": "\033[94m", "green": "\033[92m", "yellow": "\033[93m",
        "red": "\033[91m", "cyan": "\033[96m", "bold": "\033[1m", "end": "\033[0m"
    }
    print(f"{colors.get(color, '')}{text}{colors['end']}")


class FluxPipelineTest:
    def __init__(self):
        self.audio_queue = asyncio.Queue(maxsize=100)
        self.running = True
        self.speech_start_time = None
        
        # Metrics
        self.chunks_sent = 0
        self.transcripts_received = 0
        self.partial_count = 0
        self.final_count = 0
        self.latencies = []
    
    def audio_callback(self, in_data, frame_count, time_info, status):
        """PyAudio callback"""
        if self.running:
            # Simple speech detection
            samples = [int.from_bytes(in_data[i:i+2], 'little', signed=True) 
                      for i in range(0, min(len(in_data), 200), 2)]
            energy = sum(abs(s) for s in samples) / len(samples) if samples else 0
            
            if energy > 500 and self.speech_start_time is None:
                self.speech_start_time = time.time()
            
            try:
                self.audio_queue.put_nowait(in_data)
            except asyncio.QueueFull:
                pass
        
        return (None, pyaudio.paContinue)
    
    async def audio_stream(self):
        """Async generator that yields AudioChunks (same as voice pipeline)"""
        while self.running:
            try:
                audio_data = await asyncio.wait_for(
                    self.audio_queue.get(),
                    timeout=0.05
                )
                self.chunks_sent += 1
                yield AudioChunk(
                    data=audio_data,
                    sample_rate=SAMPLE_RATE,
                    channels=CHANNELS
                )
            except asyncio.TimeoutError:
                continue
    
    async def run(self):
        """Run the actual Flux pipeline test"""
        print_colored("\n=== Deepgram FLUX Pipeline Test ===", "bold")
        print_colored("Testing the EXACT same code path as dummy calls", "cyan")
        print()
        
        # Initialize the ACTUAL DeepgramFluxSTTProvider
        stt_provider = DeepgramFluxSTTProvider()
        await stt_provider.initialize({
            "api_key": os.getenv("DEEPGRAM_API_KEY"),
            "model": "flux-general-en",  # Same as app
            "sample_rate": SAMPLE_RATE,
            "encoding": "linear16"
        })
        
        print(f"Provider: {stt_provider}")
        print(f"Audio: {SAMPLE_RATE}Hz, {CHUNK_SIZE} bytes/chunk")
        print()
        
        # Initialize PyAudio
        p = pyaudio.PyAudio()
        
        try:
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
            print("-" * 60)
            
            # Use the ACTUAL stream_transcribe method
            async for transcript in stt_provider.stream_transcribe(
                audio_stream=self.audio_stream(),
                language="en"
            ):
                receive_time = time.time()
                self.transcripts_received += 1
                
                # Check for barge-in signal
                if isinstance(transcript, BargeInSignal):
                    print_colored("\n⚡ BARGE-IN: User started speaking!", "yellow")
                    continue
                
                # Calculate latency
                latency = 0
                if self.speech_start_time:
                    latency = (receive_time - self.speech_start_time) * 1000
                
                text = transcript.text
                is_final = transcript.is_final
                
                if not text:
                    # Empty final = end of turn
                    if is_final:
                        print_colored(f"\n--- END OF TURN ---", "cyan")
                        print(f"    Latency: {latency:.0f}ms | Chunks: {self.chunks_sent}")
                        if latency > 0:
                            self.latencies.append(latency)
                        self.speech_start_time = None
                        self.chunks_sent = 0
                        print()
                    continue
                
                # Display transcript
                clear = "\r" + " " * 80 + "\r"
                
                if is_final:
                    self.final_count += 1
                    print(f"{clear}", end="")
                    print_colored(f"✓ FINAL: {text}", "green")
                    print_colored(f"  └─ Latency: {latency:.0f}ms", "cyan")
                else:
                    self.partial_count += 1
                    print(f"{clear}🎤 {text}...", end="", flush=True)
        
        except KeyboardInterrupt:
            print("\n\nStopping...")
        except Exception as e:
            print_colored(f"\nError: {e}", "red")
            import traceback
            traceback.print_exc()
        finally:
            self.running = False
            stream.stop_stream()
            stream.close()
            p.terminate()
            await stt_provider.cleanup()
            
            # Summary
            print("\n" + "-" * 60)
            print_colored("Summary:", "bold")
            print(f"  Total transcripts: {self.transcripts_received}")
            print(f"  Partial: {self.partial_count}, Final: {self.final_count}")
            if self.latencies:
                avg = sum(self.latencies) / len(self.latencies)
                print_colored(f"  Avg end-of-turn latency: {avg:.0f}ms", "green")
                print(f"  Min: {min(self.latencies):.0f}ms, Max: {max(self.latencies):.0f}ms")


if __name__ == "__main__":
    try:
        test = FluxPipelineTest()
        asyncio.run(test.run())
    except KeyboardInterrupt:
        print("\nTest stopped.")
