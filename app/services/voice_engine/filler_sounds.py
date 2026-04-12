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

# Short filler phrases — natural Hindi/Hinglish thinking sounds.
# Keep each under 5 words so TTS generates in <200ms.
FILLER_PHRASES = [
    "Hmm...",
    "Achha...",
    "Haan...",
    "Dekho...",
    "Theek hai...",
]

# Module-level cache: {(tts_provider, tts_voice, tts_model): [wav_bytes, ...]}
_filler_cache: dict[tuple, list[bytes]] = {}
_cache_lock = asyncio.Lock()


async def get_filler_sound(agent) -> Optional[bytes]:
    """Return a random pre-generated filler WAV, or generate on-demand.

    First call for a given (provider, voice, model) combo generates ALL
    fillers and caches them. Subsequent calls return a random cached one
    in <1ms.
    """
    provider = (getattr(agent, "tts_provider", "smallest") or "smallest").lower()
    voice = getattr(agent, "tts_voice", "sana") or "sana"
    model = getattr(agent, "tts_model", "lightning-v3.1") or "lightning-v3.1"
    cache_key = (provider, voice, model)

    # Fast path: already cached
    if cache_key in _filler_cache and _filler_cache[cache_key]:
        return random.choice(_filler_cache[cache_key])

    # Slow path: generate all fillers (runs once per agent config)
    async with _cache_lock:
        # Double-check after acquiring lock
        if cache_key in _filler_cache and _filler_cache[cache_key]:
            return random.choice(_filler_cache[cache_key])

        logger.info("FILLER_GEN generating %d fillers for %s/%s/%s",
                     len(FILLER_PHRASES), provider, voice, model)

        wavs = []
        try:
            if provider == "smallest":
                from app.services.voice_engine.smallest_tts import smallest_tts
                for phrase in FILLER_PHRASES:
                    try:
                        wav = await asyncio.wait_for(
                            smallest_tts.synthesize(
                                text=phrase,
                                voice=voice,
                                model=model,
                            ),
                            timeout=5.0,
                        )
                        if wav and len(wav) > 500:
                            wavs.append(wav)
                    except Exception as e:
                        logger.warning("filler gen failed for '%s': %s", phrase, e)
            else:
                from app.services.voice_engine.sarvam_tts import sarvam_tts
                for phrase in FILLER_PHRASES:
                    try:
                        wav = await asyncio.wait_for(
                            sarvam_tts.synthesize(
                                text=phrase,
                                voice=voice,
                                model=model,
                            ),
                            timeout=5.0,
                        )
                        if wav and len(wav) > 500:
                            wavs.append(wav)
                    except Exception as e:
                        logger.warning("filler gen failed for '%s': %s", phrase, e)
        except Exception as e:
            logger.warning("filler generation failed: %s", e)

        _filler_cache[cache_key] = wavs
        logger.info("FILLER_GEN cached %d fillers for %s/%s/%s",
                     len(wavs), provider, voice, model)

        if wavs:
            return random.choice(wavs)
        return None
