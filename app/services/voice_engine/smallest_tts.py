import httpx
from app.config import get_settings
from app.services.voice_engine.http_clients import get_smallest_client


class SmallestTTS:

    BASE_URL = "https://waves-api.smallest.ai"

    VOICES = {
        "emily": "emily",
        "sarah": "sarah",
        "luna": "luna",
        "john": "john",
        "mithali": "mithali",
    }

    async def synthesize(
        self,
        text: str,
        voice: str = "emily",
        speed: float = 1.0,
        model: str = "lightning-v2",
    ) -> bytes:
        """Convert text to speech via Smallest AI. Best for English."""
        if not text or not text.strip():
            return b""

        settings = get_settings()
        client = get_smallest_client()
        try:
            response = await client.post(
                "/api/v1/lightning/get_speech",
                headers={
                    "Authorization": f"Bearer {settings.smallest_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "text": text,
                    "voice_id": voice,
                    "speed": speed,
                    "model": model,
                    "sample_rate": 8000,
                    "add_wav_header": True,
                },
            )
            if response.status_code != 200:
                return b""
            return response.content
        except httpx.RequestError:
            return b""


smallest_tts = SmallestTTS()
