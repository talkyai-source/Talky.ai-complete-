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
    channels: int = 1,
    bit_depth: int = 16
) -> bytes:
    """
    Resample PCM audio from one sample rate to another using librosa+soxr.
    
    Uses high-quality band-limited sinc interpolation (soxr_hq).
    
    Args:
        audio_data: Raw PCM audio bytes (16-bit or 32-bit float)
        from_rate: Source sample rate in Hz
        to_rate: Target sample rate in Hz
        channels: Number of audio channels
        bit_depth: Bit depth (16 for int16, 32 for float32)
        
    Returns:
        Resampled PCM audio bytes (same format as input)
    """
    if from_rate == to_rate:
        return audio_data
    
    import numpy as np
    
    try:
        import librosa
    except ImportError:
        raise ImportError("librosa is required for resampling. Install with: pip install librosa soxr")
    
    # Convert bytes to numpy array
    if bit_depth == 16:
        audio_array = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
    elif bit_depth == 32:
        audio_array = np.frombuffer(audio_data, dtype=np.float32)
    else:
        raise ValueError(f"Unsupported bit depth: {bit_depth}")
    
    # Resample using librosa with soxr backend (high quality)
    resampled = librosa.resample(
        audio_array,
        orig_sr=from_rate,
        target_sr=to_rate,
        res_type='soxr_hq'
    )
    
    # Convert back to original format
    if bit_depth == 16:
        resampled_int = np.clip(resampled * 32768.0, -32768, 32767).astype(np.int16)
        return resampled_int.tobytes()
    else:
        return resampled.astype(np.float32).tobytes()


def pcm_float32_to_int16(pcm_f32: bytes) -> bytes:
    """
    Convert 32-bit float PCM to 16-bit integer PCM.
    
    Cartesia outputs pcm_f32le, VoIP typically needs 16-bit PCM.
    
    Args:
        pcm_f32: Raw PCM audio bytes in 32-bit float format
        
    Returns:
        Raw PCM audio bytes in 16-bit signed integer format
    """
    import numpy as np
    
    # Convert bytes to float32 array
    audio_f32 = np.frombuffer(pcm_f32, dtype=np.float32)
    
    # Scale and convert to int16
    # Clip to prevent overflow
    audio_int16 = np.clip(audio_f32 * 32768.0, -32768, 32767).astype(np.int16)
    
    return audio_int16.tobytes()


def pcm_int16_to_float32(pcm_int16: bytes) -> bytes:
    """
    Convert 16-bit integer PCM to 32-bit float PCM.
    
    Args:
        pcm_int16: Raw PCM audio bytes in 16-bit signed integer format
        
    Returns:
        Raw PCM audio bytes in 32-bit float format
    """
    import numpy as np
    
    audio_int16 = np.frombuffer(pcm_int16, dtype=np.int16)
    audio_f32 = audio_int16.astype(np.float32) / 32768.0
    
    return audio_f32.tobytes()


# G.711 mu-law encoding table constants
ULAW_BIAS = 0x84
ULAW_CLIP = 32635

def pcm_to_ulaw(pcm_data: bytes) -> bytes:
    """
    Convert 16-bit linear PCM to G.711 mu-law encoding.
    
    ITU-T G.711 mu-law is used in North America and Japan.
    Compresses 16-bit PCM to 8-bit mu-law (2:1 compression).
    
    Args:
        pcm_data: Raw PCM audio bytes (16-bit signed, little-endian)
        
    Returns:
        G.711 mu-law encoded audio bytes (8-bit)
    """
    import numpy as np
    
    # Convert to numpy array of int16
    samples = np.frombuffer(pcm_data, dtype=np.int16)
    
    # Encode each sample
    encoded = np.zeros(len(samples), dtype=np.uint8)
    
    for i, sample in enumerate(samples):
        encoded[i] = _linear_to_ulaw(sample)
    
    return encoded.tobytes()


def _linear_to_ulaw(sample: int) -> int:
    """Convert a single 16-bit linear sample to 8-bit mu-law."""
    # Get sign bit
    sign = (sample >> 8) & 0x80
    if sign:
        sample = -sample
    
    # Clip to valid range
    if sample > ULAW_CLIP:
        sample = ULAW_CLIP
    
    # Add bias for mu-law
    sample = sample + ULAW_BIAS
    
    # Find exponent and mantissa
    exponent = 7
    exp_mask = 0x4000
    
    for _ in range(7):
        if sample & exp_mask:
            break
        exponent -= 1
        exp_mask >>= 1
    
    # Extract mantissa
    mantissa = (sample >> (exponent + 3)) & 0x0F
    
    # Combine sign, exponent, and mantissa, then invert
    ulaw_byte = ~(sign | (exponent << 4) | mantissa) & 0xFF
    
    return ulaw_byte


def ulaw_to_pcm(ulaw_data: bytes) -> bytes:
    """
    Convert G.711 mu-law to 16-bit linear PCM.
    
    Args:
        ulaw_data: G.711 mu-law encoded audio bytes (8-bit)
        
    Returns:
        Raw PCM audio bytes (16-bit signed, little-endian)
    """
    import numpy as np
    
    ulaw_samples = np.frombuffer(ulaw_data, dtype=np.uint8)
    pcm_samples = np.zeros(len(ulaw_samples), dtype=np.int16)
    
    for i, ulaw_byte in enumerate(ulaw_samples):
        pcm_samples[i] = _ulaw_to_linear(ulaw_byte)
    
    return pcm_samples.tobytes()


