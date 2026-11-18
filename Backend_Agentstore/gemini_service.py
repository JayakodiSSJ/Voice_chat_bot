from __future__ import annotations

import base64
import binascii
import io
import os
import random
import re
import time
import wave

import requests
from dotenv import load_dotenv

from piper_tts_service import piper_english_tts, piper_sinhala_tts

load_dotenv()

TRANSCRIBE_MODEL_DEFAULT = "gemini-3.5-transcribe"
TTS_MODEL_DEFAULT = "gemini-3.8-flash-lite-tts"
TTS_VOICE_DEFAULT = "Aoede"
MAX_AUDIO_BYTES = 5 * 1024 * 1024
MAX_AUDIO_DURATION_SECONDS = 30
CUSTOM_VOCABULARY = ("SLT-MOBITEL", "Fiber", "Wi-Fi", "GB", "Mbps", "4G", "5G")

GENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
GENAI_UPLOAD_URL = "https://generativelanguage.googleapis.com/upload/v1beta/files"

GENERIC_CONTENT_TYPES = {"", "application/octet-stream", "binary/octet-stream"}
EXTENSION_MIME_TYPES = {
    "webm": "audio/webm",
    "wav": "audio/wav",
    "ogg": "audio/ogg",
    "oga": "audio/ogg",
    "mp3": "audio/mp3",
    "m4a": "audio/m4a",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "aiff": "audio/aiff",
}


def _configured_value(env_name: str, default: str) -> str:
    return os.getenv(env_name, "").strip() or default


def _response_json(response) -> dict:
    try:
        data = response.json()
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {"raw": response.text[:2000]}


def _api_error(data: dict, fallback: str) -> str:
    error = data.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if message:
            return str(message)
    return fallback


def _resolve_audio_mime_type(mime_type: str | None, filename: str | None) -> str:
    normalized = (mime_type or "").split(";")[0].strip().lower()
    if normalized and normalized not in GENERIC_CONTENT_TYPES:
        return normalized
    extension = (filename or "").rsplit(".", 1)[-1].lower() if filename and "." in filename else ""
    return EXTENSION_MIME_TYPES.get(extension, "audio/webm")


def _timeout_seconds(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive number of seconds.") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive number of seconds.")
    return value


def _validate_wav_audio(audio_bytes: bytes, mime_type: str) -> None:
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise ValueError("The audio recording exceeds the 5 MB limit.")
    if mime_type not in {"audio/wav", "audio/x-wav", "audio/wave"}:
        raise ValueError("Upload a PCM WAV audio recording.")
    if len(audio_bytes) < 44 or audio_bytes[:4] != b"RIFF" or audio_bytes[8:12] != b"WAVE":
        raise ValueError("The audio recording is not a valid WAV file.")

    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav_file:
            if (
                wav_file.getcomptype() != "NONE"
                or wav_file.getsampwidth() != 2
                or wav_file.getnchannels() != 1
                or wav_file.getframerate() <= 0
            ):
                raise ValueError("Audio must be mono, uncompressed 16-bit PCM WAV.")
            duration = wav_file.getnframes() / wav_file.getframerate()
    except (wave.Error, EOFError) as exc:
        raise ValueError("The audio recording is not a valid WAV file.") from exc

    if duration <= 0:
        raise ValueError("The audio recording contains no speech.")
    if duration > MAX_AUDIO_DURATION_SECONDS:
        raise ValueError("The audio recording exceeds the 30 second limit.")


def detect_transcript_language(text: str) -> str | None:
    has_sinhala = any("\u0d80" <= char <= "\u0dff" for char in text)
    has_tamil = any("\u0b80" <= char <= "\u0bff" for char in text)
    has_latin = any(("a" <= char.lower() <= "z") for char in text)
    if has_sinhala and has_tamil:
        return "mixed"
    if has_sinhala:
        return "mixed" if has_latin else "si-LK"
    if has_tamil:
        return "mixed" if has_latin else "ta-LK"
    if has_latin:
        return "en-US"
    return None


