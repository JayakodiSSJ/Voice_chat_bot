from __future__ import annotations

import io
import os
import threading
import wave
from pathlib import Path

from piper import PiperVoice


DEFAULT_MODEL_PATH = "/app/models/si_LK-sinhala-medium.onnx"
DEFAULT_ENGLISH_MODEL_PATH = "/app/models/en_US-lessac-medium.onnx"


class PiperTTS:
    """Local Piper voice loaded once per process from a configurable ONNX path."""

    def __init__(self, *, env_name: str, default_path: str, language_name: str) -> None:
        self._env_name = env_name
        self._default_path = default_path
        self._language_name = language_name
        self._voice: PiperVoice | None = None
        self._lock = threading.Lock()

    def model_path(self) -> Path:
        return Path(os.getenv(self._env_name, self._default_path).strip() or self._default_path)

    def is_installed(self) -> bool:
        model_path = self.model_path()
        return model_path.is_file() and Path(f"{model_path}.json").is_file()

    def _load_voice(self) -> PiperVoice:
        if self._voice is None:
            model_path = self.model_path()
            config_path = Path(f"{model_path}.json")
            if not model_path.is_file() or not config_path.is_file():
                raise RuntimeError(f"The local Piper {self._language_name} voice model is not installed.")
            self._voice = PiperVoice.load(str(model_path), config_path=str(config_path))
        return self._voice

    def synthesize(self, text: str) -> bytes:
        if not text.strip():
            raise ValueError("Text is required.")

        with self._lock:
            voice = self._load_voice()
            chunks = iter(voice.synthesize(text))
            first_chunk = next(chunks, None)
            if first_chunk is None or not first_chunk.audio_int16_bytes:
                raise RuntimeError(f"Piper did not generate any {self._language_name} speech audio.")

            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, "wb") as wav_file:
                wav_file.setnchannels(first_chunk.sample_channels)
                wav_file.setsampwidth(first_chunk.sample_width)
                wav_file.setframerate(first_chunk.sample_rate)
                wav_file.writeframes(first_chunk.audio_int16_bytes)
                for chunk in chunks:
                    wav_file.writeframes(chunk.audio_int16_bytes)

            audio = wav_buffer.getvalue()
            if len(audio) <= 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
                raise RuntimeError("Piper returned invalid WAV audio.")
            return audio


class PiperSinhalaTTS(PiperTTS):
    def __init__(self) -> None:
        super().__init__(
            env_name="PIPER_SINHALA_MODEL_PATH",
            default_path=DEFAULT_MODEL_PATH,
            language_name="Sinhala",
        )


class PiperEnglishTTS(PiperTTS):
    def __init__(self) -> None:
        super().__init__(
            env_name="PIPER_ENGLISH_MODEL_PATH",
            default_path=DEFAULT_ENGLISH_MODEL_PATH,
            language_name="English",
        )


piper_sinhala_tts = PiperSinhalaTTS()
piper_english_tts = PiperEnglishTTS()
