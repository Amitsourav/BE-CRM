import logging

import httpx

from app.config import get_settings
from app.services.language_detector import detect_language
from app.services.voice_engine.http_clients import get_openai_client

logger = logging.getLogger(__name__)


class OpenAISTT:
    """OpenAI Whisper STT — /v1/audio/transcriptions.

    No streaming support from OpenAI yet (as of writing); batch only.
    """

    BASE_URL = "https://api.openai.com/v1/audio/transcriptions"

    async def transcribe(
        self,
        audio_bytes: bytes,
        language_code: str = "en",
        model: str = "whisper-1",
        keywords: str = "",
    ) -> dict:
        settings = get_settings()
        if not settings.openai_api_key:
            logger.warning("openai_api_key missing — returning empty transcript")
            return {"transcript": "", "language_code": language_code, "detected_language": "en"}

        # OpenAI expects ISO-639-1 codes (en, hi); normalize "en-IN" -> "en"
        lang = (language_code or "en").split("-")[0]

        try:
            client = get_openai_client()
            response = await client.post(
                "/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                files={"file": ("audio.wav", audio_bytes, "audio/wav")},
                data={
                    "model": model,
                    "language": lang,
                    "response_format": "json",
                    **({"prompt": keywords} if keywords else {}),
                },
            )
            if response.status_code != 200:
                logger.warning("openai STT failed: %s %s", response.status_code, response.text[:200])
                return {"transcript": "", "language_code": language_code, "detected_language": "en"}
            data = response.json()
            transcript = (data.get("text") or "").strip()
            return {
                "transcript": transcript,
                "language_code": language_code,
                "detected_language": detect_language(transcript),
            }
        except (httpx.RequestError, httpx.TimeoutException) as e:
            logger.warning("openai STT error: %s", e)
            return {"transcript": "", "language_code": language_code, "detected_language": "en"}

    async def transcribe_stream(
        self,
        audio_bytes: bytes,
        timeout_seconds: float = 8.0,
        model: str = "whisper-1",
        keywords: str = "",
    ) -> dict:
        return await self.transcribe(audio_bytes, model=model, keywords=keywords)


openai_stt = OpenAISTT()
