"""
Test Deepgram SDK v4.8.1 WebSocket connection
"""
import os
from dotenv import load_dotenv
from deepgram import DeepgramClient, LiveOptions, LiveTranscriptionEvents

load_dotenv()

def main():
    api_key = os.getenv("DEEPGRAM_API_KEY")
    
    if not api_key:
        print("❌ DEEPGRAM_API_KEY not found")
        return
    
    print("✓ API key found")
    print("Creating Deepgram client...")
    
    # Create client
    deepgram = DeepgramClient(api_key)
    
    print("Creating WebSocket connection...")
    
    # Create websocket connection
    connection = deepgram.listen.websocket.v("1")
    
    print("Setting up event handlers...")
    
    # Handle transcription events
    def handle_transcript(result):
        print(f"Transcript: {result}")
    
    def handle_error(error):
        print(f"Error: {error}")
    
    # Register handlers (not using decorator)
    connection.on(LiveTranscriptionEvents.Transcript, handle_transcript)
    connection.on(LiveTranscriptionEvents.Error, handle_error)
    
    print("Starting connection...")
    
    # Start connection with streaming options
    connection.start(LiveOptions(model="nova-3", language="en-US"))
    
    print("✓ Connection started successfully!")
    
    # Close when done
    connection.finish()
    
    print("✓ Test complete")

if __name__ == "__main__":
    main()
