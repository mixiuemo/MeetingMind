class AudioCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(4096);
    this.offset = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (input && input[0]) {
      const samples = input[0];
      let sourceOffset = 0;
      while (sourceOffset < samples.length) {
        const writable = Math.min(
          this.buffer.length - this.offset,
          samples.length - sourceOffset,
        );
        this.buffer.set(
          samples.subarray(sourceOffset, sourceOffset + writable),
          this.offset,
        );
        this.offset += writable;
        sourceOffset += writable;

        if (this.offset === this.buffer.length) {
          this.port.postMessage(this.buffer, [this.buffer.buffer]);
          this.buffer = new Float32Array(4096);
          this.offset = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor("audio-capture-processor", AudioCaptureProcessor);
