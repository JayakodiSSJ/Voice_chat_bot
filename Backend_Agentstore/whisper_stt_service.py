from __future__ import annotations

import io
import os
import threading
from pathlib import Path

from gemini_service import CUSTOM_VOCABULARY, _validate_wav_audio


DEFAULT_WHISPER_MODEL_PATH = "/app/models/whisper"


def stt_provider() -> str:
    """Return the configured speech-to-text provider: local "whisper" (default) or "gemini"."""
    provider = os.getenv("STT_PROVIDER", "").strip().lower()
    return provider if provider in {"whisper", "gemini"} else "whisper"


def _positive_int(env_name: str, default: int) -> int:
    try:
        value = int(os.getenv(env_name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


class WhisperSTT:
    """Local faster-whisper transcription; the model is loaded once per process."""

    def __init__(self) -> None:
        self._model = None
        self._lock = threading.Lock()

    @staticmethod
    def model_path() -> Path:
        return Path(
            os.getenv("WHISPER_MODEL_PATH", DEFAULT_WHISPER_MODEL_PATH).strip()
            or DEFAULT_WHISPER_MODEL_PATH
        )

    def is_installed(self) -> bool:
        return (self.model_path() / "model.bin").is_file()

    def _load_model(self):
        if self._model is None:
            if not self.is_installed():
                raise RuntimeError("The local Whisper speech recognition model is not installed.")
            # Imported lazily so the rest of the backend does not require faster-whisper.
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                str(self.model_path()),
                device="cpu",
                compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "").strip() or "int8",
                cpu_threads=_positive_int("WHISPER_CPU_THREADS", 4),
            )
        return self._model

    def transcribe(self, audio_bytes: bytes, language_code: str | None = None) -> str:
        if not audio_bytes:
            raise ValueError("The audio recording is empty.")
        _validate_wav_audio(audio_bytes, "audio/wav")
        # Route language codes look like "si-LK"; Whisper expects "si".
        language = language_code.split("-")[0].lower() if language_code else None

        with self._lock:
            model = self._load_model()
            segments, _info = model.transcribe(
                io.BytesIO(audio_bytes),
                language=language,
                beam_size=_positive_int("WHISPER_BEAM_SIZE", 5),
                # English hotwords make non-English output hallucinate them, so only bias English.
                hotwords=" ".join(CUSTOM_VOCABULARY) if language == "en" else None,
                condition_on_previous_text=False,
                # A single greedy pass with repeat blocking avoids long hallucination loops
                # (temperature fallback took ~50 s on unclear audio during testing).
                temperature=0.0,
                no_repeat_ngram_size=3,
            )
            # Segments are generated lazily, so consume them while holding the lock.
            texts = [segment.text.strip() for segment in segments]
        return " ".join(text for text in texts if text).strip()


whisper_stt = WhisperSTT()