_SINHALA_TTS_LATIN_WORDS = {
    "4g": "ෆෝ ජී",
    "5g": "ෆයිව් ජී",
    "connection": "කනෙක්ෂන්",
    "data": "ඩේටා",
    "fiber": "ෆයිබර්",
    "gb": "ජී බී",
    "internet": "ඉන්ටර්නෙට්",
    "mbps": "එම් බී පී එස්",
    "mobitel": "මොබිටෙල්",
    "package": "පැකේජ්",
    "slt": "එස් එල් ටී",
    "slt-mobitel": "එස් එල් ටී මොබිටෙල්",
    "wi-fi": "වයි ෆයි",
    "wifi": "වයි ෆයි",
}


def normalize_sinhala_tts_text(text: str) -> str | None:
    has_sinhala = any("\u0d80" <= char <= "\u0dff" for char in text)
    if not has_sinhala:
        return None

    def replace_term(match: re.Match[str]) -> str:
        term = match.group(0)
        if term.isdigit():
            return term
        return _SINHALA_TTS_LATIN_WORDS.get(term.lower(), term)

    normalized_text = re.sub(
        r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*",
        replace_term,
        text,
    )
    if any(
        char.isalpha() and not "\u0d80" <= char <= "\u0dff"
        for char in normalized_text
    ):
        return None
    return normalized_text


def english_tts_provider() -> str:
    """Return the configured English TTS provider: local "piper" (default) or "gemini"."""
    provider = os.getenv("ENGLISH_TTS_PROVIDER", "").strip().lower()
    return provider if provider in {"piper", "gemini"} else "piper"


def is_english_only_text(text: str) -> bool:
    has_sinhala = any("඀" <= char <= "෿" for char in text)
    has_tamil = any("஀" <= char <= "௿" for char in text)
    has_latin = any(char.isascii() and char.isalpha() for char in text)
    return has_latin and not has_sinhala and not has_tamil


def select_tts_provider(text: str) -> str:
    """Pick the speech engine for a reply: "piper" for local voices, otherwise "gemini"."""
    if normalize_sinhala_tts_text(text) is not None:
        return "piper"
    if is_english_only_text(text) and english_tts_provider() == "piper":
        return "piper"
    return "gemini"


