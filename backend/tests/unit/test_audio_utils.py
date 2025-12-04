"""
Unit Tests for Audio Utilities
Tests audio format validation, duration calculation, and audio generation
"""
import pytest
from app.utils.audio_utils import (
    validate_pcm_format,
    calculate_audio_duration_ms,
    calculate_expected_chunk_size,
    generate_silence,
    generate_sine_wave,
    resample_audio
)


class TestValidatePCMFormat:
    """Tests for PCM format validation"""
    
    def test_valid_80ms_chunk(self):
        """Test validation of valid 80ms audio chunk at 16kHz"""
        # 80ms at 16kHz, mono, 16-bit = 16000 * 0.08 * 1 * 2 = 2560 bytes
        audio_data = bytes(2560)
        is_valid, error = validate_pcm_format(audio_data, 16000, 1, 16)
        assert is_valid is True
        assert error is None
    
    def test_valid_20ms_chunk(self):
        """Test validation of valid 20ms audio chunk"""
        # 20ms at 16kHz, mono, 16-bit = 16000 * 0.02 * 1 * 2 = 640 bytes
        audio_data = bytes(640)
        is_valid, error = validate_pcm_format(audio_data, 16000, 1, 16)
        assert is_valid is True
        assert error is None
    
    def test_empty_audio(self):
        """Test validation fails for empty audio"""
        is_valid, error = validate_pcm_format(bytes(), 16000, 1, 16)
        assert is_valid is False
        assert "empty" in error.lower()
    
    def test_invalid_chunk_size(self):
        """Test validation fails for chunk not divisible by frame size"""
        # 641 bytes is not divisible by 2 (frame size for mono 16-bit)
        audio_data = bytes(641)
        is_valid, error = validate_pcm_format(audio_data, 16000, 1, 16)
        assert is_valid is False
        assert "not divisible" in error.lower()
    
    def test_chunk_too_small(self):
        """Test validation fails for very small chunks"""
        # 5ms chunk (too small)
        audio_data = bytes(160)  # 5ms at 16kHz
        is_valid, error = validate_pcm_format(audio_data, 16000, 1, 16)
        assert is_valid is False
        assert "too small" in error.lower()
    
    def test_chunk_too_large(self):
        """Test validation fails for very large chunks"""
        # 2000ms chunk (too large)
        audio_data = bytes(64000)  # 2 seconds
        is_valid, error = validate_pcm_format(audio_data, 16000, 1, 16)
        assert is_valid is False
        assert "too large" in error.lower()
    
    def test_stereo_audio(self):
        """Test validation of stereo audio"""
        # 80ms at 16kHz, stereo, 16-bit = 16000 * 0.08 * 2 * 2 = 5120 bytes
        audio_data = bytes(5120)
        is_valid, error = validate_pcm_format(audio_data, 16000, 2, 16)
        assert is_valid is True
        assert error is None


class TestCalculateAudioDuration:
    """Tests for audio duration calculation"""
    
    def test_80ms_duration(self):
        """Test duration calculation for 80ms chunk"""
        # 80ms at 16kHz, mono, 16-bit
        chunk_size = 2560
        audio_data = bytes(chunk_size)
        duration = calculate_audio_duration_ms(audio_data, 16000, 1, 16)
        assert abs(duration - 80.0) < 0.1  # Allow small floating point error
    
    def test_20ms_duration(self):
        """Test duration calculation for 20ms chunk"""
        # 20ms at 16kHz, mono, 16-bit
        chunk_size = 640
        audio_data = bytes(chunk_size)
        duration = calculate_audio_duration_ms(audio_data, 16000, 1, 16)
        assert abs(duration - 20.0) < 0.1
    
    def test_empty_audio_duration(self):
        """Test duration calculation for empty audio"""
        duration = calculate_audio_duration_ms(bytes(), 16000, 1, 16)
        assert duration == 0.0
    
    def test_stereo_duration(self):
        """Test duration calculation for stereo audio"""
        # 100ms at 16kHz, stereo, 16-bit
        chunk_size = 6400  # 16000 * 0.1 * 2 * 2
        audio_data = bytes(chunk_size)
        duration = calculate_audio_duration_ms(audio_data, 16000, 2, 16)
        assert abs(duration - 100.0) < 0.1


