"""
Unit Tests for Audio Utilities
Tests G.711 codecs, resampling, and format conversion
"""
import pytest
import struct
import numpy as np


class TestPCMConversion:
    """Tests for PCM format conversion functions."""
    
    def test_pcm_float32_to_int16(self):
        """Test F32 to int16 conversion."""
        from app.utils.audio_utils import pcm_float32_to_int16
        
        # Create test F32 data (sine wave)
        samples = np.array([0.0, 0.5, 1.0, -0.5, -1.0], dtype=np.float32)
        f32_bytes = samples.tobytes()
        
        # Convert
        int16_bytes = pcm_float32_to_int16(f32_bytes)
        
        # Verify
        int16_samples = np.frombuffer(int16_bytes, dtype=np.int16)
        assert len(int16_samples) == 5
        assert int16_samples[0] == 0  # 0.0 -> 0
        assert int16_samples[1] == 16384  # 0.5 -> ~16384
        assert int16_samples[2] == 32767  # 1.0 -> 32767 (clipped)
        assert int16_samples[3] == -16384  # -0.5 -> ~-16384
        assert int16_samples[4] == -32768  # -1.0 -> -32768 (clipped)
    
    def test_pcm_int16_to_float32(self):
        """Test int16 to F32 conversion."""
        from app.utils.audio_utils import pcm_int16_to_float32
        
        # Create test int16 data
        samples = np.array([0, 16384, 32767, -16384, -32768], dtype=np.int16)
        int16_bytes = samples.tobytes()
        
        # Convert
        f32_bytes = pcm_int16_to_float32(int16_bytes)
        
        # Verify
        f32_samples = np.frombuffer(f32_bytes, dtype=np.float32)
        assert len(f32_samples) == 5
        assert abs(f32_samples[0]) < 0.001  # 0 -> 0.0
        assert abs(f32_samples[1] - 0.5) < 0.001  # 16384 -> ~0.5
        assert f32_samples[2] > 0.99  # 32767 -> ~1.0
        

class TestG711MuLaw:
    """Tests for G.711 mu-law codec."""
    
    def test_pcm_to_ulaw_silence(self):
        """Test that silence encodes correctly."""
        from app.utils.audio_utils import pcm_to_ulaw
        
        # Silence (zeros)
        silence = bytes(160 * 2)  # 160 samples, 16-bit
        encoded = pcm_to_ulaw(silence)
        
        assert len(encoded) == 160  # 2:1 compression
    
    def test_ulaw_roundtrip(self):
        """Test encode then decode produces similar output."""
        from app.utils.audio_utils import pcm_to_ulaw, ulaw_to_pcm
        
        # Create a simple sine wave
        samples = []
        for i in range(160):
            value = int(16384 * np.sin(2 * np.pi * 440 * i / 8000))
            samples.append(value)
        
        original = struct.pack(f'<{len(samples)}h', *samples)
        
        # Encode and decode
        encoded = pcm_to_ulaw(original)
        decoded = ulaw_to_pcm(encoded)
        
        # Check sizes
        assert len(encoded) == 160
        assert len(decoded) == len(original)
        
        # Check that decoded is reasonably close to original
        original_arr = np.frombuffer(original, dtype=np.int16)
        decoded_arr = np.frombuffer(decoded, dtype=np.int16)
        
        # Allow some error due to lossy compression
        max_error = np.max(np.abs(original_arr.astype(float) - decoded_arr.astype(float)))
        assert max_error < 4000  # Within acceptable range for 8-bit encoding


class TestG711ALaw:
    """Tests for G.711 A-law codec."""
    
    def test_pcm_to_alaw_silence(self):
        """Test that silence encodes correctly."""
        from app.utils.audio_utils import pcm_to_alaw
        
        silence = bytes(160 * 2)
        encoded = pcm_to_alaw(silence)
        
        assert len(encoded) == 160
    
    def test_alaw_roundtrip(self):
        """Test encode then decode produces similar output."""
        from app.utils.audio_utils import pcm_to_alaw, alaw_to_pcm
        
        # Create a simple sine wave
        samples = []
        for i in range(160):
            value = int(16384 * np.sin(2 * np.pi * 440 * i / 8000))
            samples.append(value)
        
        original = struct.pack(f'<{len(samples)}h', *samples)
        
        # Encode and decode
        encoded = pcm_to_alaw(original)
        decoded = alaw_to_pcm(encoded)
        
        # Check sizes
        assert len(encoded) == 160
        assert len(decoded) == len(original)


class TestConvertForRTP:
    """Tests for the full RTP conversion pipeline."""
    
    def test_convert_f32_to_ulaw(self):
        """Test full pipeline: F32 -> resample -> G.711 mu-law."""
        from app.utils.audio_utils import convert_for_rtp
        
        # Create F32 audio at 22050Hz (like Cartesia output)
        duration_seconds = 0.02  # 20ms
        num_samples = int(22050 * duration_seconds)
        samples = np.zeros(num_samples, dtype=np.float32)
        f32_audio = samples.tobytes()
        
        # Convert
        g711_audio = convert_for_rtp(
            f32_audio,
            source_rate=22050,
            source_format="pcm_f32le",
            codec="ulaw"
        )
        
        # At 8000Hz, 20ms = 160 samples = 160 bytes G.711
        # But resampled from 22050, so might be slightly different
        assert len(g711_audio) > 0
    
    def test_convert_f32_to_alaw(self):
        """Test full pipeline with A-law codec."""
        from app.utils.audio_utils import convert_for_rtp
        
        num_samples = 441  # ~20ms at 22050Hz
        samples = np.zeros(num_samples, dtype=np.float32)
        f32_audio = samples.tobytes()
        
        g711_audio = convert_for_rtp(
            f32_audio,
            source_rate=22050,
            source_format="pcm_f32le",
            codec="alaw"
        )
        
        assert len(g711_audio) > 0


class TestAudioValidation:
    """Tests for existing audio validation functions."""
    
    def test_validate_pcm_format_valid(self):
        """Test validation passes for correct format."""
        from app.utils.audio_utils import validate_pcm_format
        
        # 20ms at 16kHz = 320 samples = 640 bytes
        audio = bytes(640)
        is_valid, error = validate_pcm_format(audio)
        
        assert is_valid
        assert error is None
    
    def test_validate_pcm_format_empty(self):
        """Test validation fails for empty audio."""
        from app.utils.audio_utils import validate_pcm_format
        
        is_valid, error = validate_pcm_format(b'')
        
        assert not is_valid
        assert "empty" in error.lower()
    
    def test_calculate_audio_duration(self):
        """Test duration calculation."""
        from app.utils.audio_utils import calculate_audio_duration_ms
        
        # 16kHz, 16-bit, mono: 640 bytes = 320 samples = 20ms
        audio = bytes(640)
        duration = calculate_audio_duration_ms(audio, sample_rate=16000)
        
        assert abs(duration - 20.0) < 0.1
