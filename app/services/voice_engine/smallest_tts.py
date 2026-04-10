import httpx
from app.config import get_settings
from app.services.voice_engine.http_clients import get_smallest_client


class SmallestTTS:

    BASE_URL = "https://waves-api.smallest.ai"

    # lightning-v3.1 voice catalog (subset — full list has 104 voices).
    # IMPORTANT: v3.1 voices are INCOMPATIBLE with v1/v2 and vice versa.
    VOICES = {
        # Female Hindi/English
        "maithili": "maithili", "advika": "advika", "aisha": "aisha",
        "ishani": "ishani", "yuvika": "yuvika", "sana": "sana",
        "divya": "divya", "avni": "avni", "kavya": "kavya",
        "sameera": "sameera", "sunidhi": "sunidhi", "srishti": "srishti",
        "sakshi": "sakshi", "chinmayi": "chinmayi",
        "zoya": "zoya", "aanya": "aanya",
        # Female English
        "avery": "avery", "mia": "mia", "sophia": "sophia",
        "rachel": "rachel", "olivia": "olivia",
        # Male Hindi/English
        "devansh": "devansh", "neel": "neel", "arjun": "arjun",
        "vivaan": "vivaan", "gaurav": "gaurav", "hitesh": "hitesh",
        "vaibhav": "vaibhav", "kunal": "kunal", "siddharth": "siddharth",
        "mohit": "mohit", "mihir": "mihir", "aarush": "aarush",
        "parth": "parth",
        # Male English
        "robert": "robert", "ethan": "ethan",
        # Legacy v1 voices (only work with model="lightning")
        "emily": "emily", "mithali": "mithali", "sarah": "sarah",
        "luna": "luna", "john": "john",
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
