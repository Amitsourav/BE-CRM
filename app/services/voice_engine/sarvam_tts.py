import base64
import logging

import httpx

from app.config import get_settings
from app.services.voice_engine.audio_utils import concat_wav, split_for_tts
from app.services.voice_engine.http_clients import get_sarvam_client

logger = logging.getLogger(__name__)

# Safe default voice for bulbul:v3. Used as automatic fallback when the
# agent's configured voice is rejected by Sarvam (HTTP 400) so calls don't
# go silent mid-turn. MUST be a speaker that Sarvam's current model
# accepts — otherwise the fallback itself 400s and calls stay silent.
#
# Confirmed working via Agent 3 production calls (TURN_TIMING logs show
# non-zero tts_first_sentence_ms). bulbul:v3 valid speakers from Sarvam's
# own 400 error: aditya, ritu, ashutosh, priya, neha, rahul, pooja, rohan,
# simran, kavya, amit, dev, ...
_SAFE_DEFAULT_VOICE = "simran"


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
        client = get_sarvam_client()

        async def _call_sarvam(chunk_text: str, speaker: str) -> tuple[int, dict | str]:
            try:
                r = await client.post(
                    "/text-to-speech",
                    headers={
                        "api-subscription-key": settings.sarvam_api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "inputs": [chunk_text],
                        "target_language_code": language_code,
                        "speaker": speaker,
                        "model": model,
                        "enable_preprocessing": True,
                        "speech_sample_rate": 8000,
                        "encoding": "wav",
                        "pace": speed,
                    },
                )
            except (httpx.RequestError, httpx.TimeoutException) as e:
                return 0, str(e)
            try:
                return r.status_code, r.json() if r.status_code == 200 else r.text
            except ValueError:
                return r.status_code, r.text

        wav_blobs: list = []
        fallback_voice_tried = False
        current_voice = voice

        for chunk in chunks:
            status, body = await _call_sarvam(chunk, current_voice)

            # If the configured voice is rejected (bad voice name, unsupported
            # on this model version, etc.), retry this chunk once with the
            # safe default voice. Prevents the entire call from going silent
            # due to one misconfigured dashboard field.
            if status == 400 and current_voice != _SAFE_DEFAULT_VOICE:
                logger.warning(
                    "sarvam TTS 400 for voice=%r model=%r lang=%r body=%r — "
                    "retrying with fallback voice=%r",
                    current_voice, model, language_code, str(body)[:200],
                    _SAFE_DEFAULT_VOICE,
                )
                current_voice = _SAFE_DEFAULT_VOICE
                fallback_voice_tried = True
                status, body = await _call_sarvam(chunk, current_voice)

            if status != 200:
                logger.warning(
                    "sarvam TTS failed status=%s voice=%r body=%r",
                    status, current_voice, str(body)[:200],
                )
                if status in (401, 403, 429):
                    break
                continue

            audios = body.get("audios", []) if isinstance(body, dict) else []
            if not audios:
                continue
            try:
                wav_blobs.append(base64.b64decode(audios[0]))
            except Exception:
                continue

        if fallback_voice_tried:
            logger.info("sarvam TTS served via fallback voice %r", _SAFE_DEFAULT_VOICE)

        return concat_wav(wav_blobs)

    async def synthesize_streaming(
        self,
        text: str,
        voice: str = "simran",
        language_code: str = "hi-IN",
        speed: float = 1.0,
        model: str = "bulbul:v3",
    ):
        """Async generator yielding one WAV blob per sub-chunk, in order.

        Splits text into ~120 char chunks (instead of 450 for batch) so
        the FIRST chunk returns in ~500-700ms instead of ~1500ms for the
        full text. Plivo starts playing while later chunks are still being
        synthesized — user hears audio ~800ms sooner.

        Uses the persistent httpx client and voice fallback.
        """
        if not text or not text.strip():
            return

        # Smaller chunks for streaming → first audio arrives faster
        chunks = split_for_tts(text, max_chars=120)
        if not chunks:
            return

        settings = get_settings()
        client = get_sarvam_client()
        current_voice = voice

        for chunk in chunks:
            try:
                response = await client.post(
                    "/text-to-speech",
                    headers={
                        "api-subscription-key": settings.sarvam_api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "inputs": [chunk],
                        "target_language_code": language_code,
                        "speaker": current_voice,
                        "model": model,
                        "enable_preprocessing": True,
                        "speech_sample_rate": 8000,
                        "encoding": "wav",
                        "pace": speed,
                    },
                )
                # Voice fallback on 400
                if response.status_code == 400 and current_voice != _SAFE_DEFAULT_VOICE:
                    current_voice = _SAFE_DEFAULT_VOICE
                    response = await client.post(
                        "/text-to-speech",
                        headers={
                            "api-subscription-key": settings.sarvam_api_key,
                            "Content-Type": "application/json",
                        },
                        json={
                            "inputs": [chunk],
                            "target_language_code": language_code,
                            "speaker": current_voice,
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


    async def synthesize_for_call_streaming(
        self,
        text: str,
        agent,
        language: str = "en",
    ):
        """Streaming variant of synthesize_for_call. Yields WAV chunks
        per sub-sentence so pipeline can send audio to Plivo as each
        chunk is ready instead of waiting for the entire TTS batch."""
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

        async for wav in self.synthesize_streaming(
            text=text,
            voice=voice,
            language_code=lang_code,
            speed=agent.tts_speed or 1.0,
            model=model,
        ):
            yield wav


sarvam_tts = SarvamTTS()
