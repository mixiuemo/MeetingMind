function downsampleTo16k(samples, inputRate) {
  if (inputRate === 16000) {
    return samples;
  }

  const ratio = inputRate / 16000;
  const outputLength = Math.floor(samples.length / ratio);
  const output = new Float32Array(outputLength);

  for (let i = 0; i < outputLength; i += 1) {
    const start = Math.floor(i * ratio);
    const end = Math.min(Math.floor((i + 1) * ratio), samples.length);
    let sum = 0;
    for (let j = start; j < end; j += 1) {
      sum += samples[j];
    }
    output[i] = sum / Math.max(1, end - start);
  }

  return output;
}

function floatToPcm16(samples) {
  const output = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i += 1) {
    const value = Math.max(-1, Math.min(1, samples[i]));
    output[i] = value < 0 ? value * 32768 : value * 32767;
  }
  return output;
}

export function prepareAudioChunk(samples, inputRate) {
  return floatToPcm16(downsampleTo16k(samples, inputRate));
}

export function calculateLevel(samples) {
  let sum = 0;
  for (const sample of samples) {
    sum += sample * sample;
  }
  return Math.min(1, Math.sqrt(sum / samples.length) * 4);
}

