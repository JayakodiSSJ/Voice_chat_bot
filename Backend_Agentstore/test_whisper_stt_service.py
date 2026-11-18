import io
import os
import sys
import tempfile
import unittest
import wave
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

from whisper_stt_service import WhisperSTT, stt_provider


def make_wav(duration_seconds=1, sample_rate=16000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * sample_rate * duration_seconds)
    return buffer.getvalue()


def fake_model(texts):
    model = MagicMock()
    model.transcribe.return_value = (
        (SimpleNamespace(text=text) for text in texts),
        SimpleNamespace(language="si"),
    )
    return model


class WhisperSTTTests(unittest.TestCase):
    def test_provider_defaults_to_local_whisper(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(stt_provider(), "whisper")
        with patch.dict(os.environ, {"STT_PROVIDER": "gemini"}):
            self.assertEqual(stt_provider(), "gemini")
        with patch.dict(os.environ, {"STT_PROVIDER": "unknown"}):
            self.assertEqual(stt_provider(), "whisper")

    def test_transcription_joins_segments_and_maps_language_code(self):
        service = WhisperSTT()
        service._model = fake_model([" මට Fiber ", "", " package එකක් ඕනේ. "])

        result = service.transcribe(make_wav(), language_code="si-LK")

        self.assertEqual(result, "මට Fiber package එකක් ඕනේ.")
        kwargs = service._model.transcribe.call_args.kwargs
        self.assertEqual(kwargs["language"], "si")
        self.assertIsNone(kwargs["hotwords"])
        self.assertEqual(kwargs["temperature"], 0.0)
        self.assertEqual(kwargs["no_repeat_ngram_size"], 3)

    def test_english_requests_are_biased_toward_package_vocabulary(self):
        service = WhisperSTT()
        service._model = fake_model(["Fiber"])

        service.transcribe(make_wav(), language_code="en-US")
        self.assertIn("SLT-MOBITEL", service._model.transcribe.call_args.kwargs["hotwords"])

    def test_auto_language_passes_none_to_whisper(self):
        service = WhisperSTT()
        service._model = fake_model(["Hello"])

        self.assertEqual(service.transcribe(make_wav()), "Hello")
        self.assertIsNone(service._model.transcribe.call_args.kwargs["language"])
        self.assertIsNone(service._model.transcribe.call_args.kwargs["hotwords"])

    def test_invalid_audio_is_rejected_before_transcription(self):
        service = WhisperSTT()
        service._model = fake_model(["unused"])

        with self.assertRaises(ValueError):
            service.transcribe(b"")
        with self.assertRaises(ValueError):
            service.transcribe(b"not a wav file at all, definitely not RIFF data")
        with self.assertRaisesRegex(ValueError, "30 second"):
            service.transcribe(make_wav(duration_seconds=31))
        service._model.transcribe.assert_not_called()

    def test_missing_model_fails_with_controlled_error(self):
        service = WhisperSTT()
        with (
            patch.dict(os.environ, {"WHISPER_MODEL_PATH": "/missing/whisper"}),
            self.assertRaisesRegex(RuntimeError, "model is not installed"),
        ):
            service.transcribe(make_wav())

    def test_model_is_loaded_once_from_configured_path(self):
        with tempfile.TemporaryDirectory() as model_dir:
            with open(os.path.join(model_dir, "model.bin"), "wb") as model_file:
                model_file.write(b"model")

            fake_module = ModuleType("faster_whisper")
            fake_module.WhisperModel = MagicMock(side_effect=lambda *a, **k: fake_model(["Hi"]))
            service = WhisperSTT()
            with (
                patch.dict(os.environ, {"WHISPER_MODEL_PATH": model_dir}),
                patch.dict(sys.modules, {"faster_whisper": fake_module}),
            ):
                self.assertTrue(service.is_installed())
                service.transcribe(make_wav())
                service._model.transcribe.return_value = (
                    iter([SimpleNamespace(text="Again")]),
                    SimpleNamespace(language="en"),
                )
                self.assertEqual(service.transcribe(make_wav()), "Again")

        fake_module.WhisperModel.assert_called_once()
        self.assertEqual(fake_module.WhisperModel.call_args.args[0], model_dir)
        self.assertEqual(fake_module.WhisperModel.call_args.kwargs["device"], "cpu")


if __name__ == "__main__":
    unittest.main()
