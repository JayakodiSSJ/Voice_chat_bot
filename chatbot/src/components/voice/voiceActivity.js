export const createVoiceActivityDetector = ({
  threshold = 0.015,
  speechStartMs = 180,
  silenceEndMs = 1000,
} = {}) => {
  let speechCandidateAt = null;
  let speechStarted = false;
  let silenceStartedAt = null;

  return {
    update(volume, now) {
      if (!speechStarted) {
        if (volume < threshold) {
          speechCandidateAt = null;
          return null;
        }
        if (speechCandidateAt === null) speechCandidateAt = now;
        if (now - speechCandidateAt < speechStartMs) return null;
        speechStarted = true;
        speechCandidateAt = null;
        return "speech-start";
      }

      if (volume >= threshold) {
        silenceStartedAt = null;
        return null;
      }
      if (silenceStartedAt === null) silenceStartedAt = now;
      return now - silenceStartedAt >= silenceEndMs ? "speech-end" : null;
    },
    get hasSpeech() {
      return speechStarted;
    },
  };
};
