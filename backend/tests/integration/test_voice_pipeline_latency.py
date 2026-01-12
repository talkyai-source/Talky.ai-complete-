"""
Voice Pipeline Latency Diagnostic Test
======================================
Tests the full voice pipeline WebSocket endpoint and measures latency for:
- STT (Speech-to-Text)
- LLM (Language Model Response)
- TTS (Text-to-Speech)

Run: python -m tests.integration.test_voice_pipeline_latency
"""

import asyncio
import json
import time
import struct
import wave
import os
import sys
from datetime import datetime
from typing import Optional
import websockets
from colorama import init, Fore, Style, Back

# Initialize colorama for Windows terminal colors
init()

# Configuration
WS_URL = "ws://localhost:8000/api/v1/ws/ai-test/latency-test-{}"
SAMPLE_RATE = 16000
CHANNELS = 1

class LatencyMetrics:
    """Track latency metrics for each component."""
    def __init__(self):
        self.reset()
    
    def reset(self):
        self.audio_send_start: Optional[float] = None
        self.first_transcript_time: Optional[float] = None
        self.final_transcript_time: Optional[float] = None
        self.llm_response_time: Optional[float] = None
        self.first_audio_time: Optional[float] = None
        self.turn_complete_time: Optional[float] = None
        self.audio_chunks_received: int = 0
        self.audio_bytes_received: int = 0
    
    def get_stt_latency(self) -> Optional[float]:
        if self.audio_send_start and self.first_transcript_time:
            return (self.first_transcript_time - self.audio_send_start) * 1000
        return None
    
    def get_llm_latency(self) -> Optional[float]:
        if self.final_transcript_time and self.llm_response_time:
            return (self.llm_response_time - self.final_transcript_time) * 1000
        return None
    
    def get_tts_latency(self) -> Optional[float]:
        if self.llm_response_time and self.first_audio_time:
            return (self.first_audio_time - self.llm_response_time) * 1000
        return None
    
    def get_total_latency(self) -> Optional[float]:
        if self.audio_send_start and self.turn_complete_time:
            return (self.turn_complete_time - self.audio_send_start) * 1000
        return None


def print_header(text: str):
    """Print a styled header."""
    print(f"\n{Fore.CYAN}{'='*70}")
    print(f"{Fore.CYAN}  {text}")
    print(f"{Fore.CYAN}{'='*70}{Style.RESET_ALL}")


def print_event(event_type: str, data: dict, timestamp: float):
    """Print a WebSocket event with formatting."""
    time_str = datetime.fromtimestamp(timestamp).strftime("%H:%M:%S.%f")[:-3]
    
    color_map = {
        "ready": Fore.GREEN,
        "transcript": Fore.YELLOW,
        "llm_response": Fore.MAGENTA,
        "turn_complete": Fore.CYAN,
        "barge_in": Fore.RED,
        "heartbeat": Fore.WHITE,
        "error": Fore.RED,
        "audio": Fore.BLUE,
    }
    
    color = color_map.get(event_type, Fore.WHITE)
    
    # Special formatting for different event types
    if event_type == "transcript":
        final_badge = f"{Back.GREEN}FINAL{Style.RESET_ALL}" if data.get("is_final") else f"{Back.YELLOW}PARTIAL{Style.RESET_ALL}"
        print(f"{Fore.WHITE}[{time_str}] {color}📝 TRANSCRIPT {final_badge}: {Fore.WHITE}\"{data.get('text', '')}\"{Style.RESET_ALL}")
        if data.get("confidence"):
            print(f"           └── Confidence: {data.get('confidence', 0):.2f}")
    
    elif event_type == "llm_response":
        print(f"{Fore.WHITE}[{time_str}] {color}🤖 LLM RESPONSE:{Style.RESET_ALL}")
        print(f"           └── {Fore.WHITE}\"{data.get('text', '')}\"{Style.RESET_ALL}")
        if data.get("latency_ms"):
            print(f"           └── LLM Latency: {Fore.GREEN}{data.get('latency_ms', 0):.0f}ms{Style.RESET_ALL}")
    
    elif event_type == "turn_complete":
        print(f"{Fore.WHITE}[{time_str}] {color}✅ TURN COMPLETE:{Style.RESET_ALL}")
        print(f"           ├── LLM Latency: {Fore.GREEN}{data.get('llm_latency_ms', 0):.0f}ms{Style.RESET_ALL}")
        print(f"           ├── TTS Latency: {Fore.GREEN}{data.get('tts_latency_ms', 0):.0f}ms{Style.RESET_ALL}")
        print(f"           └── Total: {Fore.CYAN}{data.get('total_latency_ms', 0):.0f}ms{Style.RESET_ALL}")
    
    elif event_type == "ready":
        print(f"{Fore.WHITE}[{time_str}] {color}🚀 READY:{Style.RESET_ALL}")
        print(f"           ├── Session: {data.get('session_id', 'N/A')}")
        print(f"           ├── Voice: {data.get('agent_name', 'N/A')} ({data.get('voice_id', 'N/A')})")
        print(f"           └── Description: {data.get('agent_description', 'N/A')}")
    
    elif event_type == "audio":
        print(f"{Fore.WHITE}[{time_str}] {color}🔊 AUDIO CHUNK: {data.get('bytes', 0)} bytes (chunk #{data.get('chunk_num', 0)}){Style.RESET_ALL}")
    
    elif event_type == "barge_in":
        print(f"{Fore.WHITE}[{time_str}] {color}🛑 BARGE-IN DETECTED: {data.get('message', '')}{Style.RESET_ALL}")
    
    elif event_type == "error":
        print(f"{Fore.WHITE}[{time_str}] {color}❌ ERROR: {data.get('message', str(data))}{Style.RESET_ALL}")
    
    else:
        print(f"{Fore.WHITE}[{time_str}] {color}📨 {event_type.upper()}: {json.dumps(data)}{Style.RESET_ALL}")


