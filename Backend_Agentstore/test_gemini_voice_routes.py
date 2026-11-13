import io
import os
import unittest
import wave
from unittest.mock import patch

from flask import Flask

import auth_routes
from gemini_service import GeminiAPIError


def make_wav():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * 16000)
    return buffer.getvalue()


def _skip_auth(_app):
    return lambda fn: fn


class GeminiVoiceRouteTests(unittest.TestCase):
    def setUp(self):
        self.real_require_auth = auth_routes.require_auth
        self.patches = [
            patch.object(auth_routes, "require_auth", side_effect=_skip_auth),
            patch.object(auth_routes, "init_db"),
            patch.object(auth_routes, "_mark_stale_running_ingestions"),
        ]
        for active_patch in self.patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)

        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        auth_routes.register_auth_routes(self.app, mail=None)
        self.client = self.app.test_client()

    def test_voice_readiness_checks_gemini_configuration(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            response = self.client.get("/api/auth/voice/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ready": True, "provider": "gemini"})

        with patch.dict(os.environ, {}, clear=True):
            response = self.client.get("/api/auth/voice/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["error"], "gemini_not_configured")

    def test_stt_preserves_contract_and_does_not_claim_confidence(self):
        transcript = "මට Fiber package එකක් ඕනේ."
        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}),
            patch.object(auth_routes.gemini_service, "transcribe_audio", return_value=transcript) as transcribe,
        ):
            response = self.client.post(
                "/api/auth/voice/stt",
                data={
                    "language": "auto",
                    "audio": (io.BytesIO(make_wav()), "voice.wav", "audio/wav"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {
                "text": transcript,
                "language": "mixed",
                "confidence": None,
                "low_confidence": True,
            },
        )
        self.assertEqual(transcribe.call_args.kwargs["mime_type"], "audio/wav")
        self.assertEqual(transcribe.call_args.kwargs["language_code"], None)

    def test_tts_returns_browser_playable_wav_and_selects_sinhala_voice(self):
        wav = make_wav()
        with (
            patch.dict(
                os.environ,
                {
                    "GEMINI_API_KEY": "test-key",
                    "GEMINI_TTS_SINHALA_VOICE": "sinhala-voice",
                },
            ),
            patch.object(
                auth_routes.gemini_service,
                "synthesize_speech",
                return_value=(wav, "audio/wav"),
            ) as synthesize,
        ):
            response = self.client.post(
                "/api/auth/voice/tts",
                json={"text": "මට 10 GB package ekak ඕනේ."},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "audio/wav")
        self.assertEqual(response.headers["X-Speech-Provider"], "gemini")
        self.assertEqual(response.data, wav)
        self.assertEqual(synthesize.call_args.kwargs["voice"], "sinhala-voice")

    def test_sinhala_tts_endpoint_returns_local_piper_wav_without_changing_contract(self):
        wav = make_wav()
        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}),
            patch(
                "gemini_service.piper_sinhala_tts.synthesize",
                return_value=wav,
            ) as piper_synthesize,
            patch("gemini_service.requests.post") as gemini_request,
        ):
            response = self.client.post(
                "/api/auth/voice/tts",
                json={"text": "ආයුබෝවන්."},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "audio/wav")
        self.assertEqual(response.headers["X-Speech-Provider"], "piper")
        self.assertEqual(response.data, wav)
        piper_synthesize.assert_called_once_with("ආයුබෝවන්.")
        gemini_request.assert_not_called()

    def test_sinhala_reply_with_package_terms_is_logged_and_routed_to_piper(self):
        from contextlib import redirect_stdout

        output = io.StringIO()
        wav = make_wav()
        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}),
            patch(
                "gemini_service.piper_sinhala_tts.synthesize",
                return_value=wav,
            ) as piper_synthesize,
            patch("gemini_service.requests.post") as gemini_request,
            redirect_stdout(output),
        ):
            response = self.client.post(
                "/api/auth/voice/tts",
                json={"text": "SLT-MOBITEL Data Package එකක් 10 GB ඕනේ."},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Speech-Provider"], "piper")
        self.assertIn("provider=piper language=mixed", output.getvalue())
        self.assertNotIn("Data Package", output.getvalue())
        piper_synthesize.assert_called_once_with("එස් එල් ටී මොබිටෙල් ඩේටා පැකේජ් එකක් 10 ජී බී ඕනේ.")
        gemini_request.assert_not_called()

    def test_tts_rate_limit_errors_are_user_friendly_and_do_not_leak_provider_details(self):
        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}),
            patch.object(
                auth_routes.gemini_service,
                "synthesize_speech",
                side_effect=GeminiAPIError(
                    "private provider detail",
                    status_code=429,
                    retry_after="2",
                ),
            ),
        ):
            response = self.client.post(
                "/api/auth/voice/tts",
                json={"text": "Hello."},
            )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.get_json()["error"], "gemini_rate_limited")
        self.assertEqual(response.headers["Retry-After"], "2")
        self.assertNotIn("private provider detail", response.get_data(as_text=True))

    def test_daily_tts_quota_is_classified_without_exposing_provider_details(self):
        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}),
            patch.object(
                auth_routes.gemini_service,
                "synthesize_speech",
                side_effect=GeminiAPIError(
                    "Rate limit exceeded: limit 10 requests per day on Free Tier.",
                    status_code=429,
                    retry_after="57315",
                    provider_code="too_many_requests",
                    provider_status="RESOURCE_EXHAUSTED",
                ),
            ),
        ):
            response = self.client.post("/api/auth/voice/tts", json={"text": "Hello."})

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.get_json()["error"], "gemini_daily_quota_exceeded")
        self.assertIn("daily speech generation quota", response.get_json()["message"])
        self.assertEqual(response.headers["Retry-After"], "57315")
        self.assertNotIn("Free Tier", response.get_data(as_text=True))

    def test_stt_rate_limit_response_includes_retry_after(self):
        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}),
            patch.object(
                auth_routes.gemini_service,
                "transcribe_audio",
                side_effect=GeminiAPIError(
                    "Rate limit exceeded: limit 10 requests per day.",
                    status_code=429,
                    retry_after="57315",
                ),
            ),
        ):
            response = self.client.post(
                "/api/auth/voice/stt",
                data={"audio": (io.BytesIO(make_wav()), "voice.wav", "audio/wav")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.get_json()["error"], "gemini_daily_quota_exceeded")
        self.assertEqual(response.headers["Retry-After"], "57315")

    def test_voice_endpoints_remain_authenticated(self):
        app = Flask("auth_required_voice_routes")
        app.config.update(TESTING=True, SECRET_KEY="test-secret")
        with (
            patch.object(auth_routes, "require_auth", new=self.real_require_auth),
            patch.object(auth_routes, "init_db"),
            patch.object(auth_routes, "_mark_stale_running_ingestions"),
        ):
            auth_routes.register_auth_routes(app, mail=None)

        response = app.test_client().get("/api/auth/voice/ready")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
