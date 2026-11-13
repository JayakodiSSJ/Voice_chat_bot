import { createVoiceActivityDetector } from "./voiceActivity";

describe("voice activity detection", () => {
  test("ignores short background noise and detects sustained speech", () => {
    const detector = createVoiceActivityDetector({ threshold: 0.02, speechStartMs: 180 });

    expect(detector.update(0.04, 1000)).toBeNull();
    expect(detector.update(0, 1100)).toBeNull();
    expect(detector.hasSpeech).toBe(false);
    expect(detector.update(0.04, 1200)).toBeNull();
    expect(detector.update(0.04, 1380)).toBe("speech-start");
    expect(detector.hasSpeech).toBe(true);
  });

  test("ends an utterance only after the configured silence interval", () => {
    const detector = createVoiceActivityDetector({
      threshold: 0.02,
      speechStartMs: 100,
      silenceEndMs: 900,
    });

    detector.update(0.04, 1000);
    expect(detector.update(0.04, 1100)).toBe("speech-start");
    expect(detector.update(0, 1200)).toBeNull();
    expect(detector.update(0, 2099)).toBeNull();
    expect(detector.update(0, 2100)).toBe("speech-end");
  });

  test("resets silence timing when speech resumes", () => {
    const detector = createVoiceActivityDetector({
      threshold: 0.02,
      speechStartMs: 100,
      silenceEndMs: 900,
    });

    detector.update(0.04, 1000);
    detector.update(0.04, 1100);
    detector.update(0, 1200);
    expect(detector.update(0.04, 1900)).toBeNull();
    expect(detector.update(0, 2000)).toBeNull();
    expect(detector.update(0, 2900)).toBe("speech-end");
  });
});