def generate_test_audio(duration_seconds: float = 2.0, frequency: float = 440.0) -> bytes:
    """
    Generate a simple sine wave audio for testing.
    Returns LINEAR16 PCM audio data.
    """
    import math
    
    num_samples = int(SAMPLE_RATE * duration_seconds)
    audio_data = bytearray()
    
    for i in range(num_samples):
        # Generate sine wave with some amplitude variation to simulate speech-like energy
        t = i / SAMPLE_RATE
        # Add multiple frequencies to make it more speech-like (not pure tone)
        sample = 0.3 * math.sin(2 * math.pi * frequency * t)
        sample += 0.2 * math.sin(2 * math.pi * (frequency * 1.5) * t)
        sample += 0.1 * math.sin(2 * math.pi * (frequency * 2) * t)
        
        # Add slight amplitude envelope
        envelope = min(i / (SAMPLE_RATE * 0.1), 1.0) * min((num_samples - i) / (SAMPLE_RATE * 0.1), 1.0)
        sample *= envelope
        
        # Convert to 16-bit PCM
        int_sample = int(sample * 32767 * 0.8)
        int_sample = max(-32768, min(32767, int_sample))
        audio_data.extend(struct.pack('<h', int_sample))
    
    return bytes(audio_data)


def generate_silence(duration_seconds: float = 0.5) -> bytes:
    """Generate silent audio."""
    num_samples = int(SAMPLE_RATE * duration_seconds)
    return b'\x00\x00' * num_samples  # 16-bit silence


