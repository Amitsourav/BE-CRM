import io
import struct
import wave

import httpx
from app.config import get_settings
from app.services.voice_engine.http_clients import get_smallest_client, get_smallest_v3_client


def _wrap_pcm_as_wav(pcm_data: bytes, sample_rate: int = 8000, channels: int = 1, sample_width: int = 2) -> bytes:
    """Wrap raw PCM bytes in a WAV header.

    Smallest AI's v3.1 API returns raw PCM despite add_wav_header=True.
    Our pipeline expects WAV format for wav_to_mulaw conversion.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()


class SmallestTTS:

    # Old API (v1/v2 voices): waves-api.smallest.ai
    # New API (v3.1 voices):  api.smallest.ai
    BASE_URL_LEGACY = "https://waves-api.smallest.ai"
    BASE_URL_V3 = "https://api.smallest.ai"

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
        is_v3 = "v3" in model  # lightning-v3.1, lightning-v3, etc.
        try:
            if is_v3:
                # New API: api.smallest.ai/waves/v1/{model}/get_speech
                # v3.1 voices ONLY work on this endpoint
                client = get_smallest_v3_client()
                response = await client.post(
                    f"/waves/v1/{model}/get_speech",
                    headers={
                        "Authorization": f"Bearer {settings.smallest_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "text": text,
                        "voice_id": voice,
                        "speed": speed,
                        "sample_rate": 8000,
                        "add_wav_header": True,
                    },
                )
            else:
                # Legacy API: waves-api.smallest.ai/api/v1/lightning/get_speech
                # v1/v2 voices (mithali, emily, etc.)
                client = get_smallest_client()
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
            content = response.content
            if not content or len(content) < 100:
                return b""
            # v3.1 API returns raw PCM despite add_wav_header=True.
            # Wrap in WAV header if not already RIFF format.
            if content[:4] != b"RIFF":
                content = _wrap_pcm_as_wav(content, sample_rate=8000)
            return content
        except httpx.RequestError:
            return b""


smallest_tts = SmallestTTS()
