"""
Real-time Flux Voice Agent with Barge-In
Based on working Deepgram SDK v5 example
Integrates: Flux STT + Groq LLM + Cartesia TTS
"""
import os
import json
import threading
import pyaudio
from dotenv import load_dotenv

load_dotenv()

# Audio settings
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_SIZE = 2560  # ~80ms at 16kHz

# State
is_agent_speaking = False
should_interrupt = False
pending_transcript = None


def play_audio(audio_data, sample_rate=22050):
    """Play audio through speakers"""
    global is_agent_speaking, should_interrupt
    is_agent_speaking = True
    
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paFloat32, channels=1, rate=sample_rate, output=True)
    
    chunk_size = 4096
    for i in range(0, len(audio_data), chunk_size):
        if should_interrupt:
            print("🛑 Interrupted!")
            break
        stream.write(audio_data[i:i + chunk_size])
    
    stream.stop_stream()
    stream.close()
    p.terminate()
    is_agent_speaking = False


def get_groq_response(transcript):
    """Get response from Groq LLM using streaming"""
    from app.infrastructure.llm.groq import GroqLLMProvider
    from app.domain.models.conversation import Message, MessageRole
    import asyncio
    
    llm = GroqLLMProvider()
    
    # Initialize synchronously
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    loop.run_until_complete(llm.initialize({
        "api_key": os.getenv("GROQ_API_KEY"),
        "model": "llama-3.1-8b-instant",
        "temperature": 0.7,
        "max_tokens": 150
    }))
    
    messages = [Message(role=MessageRole.USER, content=transcript)]
    system_prompt = "You are a helpful voice assistant. Keep responses brief and conversational, under 2 sentences."
    
    # Stream response
    response_text = ""
    
    async def stream_response():
        nonlocal response_text
        async for token in llm.stream_chat(messages, system_prompt):
            response_text += token
    
    loop.run_until_complete(stream_response())
    loop.close()
    
    return response_text


def generate_tts(text):
    """Generate TTS audio from text using Cartesia"""
    from app.infrastructure.tts.cartesia import CartesiaTTSProvider
    import asyncio
    
    tts = CartesiaTTSProvider()
    
    # Initialize
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    loop.run_until_complete(tts.initialize({
        "api_key": os.getenv("CARTESIA_API_KEY"),
        "model_id": "sonic-3",
        "voice_id": "6ccbfb76-1fc6-48f7-b71d-91ac6298247b",
        "sample_rate": 22050
    }))
    
    # Stream synthesis
    audio_chunks = []
    
    async def synthesize():
        async for chunk in tts.stream_synthesize(
            text=text,
            voice_id="6ccbfb76-1fc6-48f7-b71d-91ac6298247b",
            sample_rate=22050
        ):
            audio_chunks.append(chunk.data)
    
    loop.run_until_complete(synthesize())
    loop.close()
    
    return b''.join(audio_chunks)


def process_response(transcript):
    """Process transcript: get LLM response and speak it"""
    global should_interrupt
    
    if should_interrupt:
        return
    
    print("🤖 Thinking...")
    response = get_groq_response(transcript)
    
    if response and not should_interrupt:
        print(f"💬 Agent: '{response}'")
        print("🔊 Speaking...")
        tts_audio = generate_tts(response)
        
        if tts_audio and not should_interrupt:
            play_audio(tts_audio, sample_rate=22050)
    
    print("\n🎤 Listening...")


def main():
    global is_agent_speaking, should_interrupt, pending_transcript
    
    print("\n" + "="*70)
    print("  🎙️  REAL-TIME FLUX VOICE AGENT")
    print("  With Barge-In Support")
    print("="*70)
    print("\nSpeak into your microphone. Press Ctrl+C to quit.")
    print("You can interrupt the agent anytime by speaking!\n")
    
    from deepgram import DeepgramClient
    from deepgram.core.events import EventType
    from deepgram.extensions.types.sockets import ListenV2SocketClientResponse
    
    # Initialize Deepgram client (API key from env)
    client = DeepgramClient()
    
    # Setup microphone
    p = pyaudio.PyAudio()
    mic_stream = p.open(
        format=pyaudio.paInt16,
        channels=CHANNELS,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=CHUNK_SIZE
    )
    
    print("🎤 Listening...")
    
    def on_flux_message(message: ListenV2SocketClientResponse) -> None:
        global should_interrupt, is_agent_speaking, pending_transcript
        
        if hasattr(message, 'type'):
            if message.type == 'TurnInfo':
                event = getattr(message, 'event', None)
                
                if event == 'StartOfTurn':
                    if is_agent_speaking:
                        should_interrupt = True
                        print("\n🗣️ Interrupting agent...")
                
                elif event == 'EndOfTurn':
                    if hasattr(message, 'transcript') and message.transcript:
                        transcript = message.transcript.strip()
                        if transcript:
                            print(f"\n📝 You said: '{transcript}'")
                            pending_transcript = transcript
            
            elif message.type == 'Results':
                if hasattr(message, 'channel'):
                    alt = message.channel.alternatives[0] if message.channel.alternatives else None
                    if alt and alt.transcript:
                        print(f"\r🎤 {alt.transcript}        ", end="", flush=True)
    
    # Connect to Flux
    with client.listen.v2.connect(
        model="flux-general-en",
        encoding="linear16",
        sample_rate=SAMPLE_RATE
    ) as connection:
        connection.on(EventType.MESSAGE, on_flux_message)
        threading.Thread(target=connection.start_listening, daemon=True).start()
        
        stop_capture = threading.Event()
        
        def capture_audio():
            while not stop_capture.is_set():
                try:
                    audio_chunk = mic_stream.read(CHUNK_SIZE, exception_on_overflow=False)
                    connection.send_media(audio_chunk)
                except:
                    break
        
        capture_thread = threading.Thread(target=capture_audio, daemon=True)
        capture_thread.start()
        
        try:
            while True:
                # Check for pending transcript to process
                if pending_transcript:
                    transcript = pending_transcript
                    pending_transcript = None
                    should_interrupt = False
                    
                    # Process in separate thread so mic keeps working
                    response_thread = threading.Thread(
                        target=process_response, 
                        args=(transcript,)
                    )
                    response_thread.start()
                
                threading.Event().wait(0.1)  # Small sleep
                
        except KeyboardInterrupt:
            print("\n\n👋 Goodbye!")
        finally:
            stop_capture.set()
            mic_stream.stop_stream()
            mic_stream.close()
            p.terminate()


if __name__ == "__main__":
    main()
