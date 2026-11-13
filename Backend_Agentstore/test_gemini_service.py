import base64
import io
import os
import unittest
import wave
from unittest.mock import Mock, patch

import requests

from gemini_service import (
    MAX_AUDIO_BYTES,
    GeminiService,
    _decode_tts_audio,
    detect_transcript_language,
    normalize_sinhala_tts_text,
)


def make_wav(duration_seconds=1, sample_rate=16000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * sample_rate * duration_seconds)
    return buffer.getvalue()


def api_response(data, *, status_code=200, headers=None):
    response = Mock()
    response.ok = 200 <= status_code < 300
    response.status_code = status_code
    response.headers = headers or {}
    response.json.return_value = data
    return response


class GeminiServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = GeminiService()

    def test_service_configuration_requires_a_gemini_api_key(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=True):
            self.assertTrue(self.service.is_configured())
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(self.service.is_configured())

    def test_sinhala_transcription_preserves_unicode_and_uses_configured_model(self):
        transcript = "මට 10 GB package එකක් ඕනේ."
        responses = [
            api_response({}, headers={"X-Goog-Upload-URL": "https://upload.example"}),
            api_response({"file": {"uri": "files/audio", "name": "files/audio"}}),
            api_response(
                {
                    "steps": [
                        {
                            "type": "model_output",
                            "content": [{"type": "transcription", "text": transcript}],
                        }
                    ]
                }
            ),
        ]
        with (
            patch("gemini_service.requests.post", side_effect=responses) as post,
            patch("gemini_service.requests.delete") as delete,
            patch.dict(os.environ, {"GEMINI_TRANSCRIBE_MODEL": "gemini-3.5-transcribe"}),
        ):
            result = self.service.transcribe_audio(
                api_key="test-key",
                audio_bytes=make_wav(),
                mime_type="audio/wav",
                filename="sinhala.wav",
                language_code="si-LK",
            )

        self.assertEqual(result, transcript)
        request_body = post.call_args_list[-1].kwargs["json"]
        self.assertEqual(request_body["model"], "gemini-3.5-transcribe")
        self.assertEqual(
            request_body["generation_config"]["transcription_config"]["language_codes"],
            ["si-LK"],
        )
        self.assertIn(
            "SLT-MOBITEL",
            request_body["generation_config"]["transcription_config"]["custom_vocabulary"],
        )
        delete.assert_called_once()

    def test_auto_transcription_preserves_mixed_language_text_without_confidence_claims(self):
        transcript = "මට Fiber connection එකක් ඕනේ, 30 Mbps plan එකක්."
        responses = [
            api_response({}, headers={"X-Goog-Upload-URL": "https://upload.example"}),
            api_response({"file": {"uri": "files/audio", "name": "files/audio"}}),
            api_response(
                {
                    "steps": [
                        {
                            "type": "model_output",
                            "content": [{"type": "text", "text": transcript}],
                        }
                    ]
                }
            ),
        ]
        with (
            patch("gemini_service.requests.post", side_effect=responses) as post,
            patch("gemini_service.requests.delete"),
        ):
            result = self.service.transcribe_audio(
                api_key="test-key",
                audio_bytes=make_wav(),
                mime_type="audio/wav",
                filename="mixed.wav",
            )

        config = post.call_args_list[-1].kwargs["json"]["generation_config"]["transcription_config"]
        self.assertEqual(config["language_codes"], [])
        self.assertEqual(result, transcript)
        self.assertEqual(detect_transcript_language(result), "mixed")

    def test_english_transcription_is_detected_from_its_text(self):
        self.assertEqual(
            detect_transcript_language("What internet packages are available?"),
            "en-US",
        )

    def test_sinhala_unicode_language_detection(self):
        self.assertEqual(detect_transcript_language("මට package එකක් ඕනේ"), "mixed")
        self.assertEqual(detect_transcript_language("මට පැකේජ් එකක් ඕනේ"), "si-LK")

    def test_invalid_audio_is_rejected_before_upload(self):
        with patch("gemini_service.requests.post") as post:
            with self.assertRaisesRegex(ValueError, "valid WAV"):
                self.service.transcribe_audio(
                    api_key="test-key",
                    audio_bytes=b"not wav",
                    mime_type="audio/wav",
                )
            post.assert_not_called()

    def test_empty_oversized_and_unsupported_audio_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            self.service.transcribe_audio(
                api_key="test-key",
                audio_bytes=b"",
                mime_type="audio/wav",
            )
        with self.assertRaisesRegex(ValueError, "5 MB"):
            self.service.transcribe_audio(
                api_key="test-key",
                audio_bytes=b"x" * (MAX_AUDIO_BYTES + 1),
                mime_type="audio/wav",
            )
        with self.assertRaisesRegex(ValueError, "PCM WAV"):
            self.service.transcribe_audio(
                api_key="test-key",
                audio_bytes=make_wav(),
                mime_type="audio/webm",
            )

    def test_synthesis_uses_configured_model_voice_and_returns_valid_wav(self):
        audio = make_wav(sample_rate=24000)
        response = api_response(
            {
                "output_audio": {
                    "data": base64.b64encode(audio).decode("ascii"),
                    "mime_type": "audio/wav",
                }
            }
        )
        with (
            patch("gemini_service.requests.post", return_value=response) as post,
            patch.dict(
                os.environ,
                {"GEMINI_TTS_MODEL": "gemini-3.8-flash-lite-tts"},
            ),
        ):
            result_audio, mime_type = self.service.synthesize_speech(
                api_key="test-key",
                text="ආයුබෝවන්. Hello!",
                voice="Aoede",
            )

        request_body = post.call_args.kwargs["json"]
        self.assertEqual(request_body["model"], "gemini-3.8-flash-lite-tts")
        self.assertEqual(request_body["generation_config"]["speech_config"], [{"voice": "Aoede"}])
        self.assertEqual(mime_type, "audio/wav")
        self.assertTrue(result_audio.startswith(b"RIFF"))
        with wave.open(io.BytesIO(result_audio), "rb") as wav_file:
            self.assertEqual(wav_file.getframerate(), 24000)

    def test_sinhala_only_synthesis_uses_local_piper_without_a_gemini_request(self):
        audio = make_wav(sample_rate=22050)
        with (
            patch("gemini_service.piper_sinhala_tts.synthesize", return_value=audio) as piper_synthesize,
            patch("gemini_service.requests.post") as post,
        ):
            result_audio, mime_type = self.service.synthesize_speech(
                api_key="",
                text="ආයුබෝවන් 123",
            )

        self.assertEqual((result_audio, mime_type), (audio, "audio/wav"))
        piper_synthesize.assert_called_once_with("ආයුබෝවන් 123")
        post.assert_not_called()

    def test_sinhala_with_supported_package_terms_uses_local_piper(self):
        audio = make_wav(sample_rate=22050)
        with (
            patch("gemini_service.piper_sinhala_tts.synthesize", return_value=audio) as piper_synthesize,
            patch("gemini_service.requests.post") as post,
        ):
            result_audio, mime_type = self.service.synthesize_speech(
                api_key="",
                text="SLT-MOBITEL Data Package එකක් 10 GB සහ 30 Mbps, Wi-Fi, Fiber 4G 5G",
            )

        self.assertEqual((result_audio, mime_type), (audio, "audio/wav"))
        piper_synthesize.assert_called_once_with(
            "එස් එල් ටී මොබිටෙල් ඩේටා පැකේජ් එකක් 10 ජී බී සහ 30 එම් බී පී එස්, "
            "වයි ෆයි, ෆයිබර් ෆෝ ජී ෆයිව් ජී"
        )
        post.assert_not_called()

    def test_unknown_english_terms_remain_on_existing_mixed_language_route(self):
        self.assertIsNone(normalize_sinhala_tts_text("මට superfast package එකක් ඕනේ"))

    def test_raw_pcm_audio_is_packaged_as_browser_playable_wav(self):
        pcm = b"\x00\x00" * 24
        audio, mime_type = _decode_tts_audio(
            base64.b64encode(pcm).decode("ascii"),
            "audio/l16",
        )

        self.assertEqual(mime_type, "audio/wav")
        with wave.open(io.BytesIO(audio), "rb") as wav_file:
            self.assertEqual(wav_file.getnchannels(), 1)
            self.assertEqual(wav_file.getsampwidth(), 2)
            self.assertEqual(wav_file.getframerate(), 24000)
            self.assertEqual(wav_file.readframes(24), pcm)

    def test_invalid_synthesis_audio_and_provider_errors_are_reported(self):
        with self.assertRaisesRegex(RuntimeError, "without a valid WAV header"):
            _decode_tts_audio(
                base64.b64encode(b"not wav data").decode("ascii"),
                "audio/wav",
            )

        failure = api_response(
            {"error": {"message": "provider details"}},
            status_code=429,
            headers={"Retry-After": "3"},
        )
        with patch("gemini_service.requests.post", return_value=failure):
            with self.assertRaisesRegex(RuntimeError, "provider details") as error:
                self.service.synthesize_speech(api_key="test-key", text="Hello")
        self.assertEqual(error.exception.retry_after, "3")

    def test_stt_429_preserves_provider_code_and_does_not_try_another_model(self):
        limited = api_response(
            {
                "error": {
                    "code": "too_many_requests",
                    "message": "Rate limit exceeded: limit 10 requests per day on Free Tier. Please try again later.",
                    "status": "RESOURCE_EXHAUSTED",
                }
            },
            status_code=429,
            headers={"Retry-After": "57315"},
        )
        responses = [
            api_response({}, headers={"X-Goog-Upload-URL": "https://upload.example"}),
            api_response({"file": {"uri": "files/audio", "name": "files/audio"}}),
            limited,
        ]
        with (
            patch("gemini_service.requests.post", side_effect=responses) as post,
            patch("gemini_service.requests.delete"),
        ):
            with self.assertRaises(RuntimeError) as error:
                self.service.transcribe_audio(
                    api_key="test-key",
                    audio_bytes=make_wav(),
                    mime_type="audio/wav",
                    filename="voice.wav",
                )

        self.assertEqual(error.exception.status_code, 429)
        self.assertEqual(error.exception.retry_after, "57315")
        self.assertEqual(error.exception.provider_code, "too_many_requests")
        self.assertEqual(error.exception.provider_status, "RESOURCE_EXHAUSTED")
        self.assertEqual(post.call_count, 3)
        self.assertEqual(post.call_args_list[-1].kwargs["json"]["model"], "gemini-3.5-transcribe")

    def test_synthesis_timeout_is_not_silently_fallbacked(self):
        with patch(
            "gemini_service.requests.post",
            side_effect=requests.Timeout("provider timeout"),
        ):
            with self.assertRaises(requests.Timeout):
                self.service.synthesize_speech(api_key="test-key", text="Hello")

    def test_invalid_timeout_configuration_is_rejected(self):
        with patch.dict(os.environ, {"GEMINI_TTS_TIMEOUT_SECONDS": "soon"}):
            with self.assertRaisesRegex(RuntimeError, "positive number"):
                self.service.synthesize_speech(api_key="test-key", text="Hello")


if __name__ == "__main__":
    unittest.main()