async def test_voice_pipeline(voice_id: str = "sophia", use_microphone: bool = False):
    """
    Test the voice pipeline and measure latencies.
    
    Args:
        voice_id: Which voice to test (sophia, emma, alex)
        use_microphone: If True, capture from microphone (requires sounddevice)
    """
    session_id = f"latency-test-{int(time.time() * 1000)}"
    ws_url = f"ws://localhost:8000/api/v1/ws/ai-test/{session_id}"
    
    print_header("VOICE PIPELINE LATENCY DIAGNOSTIC TEST")
    print(f"\n{Fore.WHITE}Configuration:")
    print(f"  • WebSocket URL: {Fore.CYAN}{ws_url}{Style.RESET_ALL}")
    print(f"  • Voice Agent: {Fore.CYAN}{voice_id}{Style.RESET_ALL}")
    print(f"  • Sample Rate: {Fore.CYAN}{SAMPLE_RATE} Hz{Style.RESET_ALL}")
    print(f"  • Test Mode: {Fore.CYAN}{'Microphone' if use_microphone else 'Synthesized Audio'}{Style.RESET_ALL}")
    
    metrics = LatencyMetrics()
    intro_audio_chunks = 0
    intro_audio_bytes = 0
    receiving_intro = True
    
    try:
        print(f"\n{Fore.YELLOW}Connecting to WebSocket...{Style.RESET_ALL}")
        
        async with websockets.connect(ws_url, ping_interval=30, ping_timeout=10) as ws:
            print(f"{Fore.GREEN}✓ Connected!{Style.RESET_ALL}")
            
            # Send config message
            config = {
                "type": "config",
                "voice_id": voice_id
            }
            await ws.send(json.dumps(config))
            print(f"{Fore.GREEN}✓ Config sent (voice: {voice_id}){Style.RESET_ALL}")
            
            print_header("WAITING FOR VOICE INTRO & TURN COMPLETE")
            
            # Receive messages until we get the introduction complete
            intro_complete = False
            while not intro_complete:
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    timestamp = time.time()
                    
                    if isinstance(message, bytes):
                        # Audio data from TTS
                        intro_audio_chunks += 1
                        intro_audio_bytes += len(message)
                        if intro_audio_chunks <= 3 or intro_audio_chunks % 5 == 0:
                            print_event("audio", {
                                "bytes": len(message), 
                                "chunk_num": intro_audio_chunks,
                                "total_bytes": intro_audio_bytes
                            }, timestamp)
                    else:
                        # JSON message
                        data = json.loads(message)
                        msg_type = data.get("type", "unknown")
                        print_event(msg_type, data, timestamp)
                        
                        if msg_type == "turn_complete":
                            intro_complete = True
                            receiving_intro = False
                        elif msg_type == "error":
                            print(f"{Fore.RED}Error received, stopping test{Style.RESET_ALL}")
                            return
                
                except asyncio.TimeoutError:
                    print(f"{Fore.YELLOW}Timeout waiting for intro, continuing...{Style.RESET_ALL}")
                    break
            
            print(f"\n{Fore.GREEN}✓ Introduction complete!{Style.RESET_ALL}")
            print(f"  └── Audio chunks: {intro_audio_chunks}, Total bytes: {intro_audio_bytes:,}")
            
            # Now test with audio input
            print_header("SENDING TEST AUDIO (Simulating User Speech)")
            
            # Generate test audio - a phrase pattern
            print(f"{Fore.YELLOW}Generating test audio...{Style.RESET_ALL}")
            
            # Create audio pattern that might trigger STT
            test_audio = generate_test_audio(duration_seconds=1.5, frequency=200)
            silence = generate_silence(duration_seconds=0.8)  # Pause to trigger end-of-turn
            
            # Send audio in chunks (simulating real-time streaming)
            chunk_size = 3200  # 100ms of audio at 16kHz, 16-bit
            metrics.reset()
            metrics.audio_send_start = time.time()
            
            print(f"\n{Fore.CYAN}Sending {len(test_audio)} bytes of test audio in {len(test_audio)//chunk_size} chunks...{Style.RESET_ALL}")
            
            for i in range(0, len(test_audio), chunk_size):
                chunk = test_audio[i:i+chunk_size]
                await ws.send(chunk)
                await asyncio.sleep(0.02)  # 20ms between chunks (real-time pacing)
            
            # Send silence to trigger end-of-turn
            print(f"{Fore.CYAN}Sending silence to trigger end-of-turn...{Style.RESET_ALL}")
            for i in range(0, len(silence), chunk_size):
                chunk = silence[i:i+chunk_size]
                await ws.send(chunk)
                await asyncio.sleep(0.02)
            
            audio_send_end = time.time()
            print(f"{Fore.GREEN}✓ Audio sent in {(audio_send_end - metrics.audio_send_start)*1000:.0f}ms{Style.RESET_ALL}")
            
            print_header("LISTENING FOR PIPELINE RESPONSE")
            
            # Listen for response
            response_timeout = 30.0
            response_start = time.time()
            turn_complete_received = False
            
            while not turn_complete_received and (time.time() - response_start) < response_timeout:
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    timestamp = time.time()
                    
                    if isinstance(message, bytes):
                        # TTS audio
                        metrics.audio_chunks_received += 1
                        metrics.audio_bytes_received += len(message)
                        
                        if metrics.first_audio_time is None:
                            metrics.first_audio_time = timestamp
                        
                        if metrics.audio_chunks_received <= 3 or metrics.audio_chunks_received % 5 == 0:
                            print_event("audio", {
                                "bytes": len(message),
                                "chunk_num": metrics.audio_chunks_received,
                                "total_bytes": metrics.audio_bytes_received
                            }, timestamp)
                    else:
                        data = json.loads(message)
                        msg_type = data.get("type", "unknown")
                        print_event(msg_type, data, timestamp)
                        
                        if msg_type == "transcript":
                            if metrics.first_transcript_time is None:
                                metrics.first_transcript_time = timestamp
                            if data.get("is_final"):
                                metrics.final_transcript_time = timestamp
                        
                        elif msg_type == "llm_response":
                            metrics.llm_response_time = timestamp
                        
                        elif msg_type == "turn_complete":
                            metrics.turn_complete_time = timestamp
                            turn_complete_received = True
                        
                        elif msg_type == "heartbeat":
                            pass  # Ignore heartbeats in output
                
                except asyncio.TimeoutError:
                    print(f"{Fore.YELLOW}...waiting for response (elapsed: {time.time() - response_start:.1f}s){Style.RESET_ALL}")
            
            # Print latency summary
            print_header("LATENCY SUMMARY")
            
            if metrics.get_stt_latency():
                print(f"  {Fore.YELLOW}📝 STT First Response:{Style.RESET_ALL}  {metrics.get_stt_latency():.0f}ms")
            else:
                print(f"  {Fore.RED}📝 STT First Response:{Style.RESET_ALL}  No transcript received")
            
            if metrics.get_llm_latency():
                print(f"  {Fore.MAGENTA}🤖 LLM Response:{Style.RESET_ALL}        {metrics.get_llm_latency():.0f}ms")
            else:
                print(f"  {Fore.RED}🤖 LLM Response:{Style.RESET_ALL}        No LLM response received")
            
            if metrics.get_tts_latency():
                print(f"  {Fore.BLUE}🔊 TTS First Audio:{Style.RESET_ALL}     {metrics.get_tts_latency():.0f}ms")
            else:
                print(f"  {Fore.RED}🔊 TTS First Audio:{Style.RESET_ALL}     No audio received")
            
            print(f"  {Fore.WHITE}{'─'*40}{Style.RESET_ALL}")
            
            if metrics.get_total_latency():
                print(f"  {Fore.CYAN}⏱️  TOTAL End-to-End:{Style.RESET_ALL}   {metrics.get_total_latency():.0f}ms")
            
            print(f"\n  {Fore.WHITE}Audio Stats:")
            print(f"    • Chunks received: {metrics.audio_chunks_received}")
            print(f"    • Total audio: {metrics.audio_bytes_received:,} bytes")
            if metrics.audio_chunks_received > 0:
                print(f"    • Avg chunk size: {metrics.audio_bytes_received // metrics.audio_chunks_received:,} bytes")
            
            # Send end call
            await ws.send(json.dumps({"type": "end_call"}))
            print(f"\n{Fore.GREEN}✓ Test complete, connection closed{Style.RESET_ALL}")
            
    except websockets.exceptions.ConnectionClosedError as e:
        print(f"{Fore.RED}WebSocket connection closed: {e}{Style.RESET_ALL}")
    except Exception as e:
        print(f"{Fore.RED}Error: {e}{Style.RESET_ALL}")
        import traceback
        traceback.print_exc()


