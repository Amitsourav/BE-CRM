"""STT provider router.

Selects the correct STT backend based on agent.stt_provider. All backends
expose the same interface:

    async def transcribe_stream(audio_bytes, model=..., timeout_seconds=...)
        -> {"transcript": str, "language_code": str, "detected_language": str}
"""
import logging

from app.services.voice_engine.sarvam_stt import sarvam_stt
from app.services.voice_engine.deepgram_stt import deepgram_stt
from app.services.voice_engine.openai_stt import openai_stt

logger = logging.getLogger(__name__)


# Sensible default model per provider when agent.stt_model is empty
_DEFAULT_MODELS = {
    "sarvam": "saaras:v3",
    "deepgram": "nova-2-general",
    "openai": "whisper-1",
}


def get_stt_for_agent(agent):
    """Return (stt_instance, model_name) for the given agent.

    Falls back to Sarvam if agent.stt_provider is unknown or missing —
    safer than crashing a live call for a bad config field.
    """
    provider = (getattr(agent, "stt_provider", None) or "sarvam").lower()
    model = getattr(agent, "stt_model", None) or _DEFAULT_MODELS.get(provider, "saaras:v3")

    if provider == "deepgram":
        return deepgram_stt, model
    if provider == "openai":
        return openai_stt, model
    if provider != "sarvam":
        logger.warning("unknown stt_provider=%r, falling back to sarvam", provider)
    return sarvam_stt, model
