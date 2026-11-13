// Small language map for the voice agent's speech recognition (STT) and
// speech synthesis (TTS) — mirrors Voice_Aisle's app/components/voice/types.ts.
export const VOICE_LANGUAGES = {
  auto: { code: "auto", bcp47: "", nativeLabel: "Auto detect", flag: "🌐" },
  en: { code: "en", bcp47: "en-US", nativeLabel: "English", flag: "🇬🇧" },
  si: { code: "si", bcp47: "si-LK", nativeLabel: "සිංහල", flag: "🇱🇰" },
  ta: { code: "ta", bcp47: "ta-LK", nativeLabel: "தமிழ்", flag: "🇱🇰" },
};

export default VOICE_LANGUAGES;
