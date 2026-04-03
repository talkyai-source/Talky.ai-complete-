class PCM16Processor extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel && channel.length) {
      const pcm = new Int16Array(channel.length);
      for (let i = 0; i < channel.length; i++) {
        const sample = channel[i] < -1 ? -1 : channel[i] > 1 ? 1 : channel[i];
        pcm[i] = sample < 0 ? (sample * 32768) | 0 : (sample * 32767) | 0;
      }
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
    }
    return true;
  }
}

registerProcessor("pcm16-processor", PCM16Processor);