def _ulaw_to_linear(ulaw_byte: int) -> int:
    """Convert a single 8-bit mu-law sample to 16-bit linear."""
    # Invert bits
    ulaw_byte = ~ulaw_byte & 0xFF
    
    # Extract components
    sign = ulaw_byte & 0x80
    exponent = (ulaw_byte >> 4) & 0x07
    mantissa = ulaw_byte & 0x0F
    
    # Compute linear value
    sample = ((mantissa << 3) + ULAW_BIAS) << exponent
    sample -= ULAW_BIAS
    
    if sign:
        sample = -sample
    
    return sample


# G.711 A-law encoding constants
ALAW_CLIP = 32635

def pcm_to_alaw(pcm_data: bytes) -> bytes:
    """
    Convert 16-bit linear PCM to G.711 A-law encoding.
    
    ITU-T G.711 A-law is used in Europe and most of the world.
    Compresses 16-bit PCM to 8-bit A-law (2:1 compression).
    
    Args:
        pcm_data: Raw PCM audio bytes (16-bit signed, little-endian)
        
    Returns:
        G.711 A-law encoded audio bytes (8-bit)
    """
    import numpy as np
    
    samples = np.frombuffer(pcm_data, dtype=np.int16)
    encoded = np.zeros(len(samples), dtype=np.uint8)
    
    for i, sample in enumerate(samples):
        encoded[i] = _linear_to_alaw(sample)
    
    return encoded.tobytes()


def _linear_to_alaw(sample: int) -> int:
    """Convert a single 16-bit linear sample to 8-bit A-law."""
    # Get sign bit
    sign = 0
    if sample < 0:
        sign = 0x80
        sample = -sample
    
    # Clip to valid range
    if sample > ALAW_CLIP:
        sample = ALAW_CLIP
    
    # Find segment and compute mantissa
    if sample >= 256:
        exponent = 7
        exp_mask = 0x4000
        
        for _ in range(7):
            if sample & exp_mask:
                break
            exponent -= 1
            exp_mask >>= 1
        
        mantissa = (sample >> (exponent + 3)) & 0x0F
    else:
        exponent = 0
        mantissa = sample >> 4
    
    # Combine and XOR with 0x55 for better idle channel noise
    alaw_byte = (sign | (exponent << 4) | mantissa) ^ 0x55
    
    return alaw_byte


def alaw_to_pcm(alaw_data: bytes) -> bytes:
    """
    Convert G.711 A-law to 16-bit linear PCM.
    
    Args:
        alaw_data: G.711 A-law encoded audio bytes (8-bit)
        
    Returns:
        Raw PCM audio bytes (16-bit signed, little-endian)
    """
    import numpy as np
    
    alaw_samples = np.frombuffer(alaw_data, dtype=np.uint8)
    pcm_samples = np.zeros(len(alaw_samples), dtype=np.int16)
    
    for i, alaw_byte in enumerate(alaw_samples):
        pcm_samples[i] = _alaw_to_linear(alaw_byte)
    
    return pcm_samples.tobytes()


def _alaw_to_linear(alaw_byte: int) -> int:
    """Convert a single 8-bit A-law sample to 16-bit linear."""
    # XOR to undo encoding
    alaw_byte ^= 0x55
    
    # Extract components
    sign = alaw_byte & 0x80
    exponent = (alaw_byte >> 4) & 0x07
    mantissa = alaw_byte & 0x0F
    
    # Compute linear value
    if exponent == 0:
        sample = (mantissa << 4) + 8
    else:
        sample = ((mantissa << 4) + 0x108) << (exponent - 1)
    
    if sign:
        sample = -sample
    
    return sample


def convert_for_rtp(
    audio_data: bytes,
    source_rate: int,
    source_format: str = "pcm_f32le",
    codec: str = "ulaw"
) -> bytes:
    """
    Convert audio to RTP-ready format (G.711 at 8000Hz).
    
    Full pipeline: Format conversion → Resample → Encode
    
    Args:
        audio_data: Input audio bytes
        source_rate: Source sample rate in Hz
        source_format: Source format ("pcm_f32le", "pcm_s16le")
        codec: Target codec ("ulaw" or "alaw")
        
    Returns:
        G.711 encoded audio at 8000Hz
    """
    # Step 1: Convert to 16-bit PCM if needed
    if source_format == "pcm_f32le":
        pcm_16 = pcm_float32_to_int16(audio_data)
        bit_depth = 32  # for resampling input
    else:
        pcm_16 = audio_data
        bit_depth = 16
    
    # Step 2: Resample to 8000Hz (G.711 standard)
    if source_rate != 8000:
        # Convert to float for resampling
        pcm_resampled = resample_audio(
            pcm_16,
            from_rate=source_rate,
            to_rate=8000,
            bit_depth=16
        )
    else:
        pcm_resampled = pcm_16
    
    # Step 3: Encode to G.711
    if codec == "ulaw":
        return pcm_to_ulaw(pcm_resampled)
    elif codec == "alaw":
        return pcm_to_alaw(pcm_resampled)
    else:
        raise ValueError(f"Unknown codec: {codec}. Use 'ulaw' or 'alaw'")

