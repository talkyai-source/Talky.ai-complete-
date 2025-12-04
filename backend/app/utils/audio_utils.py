"""
Audio Utilities Module
Provides audio format validation and utility functions for VoIP audio processing
"""
from typing import Tuple, Optional
import struct
import logging

logger = logging.getLogger(__name__)


def validate_pcm_format(
    audio_data: bytes,
    expected_rate: int = 16000,
    expected_channels: int = 1,
    expected_bit_depth: int = 16
) -> Tuple[bool, Optional[str]]:
    """
    Validate PCM audio format based on chunk size and expected parameters.
    
    Since we receive raw PCM data without headers, we validate based on:
    - Chunk size should be divisible by (channels * bytes_per_sample)
    - Chunk size should represent reasonable duration (20-200ms)
    
    Args:
        audio_data: Raw PCM audio bytes
        expected_rate: Expected sample rate in Hz (default: 16000)
        expected_channels: Expected number of channels (default: 1 for mono)
        expected_bit_depth: Expected bit depth (default: 16)
        
    Returns:
        Tuple of (is_valid: bool, error_message: Optional[str])
    """
    if not audio_data:
        return False, "Audio data is empty"
    
    # Calculate bytes per sample
    bytes_per_sample = expected_bit_depth // 8
    frame_size = expected_channels * bytes_per_sample
    
    # Check if chunk size is valid (divisible by frame size)
    if len(audio_data) % frame_size != 0:
        return False, (
            f"Invalid chunk size: {len(audio_data)} bytes is not divisible by "
            f"frame size {frame_size} (channels={expected_channels}, "
            f"bit_depth={expected_bit_depth})"
        )
    
    # Calculate duration
    num_frames = len(audio_data) // frame_size
    duration_ms = (num_frames / expected_rate) * 1000
    
    # Validate duration is reasonable (20ms to 500ms for streaming)
    if duration_ms < 10:
        return False, f"Chunk too small: {duration_ms:.1f}ms (minimum 10ms)"
    
    if duration_ms > 1000:
        return False, f"Chunk too large: {duration_ms:.1f}ms (maximum 1000ms)"
    
    logger.debug(
        f"Audio validation passed: {len(audio_data)} bytes, "
        f"{duration_ms:.1f}ms @ {expected_rate}Hz"
    )
    
    return True, None


def calculate_audio_duration_ms(
    audio_data: bytes,
    sample_rate: int = 16000,
    channels: int = 1,
    bit_depth: int = 16
) -> float:
    """
    Calculate the duration of PCM audio data in milliseconds.
    
    Args:
        audio_data: Raw PCM audio bytes
        sample_rate: Sample rate in Hz
        channels: Number of audio channels
        bit_depth: Bit depth (8, 16, 24, or 32)
        
    Returns:
        Duration in milliseconds
    """
    if not audio_data:
        return 0.0
    
    bytes_per_sample = bit_depth // 8
    frame_size = channels * bytes_per_sample
    num_frames = len(audio_data) // frame_size
    
    duration_seconds = num_frames / sample_rate
    return duration_seconds * 1000


def calculate_expected_chunk_size(
    duration_ms: float,
    sample_rate: int = 16000,
    channels: int = 1,
    bit_depth: int = 16
) -> int:
    """
    Calculate expected chunk size in bytes for a given duration.
    
    Useful for generating test audio or validating chunk sizes.
    
    Args:
        duration_ms: Desired duration in milliseconds
        sample_rate: Sample rate in Hz
        channels: Number of audio channels
        bit_depth: Bit depth (8, 16, 24, or 32)
        
    Returns:
        Expected chunk size in bytes
    """
    bytes_per_sample = bit_depth // 8
    frame_size = channels * bytes_per_sample
    
    duration_seconds = duration_ms / 1000
    num_frames = int(sample_rate * duration_seconds)
    
    return num_frames * frame_size


def generate_silence(
    duration_ms: float,
    sample_rate: int = 16000,
    channels: int = 1,
    bit_depth: int = 16
) -> bytes:
    """
    Generate silent PCM audio (all zeros).
    
    Useful for testing and padding.
    
    Args:
        duration_ms: Duration in milliseconds
        sample_rate: Sample rate in Hz
        channels: Number of audio channels
        bit_depth: Bit depth (8, 16, 24, or 32)
        
    Returns:
        Raw PCM audio bytes (silence)
    """
    chunk_size = calculate_expected_chunk_size(
        duration_ms, sample_rate, channels, bit_depth
    )
    return bytes(chunk_size)


def generate_sine_wave(
    frequency: float,
    duration_ms: float,
    sample_rate: int = 16000,
    channels: int = 1,
    amplitude: float = 0.5
) -> bytes:
    """
    Generate a sine wave PCM audio for testing.
    
    Args:
        frequency: Frequency in Hz (e.g., 440 for A4)
        duration_ms: Duration in milliseconds
        sample_rate: Sample rate in Hz
        channels: Number of audio channels
        amplitude: Amplitude (0.0 to 1.0)
        
    Returns:
        Raw PCM audio bytes (16-bit signed)
    """
    import math
    
    duration_seconds = duration_ms / 1000
    num_samples = int(sample_rate * duration_seconds)
    
    # Generate sine wave samples
    samples = []
    for i in range(num_samples):
        t = i / sample_rate
        value = amplitude * math.sin(2 * math.pi * frequency * t)
        # Convert to 16-bit signed integer
        sample_value = int(value * 32767)
        # Clamp to valid range
        sample_value = max(-32768, min(32767, sample_value))
        samples.append(sample_value)
    
    # Pack as 16-bit signed integers (little-endian)
    audio_data = struct.pack(f'<{len(samples)}h', *samples)
    
    # Duplicate for stereo if needed
    if channels == 2:
        stereo_samples = []
        for sample in samples:
            stereo_samples.extend([sample, sample])
        audio_data = struct.pack(f'<{len(stereo_samples)}h', *stereo_samples)
    
    return audio_data


def resample_audio(
    audio_data: bytes,
    from_rate: int,
    to_rate: int,
    channels: int = 1
) -> bytes:
    """
    Resample PCM audio from one sample rate to another.
    
    NOTE: This is a placeholder for future implementation.
    Currently not needed as Vonage sends 16kHz audio.
    
    Args:
        audio_data: Raw PCM audio bytes (16-bit)
        from_rate: Source sample rate in Hz
        to_rate: Target sample rate in Hz
        channels: Number of audio channels
        
    Returns:
        Resampled PCM audio bytes
        
    Raises:
        NotImplementedError: Resampling not yet implemented
    """
    if from_rate == to_rate:
        return audio_data
    
    raise NotImplementedError(
        "Audio resampling not yet implemented. "
        "Add scipy or librosa dependency when needed."
    )