class TestCalculateExpectedChunkSize:
    """Tests for expected chunk size calculation"""
    
    def test_80ms_chunk_size(self):
        """Test chunk size calculation for 80ms"""
        chunk_size = calculate_expected_chunk_size(80, 16000, 1, 16)
        assert chunk_size == 2560  # 16000 * 0.08 * 1 * 2
    
    def test_20ms_chunk_size(self):
        """Test chunk size calculation for 20ms"""
        chunk_size = calculate_expected_chunk_size(20, 16000, 1, 16)
        assert chunk_size == 640  # 16000 * 0.02 * 1 * 2
    
    def test_stereo_chunk_size(self):
        """Test chunk size calculation for stereo"""
        chunk_size = calculate_expected_chunk_size(100, 16000, 2, 16)
        assert chunk_size == 6400  # 16000 * 0.1 * 2 * 2


class TestGenerateSilence:
    """Tests for silence generation"""
    
    def test_generate_80ms_silence(self):
        """Test generating 80ms of silence"""
        audio_data = generate_silence(80, 16000, 1, 16)
        assert len(audio_data) == 2560
        assert all(b == 0 for b in audio_data)
    
    def test_silence_validates(self):
        """Test generated silence passes validation"""
        audio_data = generate_silence(80, 16000, 1, 16)
        is_valid, error = validate_pcm_format(audio_data, 16000, 1, 16)
        assert is_valid is True
    
    def test_silence_duration(self):
        """Test silence has correct duration"""
        audio_data = generate_silence(100, 16000, 1, 16)
        duration = calculate_audio_duration_ms(audio_data, 16000, 1, 16)
        assert abs(duration - 100.0) < 0.1


class TestGenerateSineWave:
    """Tests for sine wave generation"""
    
    def test_generate_440hz_sine(self):
        """Test generating 440Hz sine wave (A4 note)"""
        audio_data = generate_sine_wave(440, 100, 16000, 1, 0.5)
        assert len(audio_data) > 0
        # Should be 100ms at 16kHz, mono, 16-bit
        expected_size = calculate_expected_chunk_size(100, 16000, 1, 16)
        assert len(audio_data) == expected_size
    
    def test_sine_wave_validates(self):
        """Test generated sine wave passes validation"""
        audio_data = generate_sine_wave(440, 80, 16000, 1, 0.5)
        is_valid, error = validate_pcm_format(audio_data, 16000, 1, 16)
        assert is_valid is True
    
    def test_sine_wave_not_silent(self):
        """Test sine wave is not all zeros"""
        audio_data = generate_sine_wave(440, 100, 16000, 1, 0.5)
        # Should have non-zero values
        assert not all(b == 0 for b in audio_data)
    
    def test_sine_wave_duration(self):
        """Test sine wave has correct duration"""
        audio_data = generate_sine_wave(440, 150, 16000, 1, 0.5)
        duration = calculate_audio_duration_ms(audio_data, 16000, 1, 16)
        assert abs(duration - 150.0) < 0.1


class TestResampleAudio:
    """Tests for audio resampling (placeholder)"""
    
    def test_same_rate_returns_original(self):
        """Test resampling with same rate returns original"""
        audio_data = bytes(1000)
        resampled = resample_audio(audio_data, 16000, 16000, 1)
        assert resampled == audio_data
    
    def test_different_rate_raises_not_implemented(self):
        """Test resampling with different rate raises NotImplementedError"""
        audio_data = bytes(1000)
        with pytest.raises(NotImplementedError):
            resample_audio(audio_data, 8000, 16000, 1)


class TestAudioChunkModel:
    """Tests for AudioChunk model validation"""
    
    def test_audio_chunk_creation(self):
        """Test creating AudioChunk with valid data"""
        from app.domain.models.conversation import AudioChunk
        
        audio_data = generate_silence(80, 16000, 1, 16)
        chunk = AudioChunk(
            data=audio_data,
            sample_rate=16000,
            channels=1
        )
        
        assert chunk.data == audio_data
        assert chunk.sample_rate == 16000
        assert chunk.channels == 1
    
    def test_audio_chunk_with_sine_wave(self):
        """Test AudioChunk with generated sine wave"""
        from app.domain.models.conversation import AudioChunk
        
        audio_data = generate_sine_wave(440, 100, 16000, 1, 0.5)
        chunk = AudioChunk(
            data=audio_data,
            sample_rate=16000,
            channels=1
        )
        
        # Validate the audio in the chunk
        is_valid, error = validate_pcm_format(chunk.data, 16000, 1, 16)
        assert is_valid is True


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v"])