def _decode_tts_audio(audio_data: str, mime_type: str | None) -> tuple[bytes, str]:
    try:
        audio_bytes = base64.b64decode(audio_data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError("Gemini returned audio in an invalid encoding.") from exc

    normalized_mime_type = (mime_type or "audio/wav").split(";")[0].strip().lower()
    if normalized_mime_type in {"audio/wav", "audio/x-wav"}:
        if audio_bytes[:4] != b"RIFF" or audio_bytes[8:12] != b"WAVE":
            raise RuntimeError("Gemini returned WAV audio without a valid WAV header.")
        return audio_bytes, "audio/wav"

    if normalized_mime_type in {"audio/l16", "audio/pcm"}:
        if not audio_bytes or len(audio_bytes) % 2:
            raise RuntimeError("Gemini returned invalid 16-bit PCM audio.")
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(24000)
            wav_file.writeframes(audio_bytes)
        return wav_buffer.getvalue(), "audio/wav"

    raise RuntimeError("Gemini returned an unsupported audio format.")


def _interaction_error(response, data: dict) -> str | None:
    if not response.ok or data.get("status") in {"failed", "cancelled", "incomplete"}:
        return _api_error(data, f"Gemini transcription failed with status {response.status_code}.")
    return None


class GeminiAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: str | None = None,
        provider_code: str | int | None = None,
        provider_status: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.provider_code = provider_code
        self.provider_status = provider_status


def _provider_error_fields(data: dict) -> dict:
    error = data.get("error")
    if not isinstance(error, dict):
        return {}
    return {
        "provider_code": error.get("code"),
        "provider_status": error.get("status"),
    }


def _gemini_api_error(response, data: dict, fallback: str) -> GeminiAPIError:
    return GeminiAPIError(
        _api_error(data, fallback),
        status_code=getattr(response, "status_code", None),
        retry_after=getattr(response, "headers", {}).get("Retry-After"),
        **_provider_error_fields(data),
    )


def _is_transient_model_error(response, message: str) -> bool:
    normalized = message.lower()
    status_code = getattr(response, "status_code", 0)
    if status_code == 429 or any(
        phrase in normalized
        for phrase in ("quota", "rate limit", "too_many_requests", "per day", "resource_exhausted")
    ):
        return False
    return status_code in {408, 500, 502, 503, 504} or any(
        phrase in normalized
        for phrase in (
            "high demand", "spikes in demand", "temporarily unavailable", "try again later",
            "overloaded", "capacity",
        )
    )


def _post_interaction_with_retry(api_key: str, request_body: dict, *, timeout: int):
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    for attempt in range(2):
        try:
            response = requests.post(
                f"{GENAI_BASE_URL}/interactions",
                headers=headers,
                json=request_body,
                timeout=timeout,
            )
        except requests.RequestException:
            if attempt == 1:
                raise
            time.sleep(0.8 + random.uniform(0.0, 0.4))
            continue

        data = _response_json(response)
        failure = _interaction_error(response, data)
        if not failure or not _is_transient_model_error(response, failure) or attempt == 1:
            return response, data, failure

        retry_after = response.headers.get("Retry-After", "")
        try:
            delay = min(max(float(retry_after), 0.5), 4.0)
        except (TypeError, ValueError):
            delay = min(0.8 * (2 ** attempt) + random.uniform(0.0, 0.4), 4.0)
        time.sleep(delay)

    return None, {}, "Gemini request could not be completed."


class GeminiService:
    @staticmethod
    def is_configured() -> bool:
        return bool(os.getenv("GEMINI_API_KEY", "").strip())

    def transcribe_audio(
        self,
        *,
        api_key: str,
        audio_bytes: bytes,
        mime_type: str,
        filename: str | None = None,
        language_code: str | None = None,
    ) -> str:
        if not audio_bytes:
            raise ValueError("The audio recording is empty.")

        normalized_mime_type = _resolve_audio_mime_type(mime_type, filename)
        _validate_wav_audio(audio_bytes, normalized_mime_type)
        model = _configured_value("GEMINI_TRANSCRIBE_MODEL", TRANSCRIBE_MODEL_DEFAULT)
        display_name = (filename or "voice-input")[:512]
        upload_timeout = _timeout_seconds("GEMINI_UPLOAD_TIMEOUT_SECONDS", 30)

        upload_start = requests.post(
            GENAI_UPLOAD_URL,
            headers={
                "x-goog-api-key": api_key,
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(len(audio_bytes)),
                "X-Goog-Upload-Header-Content-Type": normalized_mime_type,
                "Content-Type": "application/json",
            },
            json={"file": {"display_name": display_name}},
            timeout=upload_timeout,
        )
        upload_start_data = _response_json(upload_start)
        if not upload_start.ok:
            raise _gemini_api_error(
                upload_start,
                upload_start_data,
                f"Gemini audio upload failed with status {upload_start.status_code}.",
            )

        upload_url = upload_start.headers.get("X-Goog-Upload-URL")
        if not upload_url:
            raise RuntimeError("Gemini audio upload did not return an upload URL.")

        uploaded = requests.post(
            upload_url,
            headers={
                "Content-Length": str(len(audio_bytes)),
                "X-Goog-Upload-Offset": "0",
                "X-Goog-Upload-Command": "upload, finalize",
                "Content-Type": normalized_mime_type,
            },
            data=audio_bytes,
            timeout=upload_timeout,
        )
        uploaded_data = _response_json(uploaded)
        if not uploaded.ok:
            raise _gemini_api_error(
                uploaded,
                uploaded_data,
                f"Gemini audio upload failed with status {uploaded.status_code}.",
            )

        file_data = uploaded_data.get("file") if isinstance(uploaded_data.get("file"), dict) else uploaded_data
        file_uri = file_data.get("uri")
        file_name = file_data.get("name")
        if not file_uri:
            raise RuntimeError("Gemini audio upload did not return a file URI.")

        try:
            request_body = {
                "model": model,
                "input": [
                    {
                        "type": "audio",
                        "uri": file_uri,
                        "mime_type": normalized_mime_type,
                    }
                ],
            }
            if language_code:
                request_body["generation_config"] = {
                    "transcription_config": {
                        "language_codes": [language_code],
                        "custom_vocabulary": list(CUSTOM_VOCABULARY),
                    }
                }
            else:
                request_body["generation_config"] = {
                    "transcription_config": {
                        "language_codes": [],
                        "custom_vocabulary": list(CUSTOM_VOCABULARY),
                    }
                }

            response, data, failure = _post_interaction_with_retry(
                api_key,
                request_body,
                timeout=_timeout_seconds("GEMINI_STT_TIMEOUT_SECONDS", 90),
            )
            if failure and response is not None and response.status_code == 429:
                raise _gemini_api_error(
                    response,
                    data,
                    f"Gemini transcription failed with status {response.status_code}.",
                )
            if failure:
                raise _gemini_api_error(
                    response,
                    data,
                    f"Gemini transcription failed with status {getattr(response, 'status_code', 'unknown')}.",
                )

            transcript_parts = []
            for step in data.get("steps") or []:
                if step.get("type") != "model_output":
                    continue
                for part in step.get("content") or []:
                    if part.get("type") in {"text", "transcription"} and part.get("text"):
                        transcript_parts.append(part["text"].strip())
            return "\n".join(part for part in transcript_parts if part).strip()
        finally:
            if file_name:
                try:
                    requests.delete(
                        f"{GENAI_BASE_URL}/{file_name}",
                        headers={"x-goog-api-key": api_key},
                        timeout=15,
                    )
                except requests.RequestException as exc:
                    print(f"WARN Gemini temporary audio cleanup failed: {exc}", flush=True)

    def synthesize_speech(
        self,
        *,
        api_key: str,
        text: str,
        voice: str | None = None,
    ) -> tuple[bytes, str]:
        spoken_text = (text or "").strip()
        if not spoken_text:
            raise ValueError("Text is required for speech synthesis.")
        piper_text = normalize_sinhala_tts_text(spoken_text)
        if piper_text is not None:
            return piper_sinhala_tts.synthesize(piper_text), "audio/wav"
        if is_english_only_text(spoken_text) and english_tts_provider() == "piper":
            return piper_english_tts.synthesize(spoken_text), "audio/wav"

        model =_configured_value("GEMINI_TTS_MODEL", TTS_MODEL_DEFAULT)
        chosen_voice = voice or _configured_value("GEMINI_TTS_VOICE", TTS_VOICE_DEFAULT)
        timeout = _timeout_seconds("GEMINI_TTS_TIMEOUT_SECONDS", 90)
        response = requests.post(
            f"{GENAI_BASE_URL}/interactions",
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={
                "model": model,
                "input": [
                    {
                        "type": "user_input",
                        "content": [{"type": "text", "text": spoken_text}],
                    }
                ],
                "response_format": {"type": "audio"},
                "generation_config": {"speech_config": [{"voice": chosen_voice}]},
            },
            timeout=timeout,
        )
        data = _response_json(response)
        if not response.ok:
            raise _gemini_api_error(
                response,
                data,
                f"Gemini speech synthesis failed with status {response.status_code}.",
            )
        if data.get("status") in {"failed", "cancelled", "incomplete"}:
            raise RuntimeError(_api_error(data, "Gemini could not complete speech synthesis."))

        output_audio = data.get("output_audio")
        if isinstance(output_audio, dict) and output_audio.get("data"):
            return _decode_tts_audio(
                output_audio["data"],
                output_audio.get("mime_type"),
            )

        audio_parts = [
            part
            for step in data.get("steps") or []
            if step.get("type") == "model_output"
            for part in step.get("content") or []
            if part.get("type") == "audio" and part.get("data")
        ]
        if audio_parts:
            # The REST response can contain multiple blocks; use its final audio block.
            part = audio_parts[-1]
            return _decode_tts_audio(part["data"], part.get("mime_type"))
        raise RuntimeError("Gemini speech response did not include audio output.")


gemini_service = GeminiService()