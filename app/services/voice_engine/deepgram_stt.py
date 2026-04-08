import logging

import httpx

from app.config import get_settings
from app.services.language_detector import detect_language

logger = logging.getLogger(__name__)


class DeepgramSTT:
    """Deepgram Nova-2 STT — batch (prerecorded) endpoint.

    Streaming would be faster but requires a second WS to the provider and
    back-pressure handling. Batch is good enough to match current Sarvam path.
    """

    BASE_URL = "https://api.deepgram.com/v1/listen"

    async def transcribe(
        self,
        audio_bytes: bytes,
        language_code: str = "en-IN",
        model: str = "nova-2-general",
    ) -> dict:
        settings = get_settings()
        if not settings.deepgram_api_key:
            logger.warning("deepgram_api_key missing — returning empty transcript")
            return {"transcript": "", "language_code": language_code, "detected_language": "en"}

        try:
            params = {
                "model": model,
                "language": language_code,
                "smart_format": "true",
                "punctuate": "true",
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    self.BASE_URL,
                    params=params,
                    headers={
                        "Authorization": f"Token {settings.deepgram_api_key}",
                        "Content-Type": "audio/wav",
                    },
                    content=audio_bytes,
                )
                if response.status_code != 200:
                    logger.warning("deepgram STT failed: %s %s", response.status_code, response.text[:200])
                    return {"transcript": "", "language_code": language_code, "detected_language": "en"}
                data = response.json()
                transcript = (
                    data.get("results", {})
                    .get("channels", [{}])[0]
                    .get("alternatives", [{}])[0]
                    .get("transcript", "")
                    or ""
                ).strip()
                return {
                    "transcript": transcript,
                    "language_code": language_code,
                    "detected_language": detect_language(transcript),
                }
        except (httpx.RequestError, httpx.TimeoutException) as e:
            logger.warning("deepgram STT error: %s", e)
            return {"transcript": "", "language_code": language_code, "detected_language": "en"}

    # Match sarvam_stt interface — no real streaming here, delegate to batch
    async def transcribe_stream(
        self,
        audio_bytes: bytes,
        timeout_seconds: float = 8.0,
        model: str = "nova-2-general",
    ) -> dict:
        return await self.transcribe(audio_bytes, model=model)


deepgram_stt = DeepgramSTT()