async def test_pipeline_with_real_text():
    """
    Alternative test: Send a text message and measure TTS-only latency.
    This bypasses STT to isolate TTS performance issues.
    """
    print_header("TTS-ONLY LATENCY TEST (via LLM response)")
    print(f"{Fore.YELLOW}This test measures LLM + TTS latency only (no STT){Style.RESET_ALL}")
    
    # This would require a different endpoint that accepts text input
    # For now, direct users to check server logs
    print(f"\n{Fore.CYAN}To isolate TTS latency, check server logs for:{Style.RESET_ALL}")
    print(f"  • 'llm_response' log entries (LLM latency)")
    print(f"  • 'tts_start' and 'tts_complete' log entries (TTS latency)")
    print(f"  • 'turn_complete' log entries (total turn latency)")


async def benchmark_multiple_turns(voice_id: str = "sophia", num_turns: int = 3):
    """
    Run multiple turns and collect average latencies.
    """
    print_header(f"BENCHMARK: {num_turns} TURNS WITH {voice_id.upper()}")
    
    # This is a placeholder - would need actual audio samples or TTS text input
    print(f"{Fore.YELLOW}Multi-turn benchmark not yet implemented.{Style.RESET_ALL}")
    print(f"Run the single turn test multiple times to gather averages.")


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Voice Pipeline Latency Diagnostic Tool")
    parser.add_argument("--voice", "-v", default="sophia", choices=["sophia", "emma", "alex"],
                       help="Voice agent to test (default: sophia)")
    parser.add_argument("--benchmark", "-b", type=int, default=0,
                       help="Number of turns for benchmark (0 = single test)")
    
    args = parser.parse_args()
    
    print(f"\n{Fore.CYAN}{'='*70}")
    print(f"{Fore.CYAN}  TALKY.AI VOICE PIPELINE DIAGNOSTIC TOOL")
    print(f"{Fore.CYAN}  Testing latency for STT → LLM → TTS pipeline")
    print(f"{Fore.CYAN}{'='*70}{Style.RESET_ALL}")
    
    if args.benchmark > 0:
        asyncio.run(benchmark_multiple_turns(args.voice, args.benchmark))
    else:
        asyncio.run(test_voice_pipeline(args.voice))


if __name__ == "__main__":
    main()
