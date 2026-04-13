"""Pre-generated filler sounds for natural conversation pacing.

Plays a random short "thinking" sound (Hmm, Achha, Haan, Dekho)
immediately after STT completes, BEFORE the LLM starts processing.
Eliminates the 1-2s dead silence that makes the agent sound robotic.

Usage in pipeline:
    filler_wav = await get_filler_sound(agent)
    yield {"audio": filler_wav, "filler": True, ...}
    # Then continue with LLM → TTS as normal
"""
import asyncio
import logging
import random
from typing import Optional

logger = logging.getLogger(__name__)

# Language-neutral fillers that sound natural in BOTH English and Hindi.
# "Hmm" is universal — works in any language without sounding odd.
# Avoided: "Haan", "Ji", "ek second", "dekho" — these sound jarring
# when the agent replies in English.
SHORT_FILLER_PHRASES = [
    "Hmm.",
    "Hmm.",
    "Okay.",
]

LONG_FILLER_PHRASES = [
    "Hmm, one second.",
    "Hmm, let me check.",
    "Okay, so.",
]

# Module-level cache: {(tts_provider, tts_voice, tts_model, "short"|"long"): [wav_bytes, ...]}
# Cache clears on deploy (new process). Regenerates on first call.
_filler_cache: dict[tuple, list[bytes]] = {}
_cache_lock = asyncio.Lock()


async def _generate_fillers(phrases: list, provider: str, voice: str, model: str) -> list[bytes]:
    """Generate TTS audio for a list of filler phrases."""
    wavs = []
    try:
        if provider == "smallest":
            from app.services.voice_engine.smallest_tts import smallest_tts
            for phrase in phrases:
                try:
                    wav = await asyncio.wait_for(
                        smallest_tts.synthesize(text=phrase, voice=voice, model=model),
                        timeout=5.0,
                    )
                    if wav and len(wav) > 500:
                        wavs.append(wav)
                except Exception as e:
                    logger.warning("filler gen failed for '%s': %s", phrase, e)
        else:
            from app.services.voice_engine.sarvam_tts import sarvam_tts
            for phrase in phrases:
                try:
                    wav = await asyncio.wait_for(
                        sarvam_tts.synthesize(text=phrase, voice=voice, model=model),
                        timeout=5.0,
                    )
                    if wav and len(wav) > 500:
                        wavs.append(wav)
                except Exception as e:
                    logger.warning("filler gen failed for '%s': %s", phrase, e)
    except Exception as e:
        logger.warning("filler generation failed: %s", e)
    return wavs


async def get_filler_sound(agent, long: bool = False) -> Optional[bytes]:
    """Return a random pre-generated filler WAV.

    long=False: short neutral filler ("Haan...", ~0.3s) for simple replies
    long=True:  longer thinking filler ("Hmm, ek second...", ~1.2s) for complex questions

    First call generates both sets and caches them.
    """
    provider = (getattr(agent, "tts_provider", "smallest") or "smallest").lower()
    voice = getattr(agent, "tts_voice", "sana") or "sana"
    model = getattr(agent, "tts_model", "lightning-v3.1") or "lightning-v3.1"
    ftype = "long" if long else "short"
    cache_key = (provider, voice, model, ftype)

    # Fast path: already cached
    if cache_key in _filler_cache and _filler_cache[cache_key]:
        return random.choice(_filler_cache[cache_key])

    # Slow path: generate fillers (runs once per agent config)
    async with _cache_lock:
        if cache_key in _filler_cache and _filler_cache[cache_key]:
            return random.choice(_filler_cache[cache_key])

        phrases = LONG_FILLER_PHRASES if long else SHORT_FILLER_PHRASES
        logger.info("FILLER_GEN generating %d %s fillers for %s/%s/%s",
                     len(phrases), ftype, provider, voice, model)

        wavs = await _generate_fillers(phrases, provider, voice, model)
        _filler_cache[cache_key] = wavs
        logger.info("FILLER_GEN cached %d %s fillers for %s/%s/%s",
                     len(wavs), ftype, provider, voice, model)

        if wavs:
            return random.choice(wavs)
        return None
