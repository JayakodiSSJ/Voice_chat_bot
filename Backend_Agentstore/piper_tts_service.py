from __future__ import annotations

import io
import os
import threading
import wave
from pathlib import Path

from piper import PiperVoice


DEFAULT_MODEL_PATH = "/app/models/si_LK-sinhala-medium.onnx"


class PiperSinhalaTTS:
    def __init__(self) -> None:
        self._voice: PiperVoice | None = None
        self._lock = threading.Lock()

    def _load_voice(self) -> PiperVoice:
        if self._voice is None:
            model_path = Path(
                os.getenv("PIPER_SINHALA_MODEL_PATH", DEFAULT_MODEL_PATH).strip()
                or DEFAULT_MODEL_PATH
            )
            config_path = Path(f"{model_path}.json")
            if not model_path.is_file() or not config_path.is_file():
                raise RuntimeError("The local Piper Sinhala voice model is not installed.")
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
                raise RuntimeError("Piper did not generate any Sinhala speech audio.")

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


piper_sinhala_tts = PiperSinhalaTTS()
