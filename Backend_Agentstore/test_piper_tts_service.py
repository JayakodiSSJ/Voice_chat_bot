import io
import os
import tempfile
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import patch

from piper_tts_service import PiperSinhalaTTS


class PiperSinhalaTTSTests(unittest.TestCase):
    def test_synthesis_writes_pcm_chunks_to_wav_using_model_sample_rate(self):
        voice = SimpleNamespace(
            synthesize=lambda _text: iter(
                [
                    SimpleNamespace(
                        sample_rate=22050,
                        sample_width=2,
                        sample_channels=1,
                        audio_int16_bytes=b"\x01\x00" * 10,
                    ),
                    SimpleNamespace(audio_int16_bytes=b"\x02\x00" * 5),
                ]
            )
        )
        service = PiperSinhalaTTS()
        service._voice = voice

        result = service.synthesize("ආයුබෝවන්")

        self.assertTrue(result.startswith(b"RIFF"))
        with wave.open(io.BytesIO(result), "rb") as audio:
            self.assertEqual(audio.getframerate(), 22050)
            self.assertEqual(audio.getnchannels(), 1)
            self.assertEqual(audio.getsampwidth(), 2)
            self.assertEqual(audio.getnframes(), 15)

    def test_model_is_loaded_once_from_configured_local_path(self):
        with tempfile.TemporaryDirectory() as model_dir:
            model_path = os.path.join(model_dir, "sinhala.onnx")
            config_path = f"{model_path}.json"
            with open(model_path, "wb") as model, open(config_path, "w", encoding="utf-8") as config:
                model.write(b"model")
                config.write("{}")

            voice = SimpleNamespace(
                synthesize=lambda _text: iter(
                    [
                        SimpleNamespace(
                            sample_rate=22050,
                            sample_width=2,
                            sample_channels=1,
                            audio_int16_bytes=b"\x00\x00" * 10,
                        )
                    ]
                )
            )
            service = PiperSinhalaTTS()
            with (
                patch.dict(os.environ, {"PIPER_SINHALA_MODEL_PATH": model_path}),
                patch("piper_tts_service.PiperVoice.load", return_value=voice) as load_voice,
            ):
                service.synthesize("ආයුබෝවන්")
                service.synthesize("සුබ දවසක්")

        load_voice.assert_called_once_with(model_path, config_path=config_path)

    def test_missing_local_model_fails_with_controlled_error(self):
        service = PiperSinhalaTTS()
        with (
            patch.dict(os.environ, {"PIPER_SINHALA_MODEL_PATH": "/missing/sinhala.onnx"}),
            self.assertRaisesRegex(RuntimeError, "model is not installed"),
        ):
            service.synthesize("ආයුබෝවන්")


if __name__ == "__main__":
    unittest.main()
