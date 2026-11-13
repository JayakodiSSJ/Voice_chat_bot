import { createPcmWavRecorder, stopAudioPlayback } from "./audioUtils";

describe("voice audio utilities", () => {
  afterEach(() => {
    delete window.AudioContext;
  });

  test("captures microphone PCM directly as mono 16 kHz WAV without decoding compressed audio", async () => {
    let processor;
    const audioContext = {
      sampleRate: 32000,
      state: "running",
      destination: {},
      createMediaStreamSource: jest.fn(() => ({ connect: jest.fn(), disconnect: jest.fn() })),
      createScriptProcessor: jest.fn(() => {
        processor = { connect: jest.fn(), disconnect: jest.fn(), onaudioprocess: null };
        return processor;
      }),
      createGain: jest.fn(() => ({ gain: {}, connect: jest.fn(), disconnect: jest.fn() })),
      close: jest.fn().mockResolvedValue(undefined),
    };
    window.AudioContext = jest.fn(() => audioContext);

    const recorder = await createPcmWavRecorder({});
    processor.onaudioprocess({
      inputBuffer: {
        length: 4,
        numberOfChannels: 1,
        getChannelData: () => new Float32Array([-1, -0.5, 0.5, 1]),
      },
    });
    const wavBlob = await recorder.stop();
    const bytes = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(reader.error);
      reader.readAsArrayBuffer(wavBlob);
    });
    const view = new DataView(bytes);

    expect(wavBlob.type).toBe("audio/wav");
    expect(view.getUint32(24, true)).toBe(16000);
    expect(view.getUint16(22, true)).toBe(1);
    expect(view.getUint16(34, true)).toBe(16);
    expect(view.getUint32(40, true)).toBe(4);
    expect(audioContext.createScriptProcessor).toHaveBeenCalledWith(4096, 1, 1);
    expect(audioContext.close).toHaveBeenCalledTimes(1);
  });

  test("rejects empty PCM capture instead of producing an invalid WAV", async () => {
    const audioContext = {
      sampleRate: 48000,
      state: "running",
      destination: {},
      createMediaStreamSource: jest.fn(() => ({ connect: jest.fn(), disconnect: jest.fn() })),
      createScriptProcessor: jest.fn(() => ({ connect: jest.fn(), disconnect: jest.fn(), onaudioprocess: null })),
      createGain: jest.fn(() => ({ gain: {}, connect: jest.fn(), disconnect: jest.fn() })),
      close: jest.fn().mockResolvedValue(undefined),
    };
    window.AudioContext = jest.fn(() => audioContext);

    const recorder = await createPcmWavRecorder({});
    await expect(recorder.stop()).rejects.toThrow("No microphone audio was captured");
    expect(audioContext.close).toHaveBeenCalledTimes(1);
  });

  test("stops playback and releases its temporary audio URL", () => {
    const audioElement = {
      pause: jest.fn(),
      removeAttribute: jest.fn(),
      load: jest.fn(),
    };
    const urlApi = { revokeObjectURL: jest.fn() };

    stopAudioPlayback(audioElement, "blob:voice", urlApi);

    expect(audioElement.pause).toHaveBeenCalledTimes(1);
    expect(audioElement.removeAttribute).toHaveBeenCalledWith("src");
    expect(audioElement.load).toHaveBeenCalledTimes(1);
    expect(urlApi.revokeObjectURL).toHaveBeenCalledWith("blob:voice");
  });

});
