"""
Integration Test for Google Cloud TTS with Chirp 3: HD Voices
Tests the GoogleTTSProvider implementation
"""
import asyncio
import os
import pytest
from dotenv import load_dotenv

load_dotenv()


class TestGoogleTTSProvider:
    """Integration tests for Google Cloud TTS with Chirp 3: HD voices."""
    
    @pytest.mark.asyncio
    async def test_google_tts_initialization(self):
        """Test Google TTS provider initializes correctly."""
        from app.infrastructure.tts.google_tts import GoogleTTSProvider
        
        api_key = os.getenv("GOOGLE_TTS_API_KEY")
        if not api_key:
            pytest.skip("GOOGLE_TTS_API_KEY not set")
        
        tts = GoogleTTSProvider()
        await tts.initialize({
            "api_key": api_key,
            "voice_id": "en-US-Chirp3-HD-Orus",
            "language_code": "en-US"
        })
        
        assert tts.name == "google"
        assert tts._api_key is not None
        
        await tts.cleanup()
    
    @pytest.mark.asyncio
    async def test_google_tts_synthesis_orus_voice(self):
        """Test speech synthesis with Orus voice (default)."""
        from app.infrastructure.tts.google_tts import GoogleTTSProvider
        
        api_key = os.getenv("GOOGLE_TTS_API_KEY")
        if not api_key:
            pytest.skip("GOOGLE_TTS_API_KEY not set")
        
        tts = GoogleTTSProvider()
        await tts.initialize({
            "api_key": api_key
        })
        
        # Generate audio using Orus voice
        text = "Hello, this is a test of Google Cloud TTS with Chirp 3 HD Orus voice."
        audio_chunks = []
        
        async for chunk in tts.stream_synthesize(
            text=text,
            voice_id="Orus",
            sample_rate=24000
        ):
            audio_chunks.append(chunk.data)
        
        # Verify audio was generated
        assert len(audio_chunks) > 0
        total_bytes = sum(len(chunk) for chunk in audio_chunks)
        assert total_bytes > 0
        print(f"Generated {len(audio_chunks)} chunks, total {total_bytes} bytes")
        
        await tts.cleanup()
    
    @pytest.mark.asyncio
    async def test_google_tts_full_voice_format(self):
        """Test synthesis with full voice ID format."""
        from app.infrastructure.tts.google_tts import GoogleTTSProvider
        
        api_key = os.getenv("GOOGLE_TTS_API_KEY")
        if not api_key:
            pytest.skip("GOOGLE_TTS_API_KEY not set")
        
        tts = GoogleTTSProvider()
        await tts.initialize({"api_key": api_key})
        
        # Use full voice format
        audio_chunks = []
        async for chunk in tts.stream_synthesize(
            text="Testing full voice format.",
            voice_id="en-US-Chirp3-HD-Orus",
            sample_rate=24000
        ):
            audio_chunks.append(chunk.data)
        
        assert len(audio_chunks) > 0
        
        await tts.cleanup()
    
    @pytest.mark.asyncio
    async def test_google_tts_available_voices(self):
        """Test getting available Chirp 3: HD voices."""
        from app.infrastructure.tts.google_tts import GoogleTTSProvider
        
        api_key = os.getenv("GOOGLE_TTS_API_KEY")
        if not api_key:
            pytest.skip("GOOGLE_TTS_API_KEY not set")
        
        tts = GoogleTTSProvider()
        await tts.initialize({"api_key": api_key})
        
        voices = await tts.get_available_voices()
        
        assert len(voices) > 0
        
        # Find Orus voice
        orus_voice = next((v for v in voices if "Orus" in v["id"]), None)
        assert orus_voice is not None
        assert orus_voice["gender"] == "Male"
        
        await tts.cleanup()
    
    @pytest.mark.asyncio
    async def test_google_tts_speaking_rate(self):
        """Test synthesis with custom speaking rate."""
        from app.infrastructure.tts.google_tts import GoogleTTSProvider
        
        api_key = os.getenv("GOOGLE_TTS_API_KEY")
        if not api_key:
            pytest.skip("GOOGLE_TTS_API_KEY not set")
        
        tts = GoogleTTSProvider()
        await tts.initialize({"api_key": api_key})
        
        # Generate with faster speaking rate
        audio_chunks = []
        async for chunk in tts.stream_synthesize(
            text="This is spoken at a faster rate.",
            voice_id="Orus",
            sample_rate=24000,
            speaking_rate=1.5
        ):
            audio_chunks.append(chunk.data)
        
        assert len(audio_chunks) > 0
        
        await tts.cleanup()


class TestTTSFactoryWithGoogle:
    """Test TTSFactory with Google TTS provider."""
    
    def test_factory_lists_google_provider(self):
        """Test that factory lists Google TTS provider."""
        from app.infrastructure.tts.factory import TTSFactory
        
        providers = TTSFactory.list_providers()
        assert "google" in providers
    
    def test_factory_creates_google_provider(self):
        """Test that factory creates Google TTS provider."""
        from app.infrastructure.tts.factory import TTSFactory
        
        provider = TTSFactory.create("google", {})
        assert provider.name == "google"
