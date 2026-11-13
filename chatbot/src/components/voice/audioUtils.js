const TARGET_SAMPLE_RATE = 16000;

export const stopAudioPlayback = (audioElement, objectUrl, urlApi = window.URL) => {
  if (audioElement) {
    audioElement.pause();
    audioElement.removeAttribute("src");
    audioElement.load();
  }
  if (objectUrl) urlApi.revokeObjectURL(objectUrl);
};

const writeAscii = (view, offset, value) => {
  for (let index = 0; index < value.length; index += 1) {
    view.setUint8(offset + index, value.charCodeAt(index));
  }
};

const encodePcmWav = (samples, sourceRate) => {
  if (!samples.length || sourceRate <= 0) {
    throw new Error("No microphone audio was captured.");
  }
  const outputLength = Math.ceil(samples.length * TARGET_SAMPLE_RATE / sourceRate);
  const pcm = new Int16Array(outputLength);
  for (let outputIndex = 0; outputIndex < outputLength; outputIndex += 1) {
    const start = Math.floor(outputIndex * sourceRate / TARGET_SAMPLE_RATE);
    const end = Math.min(
      samples.length,
      Math.max(start + 1, Math.floor((outputIndex + 1) * sourceRate / TARGET_SAMPLE_RATE))
    );
    let sum = 0;
    for (let sourceIndex = start; sourceIndex < end; sourceIndex += 1) sum += samples[sourceIndex];
    const sample = Math.max(-1, Math.min(1, sum / Math.max(1, end - start)));
    pcm[outputIndex] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }

  const wavBuffer = new ArrayBuffer(44 + pcm.byteLength);
  const view = new DataView(wavBuffer);
  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + pcm.byteLength, true);
  writeAscii(view, 8, "WAVE");
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, TARGET_SAMPLE_RATE, true);
  view.setUint32(28, TARGET_SAMPLE_RATE * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(view, 36, "data");
  view.setUint32(40, pcm.byteLength, true);
  new Int16Array(wavBuffer, 44).set(pcm);
  return new Blob([wavBuffer], { type: "audio/wav" });
};

export const createPcmWavRecorder = async (stream) => {
  const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextCtor) {
    throw new Error("Audio recording is not supported in this browser.");
  }

  const audioContext = new AudioContextCtor();
  if (typeof audioContext.createScriptProcessor !== "function") {
    await audioContext.close();
    throw new Error("Audio recording is not supported in this browser.");
  }
  try {
    const source = audioContext.createMediaStreamSource(stream);
    const processor = audioContext.createScriptProcessor(4096, 1, 1);
    const mute = audioContext.createGain();
    const chunks = [];
    let sampleCount = 0;
    let stopPromise = null;
    mute.gain.value = 0;
    processor.onaudioprocess = (event) => {
      const input = event.inputBuffer;
      if (!input.numberOfChannels || !input.length) return;
      const samples = new Float32Array(input.length);
      for (let channel = 0; channel < input.numberOfChannels; channel += 1) {
        const channelData = input.getChannelData(channel);
        for (let index = 0; index < input.length; index += 1) {
          samples[index] += channelData[index] / input.numberOfChannels;
        }
      }
      chunks.push(samples);
      sampleCount += samples.length;
    };
    source.connect(processor);
    processor.connect(mute);
    mute.connect(audioContext.destination);
    if (audioContext.state === "suspended") await audioContext.resume();

    return {
      stop() {
        if (stopPromise) return stopPromise;
        processor.onaudioprocess = null;
        source.disconnect();
        processor.disconnect();
        mute.disconnect();
        stopPromise = audioContext.close().then(() => {
          const samples = new Float32Array(sampleCount);
          let offset = 0;
          chunks.forEach((chunk) => {
            samples.set(chunk, offset);
            offset += chunk.length;
          });
          chunks.length = 0;
          return encodePcmWav(samples, audioContext.sampleRate);
        });
        return stopPromise;
      },
    };
  } catch (error) {
    if (audioContext.state !== "closed") await audioContext.close();
    throw error;
  }
};
