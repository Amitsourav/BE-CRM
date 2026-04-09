import base64
import httpx
from app.config import get_settings
from app.services.voice_engine.audio_utils import concat_wav, split_for_tts
from app.services.voice_engine.http_clients import get_sarvam_client


class SarvamTTS:

    BASE_URL = "https://api.sarvam.ai"

    VOICES = {
        "simran": "simran",
        "anushka": "anushka",
        "priya": "priya",
        "pooja": "pooja",
        "ishita": "ishita",
        "shreya": "shreya",
        "arjun": "arjun",
        "rahul": "rahul",
        "aditya": "aditya",
    }

    async def synthesize(
        self,
        text: str,
        voice: str = "simran",
        language_code: str = "hi-IN",
        speed: float = 1.0,
        model: str = "bulbul:v3",
    ) -> bytes:
        """Convert text to speech (wav bytes). Splits long text on sentence boundaries."""
        if not text or not text.strip():
            return b""

        chunks = split_for_tts(text, max_chars=450)
        if not chunks:
            return b""

        settings = get_settings()
        wav_blobs: list = []
        client = get_sarvam_client()

        try:
            for chunk in chunks:
                response = await client.post(
                    "/text-to-speech",
                    headers={
                        "api-subscription-key": settings.sarvam_api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "inputs": [chunk],
                        "target_language_code": language_code,
                        "speaker": voice,
                        "model": model,
                        "enable_preprocessing": True,
                        "speech_sample_rate": 8000,
                        "encoding": "wav",
                        "pace": speed,
                    },
                )
                if response.status_code != 200:
                    if response.status_code in (401, 403, 429):
                        break
                    continue
                try:
                    data = response.json()
                except ValueError:
                    continue
                audios = data.get("audios", []) if isinstance(data, dict) else []
                if not audios:
                    continue
                try:
                    wav_blobs.append(base64.b64decode(audios[0]))
                except Exception:
                    continue
        except (httpx.RequestError, httpx.TimeoutException):
            pass

        return concat_wav(wav_blobs)

    async def synthesize_streaming(
        self,
        text: str,
        voice: str = "simran",
        language_code: str = "hi-IN",
        speed: float = 1.0,
        model: str = "bulbul:v3",
    ):
        """Async generator yielding one WAV blob per sentence, in order.

        Lets the WS handler stream the first sentence to Plivo while later
        sentences are still being synthesized — cuts perceived latency
        from "wait for full TTS" to "wait for first sentence".
        """
        if not text or not text.strip():
            return

        chunks = split_for_tts(text, max_chars=450)
        if not chunks:
            return

        settings = get_settings()
        async with httpx.AsyncClient(timeout=30.0) as client:
            for chunk in chunks:
                try:
                    response = await client.post(
                        f"{self.BASE_URL}/text-to-speech",
                        headers={
                            "api-subscription-key": settings.sarvam_api_key,
                            "Content-Type": "application/json",
                        },
                        json={
                            "inputs": [chunk],
                            "target_language_code": language_code,
                            "speaker": voice,
                            "model": model,
                            "enable_preprocessing": True,
                            "speech_sample_rate": 8000,
                            "encoding": "wav",
                            "pace": speed,
                        },
                    )
                    if response.status_code != 200:
                        if response.status_code in (401, 403, 429):
                            return
                        continue
                    try:
                        data = response.json()
                    except ValueError:
                        continue
                    audios = data.get("audios", []) if isinstance(data, dict) else []
                    if not audios:
                        continue
                    try:
                        wav = base64.b64decode(audios[0])
                        if wav:
                            yield wav
                    except Exception:
                        continue
                except (httpx.RequestError, httpx.TimeoutException):
                    continue

    async def synthesize_for_call(
        self,
        text: str,
        agent,
        language: str = "en",
    ) -> bytes:
        """Smart synthesize using dual TTS config if available."""
        if (
            language == "hi"
            and getattr(agent, "tts_provider_hindi", None) == "sarvam"
            and getattr(agent, "tts_voice_hindi", None)
        ):
            voice = agent.tts_voice_hindi
            model = agent.tts_model_hindi or "bulbul:v3"
            lang_code = "hi-IN"
        elif (
            language == "en"
            and getattr(agent, "tts_provider_english", None) == "sarvam"
            and getattr(agent, "tts_voice_english", None)
        ):
            voice = agent.tts_voice_english
            model = agent.tts_model_english or "bulbul:v3"
            lang_code = "en-IN"
        else:
            voice = agent.tts_voice or "simran"
            model = agent.tts_model or "bulbul:v3"
            lang_code = "hi-IN" if language == "hi" else "en-IN"

        return await self.synthesize(
            text=text,
            voice=voice,
            language_code=lang_code,
            speed=agent.tts_speed or 1.0,
            model=model,
        )


sarvam_tts = SarvamTTS()
