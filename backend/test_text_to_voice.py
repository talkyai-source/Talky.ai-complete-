"""
Text-to-Voice Test
Type your message → Groq LLM → Cartesia TTS (speaks response)
Simplified test to verify the pipeline works!
"""
import asyncio
import os
from dotenv import load_dotenv
from groq import AsyncGroq
from cartesia import AsyncCartesia

try:
    import pyaudio
    AUDIO_AVAILABLE = True
except ImportError:
    AUDIO_AVAILABLE = False
    print("ERROR: pip install pyaudio")
    exit(1)

load_dotenv()


async def chat_and_speak(user_text, conversation_history):
    """Generate LLM response and speak it"""
    print(f"\n👤 You: {user_text}")
    
    # Add to history
    conversation_history.append({"role": "user", "content": user_text})
    
    # Generate LLM response
    print("🤖 AI thinking...", end=" ", flush=True)
    
    groq = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
    
    messages = [
        {"role": "system", "content": "You are Talky, a helpful voice assistant. Keep responses brief and conversational (2-3 sentences max)."}
    ] + conversation_history
    
    stream = await groq.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=messages,
        temperature=0.7,
        max_tokens=100,
        stream=True
    )
    
    ai_response = ""
    print("\n🤖 AI: ", end="", flush=True)
    
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            token = chunk.choices[0].delta.content
            ai_response += token
            print(token, end="", flush=True)
    
    print("\n")
    
    conversation_history.append({"role": "assistant", "content": ai_response})
    
    # Speak the response
    print("🔊 AI speaking...")
    
    cartesia = AsyncCartesia(api_key=os.getenv("CARTESIA_API_KEY"))
    
    p = pyaudio.PyAudio()
    speaker = p.open(
        format=pyaudio.paFloat32,
        channels=1,
        rate=22050,
        output=True
    )
    
    try:
        bytes_iter = cartesia.tts.bytes(
            model_id="sonic-3",
            transcript=ai_response,
            voice={"mode": "id", "id": "6ccbfb76-1fc6-48f7-b71d-91ac6298247b"},
            language="en",
            output_format={
                "container": "raw",
                "sample_rate": 22050,
                "encoding": "pcm_f32le"
            }
        )
        
        async for chunk in bytes_iter:
            speaker.write(chunk)
        
        print("✓ AI finished speaking\n")
    
    finally:
        speaker.close()
        p.terminate()
    
    return conversation_history


async def main():
    """Main conversation loop"""
    print("\n" + "="*70)
    print("  🎙️  TALKY.AI - TEXT-TO-VOICE TEST")
    print("  Type your message → AI responds with voice!")
    print("="*70)
    print("\n✨ Type your message and press ENTER")
    print("🔊 AI will respond with voice")
    print("🛑 Type 'quit' to exit\n")
    print("="*70 + "\n")
    
    conversation_history = []
    
    try:
        while True:
            # Get user input
            user_input = input("👤 You: ").strip()
            
            if not user_input:
                continue
            
            if user_input.lower() in ['quit', 'exit', 'bye']:
                print("\n👋 Goodbye!")
                break
            
            # Process and respond
            conversation_history = await chat_and_speak(user_input, conversation_history)
            
            # Keep only last 10 messages
            if len(conversation_history) > 10:
                conversation_history = conversation_history[-10:]
    
    except KeyboardInterrupt:
        print("\n\n🛑 Interrupted")
    
    print("\n✓ Session ended")


if __name__ == "__main__":
    # Check API keys
    for key in ["GROQ_API_KEY", "CARTESIA_API_KEY"]:
        if not os.getenv(key):
            print(f"❌ Missing {key}")
            exit(1)
    
    asyncio.run(main())
