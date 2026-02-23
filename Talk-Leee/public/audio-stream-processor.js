/**
 * AudioStreamProcessor - AudioWorklet for gapless TTS streaming
 * 
 * This processor receives Int16 PCM audio from the backend via the main thread,
 * converts it to Float32 for Web Audio API, and maintains a jitter buffer for
 * smooth playback without gaps between chunks.
 * 
 * Features:
 * - Int16 to Float32 conversion
 * - Linear resampling when source/output sample rates differ
 * - Ring buffer with max size limit to prevent memory leaks
 * - 200ms pre-buffering to prevent underruns
 * - Zero-fill on buffer underrun (graceful degradation)
 * - Supports multiple sample rates (16000, 24000 for Deepgram TTS)
 * 
 * Sample Rate: Dynamic (matches AudioContext, typically 24000 for Deepgram TTS)
 * Bit Depth: 16-bit PCM input, 32-bit float output
 * Channels: Mono
 * 
 * Deepgram Best Practices:
 * - Keep buffer bounded to prevent memory growth
 * - Handle sample rate mismatches gracefully
 * - Reset on interruption for clean state
 * - Use 24000 Hz for best streaming TTS quality
 */

class AudioStreamProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    
    // Output sample rate is the AudioContext rate in the worklet global scope.
    // This is often 48000 on many devices, even if 24000 was requested.
    this.outputSampleRate = sampleRate;
    // Source TTS sample rate (set by main thread from backend "ready" payload).
    this.sourceSampleRate = 24000;
    
    // Ring buffer for incoming audio chunks (stored as Float32)
    // Start with 8 seconds and allow growth up to 30 seconds so
    // long responses (like package descriptions) don't get dropped.
    this.maxBufferSize = this.outputSampleRate * 8;
    this.maxHardBufferSize = this.outputSampleRate * 30;
    this.buffer = new Float32Array(this.maxBufferSize);
    this.bufferReadIndex = 0;
    this.bufferWriteIndex = 0;
    this.bufferFillCount = 0; // Number of samples in buffer
    
    // Pre-buffer size: 200ms at output sample rate
    // This prevents underruns during network jitter
    this.targetPreBufferSize = Math.floor(this.outputSampleRate * 0.2);
    
    // Flag to track if we've started playback
    this.hasStarted = false;
    
    // Debug counters
    this.underrunCount = 0;
    this.chunkCount = 0;
    this.droppedSamples = 0;
    this.overflowCount = 0;
    
    // Listen for audio chunks from main thread
    this.port.onmessage = (event) => {
      const { audioData, sampleRate, reset } = event.data;
      
      // Handle reset command (e.g., on barge-in)
      if (reset) {
        this._resetBuffer();
        return;
      }
      
      // Update source sample rate if provided (from backend ready payload).
      if (sampleRate && sampleRate > 0 && sampleRate !== this.sourceSampleRate) {
        this.sourceSampleRate = sampleRate;
        console.log(
          `[AudioWorklet] Source sample rate set to ${this.sourceSampleRate} Hz (output ${this.outputSampleRate} Hz)`
        );
      }
      
      // Convert Int16 ArrayBuffer to Float32Array
      const int16Data = new Int16Array(audioData);
      
      // Convert Int16 (-32768 to 32767) to Float32 (-1.0 to 1.0)
      let float32Data = new Float32Array(int16Data.length);
      for (let i = 0; i < int16Data.length; i++) {
        float32Data[i] = int16Data[i] / 32768.0;
      }

      // Resample to match AudioContext output rate when needed.
      if (this.sourceSampleRate !== this.outputSampleRate) {
        float32Data = this._resampleLinear(
          float32Data,
          this.sourceSampleRate,
          this.outputSampleRate
        );
      }
      
      // Add to ring buffer
      this._addToBuffer(float32Data);
      this.chunkCount++;
    };
  }

  /**
   * Linear resampler (fast and adequate for speech playback).
   */
  _resampleLinear(input, fromRate, toRate) {
    if (!input || input.length === 0 || fromRate <= 0 || toRate <= 0 || fromRate === toRate) {
      return input;
    }

    const ratio = toRate / fromRate;
    const outputLength = Math.max(1, Math.round(input.length * ratio));
    const output = new Float32Array(outputLength);

    for (let i = 0; i < outputLength; i++) {
      const srcPos = i / ratio;
      const idx = Math.floor(srcPos);
      const frac = srcPos - idx;
      const a = input[Math.min(idx, input.length - 1)];
      const b = input[Math.min(idx + 1, input.length - 1)];
      output[i] = a + (b - a) * frac;
    }

    return output;
  }
  
  /**
   * Reset the buffer state (called on barge-in/interruption)
   */
  _resetBuffer() {
    this.bufferFillCount = 0;
    this.bufferReadIndex = 0;
    this.bufferWriteIndex = 0;
    this.hasStarted = false;
    this.underrunCount = 0;
    this.chunkCount = 0;
    this.droppedSamples = 0;
    this.overflowCount = 0;
    // Clear buffer to prevent old audio playing after reset
    this.buffer.fill(0);
  }
  
  _growBuffer(newSize) {
    if (newSize <= this.maxBufferSize) return;
    const targetSize = Math.min(newSize, this.maxHardBufferSize);
    if (targetSize <= this.maxBufferSize) return;

    const newBuffer = new Float32Array(targetSize);
    const toCopy = Math.min(this.bufferFillCount, targetSize);
    for (let i = 0; i < toCopy; i++) {
      newBuffer[i] = this.buffer[(this.bufferReadIndex + i) % this.maxBufferSize];
    }

    this.buffer = newBuffer;
    this.maxBufferSize = targetSize;
    this.bufferReadIndex = 0;
    this.bufferWriteIndex = toCopy % this.maxBufferSize;
    this.bufferFillCount = toCopy;
  }

  /**
   * Add samples to the ring buffer.
   *
   * Strategy:
   * 1) Grow buffer up to hard cap when needed.
   * 2) If still over cap, drop newest input tail (not oldest queued audio)
   *    to avoid "fast-forward" perception during playback.
   */
  _addToBuffer(samples) {
    let len = samples.length;

    // Try to grow before dropping anything.
    const requiredSize = this.bufferFillCount + len;
    if (requiredSize > this.maxBufferSize && this.maxBufferSize < this.maxHardBufferSize) {
      this._growBuffer(requiredSize);
    }
    
    // If still too large, keep continuity by dropping newest tail.
    if (this.bufferFillCount + len > this.maxBufferSize) {
      const allowed = Math.max(0, this.maxBufferSize - this.bufferFillCount);
      const toDrop = len - allowed;
      len = allowed;
      this.droppedSamples += toDrop;
      this.overflowCount++;
      if (this.overflowCount <= 3 || this.overflowCount % 10 === 0) {
        console.warn(
          `[AudioWorklet] Buffer overflow #${this.overflowCount}. ` +
          `Dropped newest ${toDrop} samples (total dropped ${this.droppedSamples}).`
        );
      }
    }

    if (len <= 0) {
      return;
    }
    
    // Add samples to buffer
    for (let i = 0; i < len; i++) {
      this.buffer[this.bufferWriteIndex] = samples[i];
      this.bufferWriteIndex = (this.bufferWriteIndex + 1) % this.maxBufferSize;
    }
    this.bufferFillCount += len;
  }
  
  /**
   * Read samples from the ring buffer
   * Returns number of samples actually read
   */
  _readFromBuffer(outputArray) {
    const len = outputArray.length;
    const toRead = Math.min(len, this.bufferFillCount);
    
    for (let i = 0; i < toRead; i++) {
      outputArray[i] = this.buffer[this.bufferReadIndex];
      this.bufferReadIndex = (this.bufferReadIndex + 1) % this.maxBufferSize;
    }
    
    this.bufferFillCount -= toRead;
    return toRead;
  }

  /**
   * Called by the audio engine for each render quantum (usually 128 samples)
   * @param {Array<Array<Float32Array>>} inputs - Input audio data
   * @param {Array<Array<Float32Array>>} outputs - Output audio data
   * @param {Object} parameters - Audio parameters
   * @returns {boolean} - Keep processor alive
   */
  process(inputs, outputs) {
    const output = outputs[0];
    const channel = output[0];
    const requiredSamples = channel.length; // Typically 128 samples
    
    // Pre-buffer stage: accumulate enough audio before starting playback
    // This prevents initial underrun and provides jitter tolerance
    if (!this.hasStarted) {
      if (this.bufferFillCount >= this.targetPreBufferSize) {
        this.hasStarted = true;
      } else {
        // Output silence while buffering
        channel.fill(0);
        return true;
      }
    }
    
    // Normal playback: output audio from buffer
    if (this.bufferFillCount >= requiredSamples) {
      // Read samples from buffer
      this._readFromBuffer(channel);
      
      // If we drained the buffer significantly, go back to pre-buffer mode
      // This prevents stuttering when network is slow
      if (this.bufferFillCount < this.targetPreBufferSize / 2) {
        this.hasStarted = false;
      }
    } else {
      // Buffer underrun: not enough audio available
      // This happens when network is slower than playback
      this.underrunCount++;
      
      // Output silence to prevent glitching
      channel.fill(0);
      
      // Reset to pre-buffer state to re-accumulate
      this.hasStarted = false;
      
      // Log underrun every 10 occurrences (don't spam)
      if (this.underrunCount % 10 === 0) {
        console.warn(`[AudioWorklet] Buffer underrun #${this.underrunCount}. ` +
                    `Buffer: ${this.bufferFillCount}/${requiredSamples} samples, ` +
                    `Dropped: ${this.droppedSamples}`);
      }
    }
    
    // Return true to keep processor alive
    return true;
  }
}

// Register the processor
registerProcessor('audio-stream-processor', AudioStreamProcessor);
