import logging
import time

from app.services.voice_engine.sarvam_tts import sarvam_tts
from app.services.voice_engine.smallest_tts import smallest_tts
from app.services.voice_engine.llm_service import llm_service
from app.services.voice_engine.call_state import call_state_manager
from app.services.voice_engine.retry import retry_async
from app.services.voice_engine.stt_router import get_stt_for_agent

logger = logging.getLogger(__name__)


def _stt_language_code_for_agent(agent) -> str:
    """Pick the STT language_code based on agent config.

    Why this exists: Sarvam's 'unknown' auto-detection was randomly
    returning Gujarati/Telugu/Malayalam/Bengali scripts for the same
    user (who was actually speaking Hindi+English). Locking the STT
    to a specific language code stops the script chaos.

    Sarvam's 'hi-IN' accepts Hindi AND English speech (Hinglish),
    which is what almost every Indian voice agent actually needs.
    Only return 'en-IN' for pure-English agents.
    """
    provider = (getattr(agent, "stt_provider", "sarvam") or "sarvam").lower()
    primary = (getattr(agent, "primary_language", None) or "en").lower()
    secondary = (getattr(agent, "secondary_language", None) or "hi").lower()
    style = (getattr(agent, "language_style", None) or "mirror").lower()

    handles_hindi = (
        primary == "hi"
        or secondary == "hi"
        or style in ("hinglish", "mirror_hinglish")
    )

    if provider == "deepgram":
        # Deepgram doesn't support Hindi on nova-2-general; use multi
        return "multi" if handles_hindi else "en-IN"
    if provider == "openai":
        # Whisper uses ISO-639-1
        return "hi" if handles_hindi else "en"
    # Sarvam: en-IN is safer for mixed English+Hindi callers because
    # hi-IN mode mistranslates English words into Hindi equivalents
    # ("yeah sure" → "यह अच्छा", "its myself" → "हम्म इसमें").
    # en-IN correctly transcribes English and romanizes Hindi speech
    # which our language detector can still classify from HINDI_WORDS.
    # If pure Hindi callers report bad transcription, switch back to
    # hi-IN and accept the English translation artifacts.
    return "en-IN"


class VoicePipeline:

    async def process_audio(
        self,
        call_id: str,
        audio_bytes: bytes,
        agent,
    ) -> dict:
        """Audio → STT → LLM → TTS → Audio."""
        state = call_state_manager.get(call_id)
        if not state:
            return {"error": "Call not found"}

        # STEP 1: STT — route by agent.stt_provider (sarvam/deepgram/openai).
        # Pass a concrete language_code derived from agent config so Sarvam
        # doesn't randomly pick Gujarati/Telugu/Malayalam etc per turn.
        stt_engine, stt_model = get_stt_for_agent(agent)
        stt_keywords = getattr(agent, "stt_keywords", None) or ""
        stt_lang = _stt_language_code_for_agent(agent)
        stt_result = await retry_async(
            lambda: stt_engine.transcribe_stream(
                audio_bytes=audio_bytes,
                model=stt_model,
                keywords=stt_keywords,
                language_code=stt_lang,
            ),
            attempts=1,  # each backend has its own internal fallback
            fallback={"transcript": "", "language_code": stt_lang, "detected_language": "en"},
            label=f"stt_{getattr(agent, 'stt_provider', 'sarvam')}",
        )
        transcript = (stt_result or {}).get("transcript", "").strip()

        if not transcript:
            return {
                "audio_response": b"",
                "transcript": "",
                "agent_response": "",
                "language": state.current_language,
            }

        # STEP 2: LLM (snapshot history before mutation)
        current_history = list(state.conversation_history)
        llm_result = await retry_async(
            lambda: llm_service.get_response(
                message=transcript,
                conversation_history=current_history,
                agent=agent,
            ),
            attempts=2,
            fallback={"response": "Sorry, please repeat that.", "language": "en"},
            label="llm",
        )
        agent_response = (llm_result or {}).get("response", "")
        detected_language = (llm_result or {}).get("language", "en")

        # STEP 3: TTS — WS handler owns is_agent_speaking flag (set after
        # _send_audio_response with proper playback-duration timer)
        audio_response = await retry_async(
            lambda: self._get_tts_audio(
                text=agent_response,
                language=detected_language,
                agent=agent,
            ),
            attempts=2,
            fallback=b"",
            label="tts",
        )

        # STEP 4: state update — add_turn is the single source of truth
        state.add_turn(
            user_text=transcript,
            agent_text=agent_response,
            language=detected_language,
        )

        return {
            "audio_response": audio_response,
            "transcript": transcript,
            "agent_response": agent_response,
            "language": detected_language,
        }

    async def process_audio_streaming(
        self,
        call_id: str,
        audio_bytes: bytes,
        agent,
    ):
        """Streaming variant: yields WAV audio chunks sentence-by-sentence.

        Same STT step, but the LLM runs in streaming mode and each complete
        sentence is handed to TTS immediately. The user hears the first
        words while the LLM is still generating — ~2s of perceived latency
        shaved on average.

        Yields:
            {"audio": wav_bytes, "text": sentence, "language": lang}
              — one per sentence, in order
            {"done": True, "transcript": "...", "response": "...", "language": lang}
              — once at the end, after state has been updated
        """
        state = call_state_manager.get(call_id)
        if not state:
            return

        turn_t0 = time.time()

        # STEP 1: STT (same as batch path)
        stt_engine, stt_model = get_stt_for_agent(agent)
        stt_keywords = getattr(agent, "stt_keywords", None) or ""
        stt_lang = _stt_language_code_for_agent(agent)
        stt_t0 = time.time()
        stt_result = await retry_async(
            lambda: stt_engine.transcribe_stream(
                audio_bytes=audio_bytes,
                model=stt_model,
                keywords=stt_keywords,
                language_code=stt_lang,
            ),
            attempts=1,
            fallback={"transcript": "", "language_code": stt_lang, "detected_language": "en"},
            label=f"stt_{getattr(agent, 'stt_provider', 'sarvam')}",
        )
        stt_ms = int((time.time() - stt_t0) * 1000)
        transcript = (stt_result or {}).get("transcript", "").strip()
        if not transcript:
            logger.info("TURN_EMPTY call_id=%s stt_ms=%d", call_id, stt_ms)
            return

        # STEP 2: LLM stream → sentence chunks → TTS immediately
        current_history = list(state.conversation_history)
        full_response = ""
        detected_language = "en"
        any_audio_sent = False
        llm_first_token_ms = 0
        tts_first_sentence_ms = 0
        llm_t0 = time.time()
        first_token_seen = False
        first_audio_seen = False

        try:
            async for chunk in llm_service.get_response_stream(
                message=transcript,
                conversation_history=current_history,
                agent=agent,
            ):
                ctype = chunk.get("type")
                if ctype == "sentence":
                    if not first_token_seen:
                        first_token_seen = True
                        llm_first_token_ms = int((time.time() - llm_t0) * 1000)
                    sentence = chunk.get("text", "").strip()
                    detected_language = chunk.get("language", detected_language)
                    if not sentence:
                        continue
                    # TTS this sentence alone; skip on failure rather than block
                    tts_t0 = time.time()
                    try:
                        wav = await self._get_tts_audio(
                            text=sentence,
                            language=detected_language,
                            agent=agent,
                        )
                    except Exception:
                        wav = b""
                    if wav:
                        any_audio_sent = True
                        if not first_audio_seen:
                            first_audio_seen = True
                            tts_first_sentence_ms = int((time.time() - tts_t0) * 1000)
                        yield {
                            "audio": wav,
                            "text": sentence,
                            "language": detected_language,
                        }
                elif ctype == "done":
                    full_response = chunk.get("text", "") or full_response
                    detected_language = chunk.get("language", detected_language)
                elif ctype == "error":
                    # Streaming failed — fall through to batch fallback
                    raise RuntimeError(chunk.get("text") or "llm stream error")
        except Exception as e:
            # Fall back to batch LLM+TTS so the turn still produces output
            import logging
            logging.getLogger(__name__).warning(
                "streaming pipeline failed (%s), falling back to batch", e
            )
            llm_result = await llm_service.get_response(
                message=transcript,
                conversation_history=current_history,
                agent=agent,
            )
            full_response = (llm_result or {}).get("response", "")
            detected_language = (llm_result or {}).get("language", "en")
            if full_response:
                wav = await self._get_tts_audio(
                    text=full_response,
                    language=detected_language,
                    agent=agent,
                )
                if wav:
                    any_audio_sent = True
                    yield {
                        "audio": wav,
                        "text": full_response,
                        "language": detected_language,
                    }

        # STEP 3: state update (single source of truth, once per turn)
        if full_response:
            state.add_turn(
                user_text=transcript,
                agent_text=full_response,
                language=detected_language,
            )

        turn_total_ms = int((time.time() - turn_t0) * 1000)
        logger.info(
            "TURN_TIMING call_id=%s stt_ms=%d llm_first_token_ms=%d "
            "tts_first_sentence_ms=%d total_ms=%d lang=%s transcript=%r",
            call_id, stt_ms, llm_first_token_ms, tts_first_sentence_ms,
            turn_total_ms, detected_language, transcript[:80],
        )

        yield {
            "done": True,
            "transcript": transcript,
            "response": full_response,
            "language": detected_language,
            "audio_sent": any_audio_sent,
        }

    async def _get_tts_audio(
        self,
        text: str,
        language: str,
        agent,
    ) -> bytes:
        """Route to correct TTS provider — dual config first, then default.

        Smallest dual-TTS path is handled here (Sarvam path delegated to
        sarvam_tts.synthesize_for_call to avoid duplicating routing logic).
        """
        # DUAL TTS English via Smallest is the only case sarvam_tts can't handle
        if (
            language == "en"
            and getattr(agent, "tts_provider_english", None) == "smallest"
        ):
            return await smallest_tts.synthesize(
                text=text,
                voice=agent.tts_voice_english or "emily",
                model=agent.tts_model_english or "lightning-v2",
            )

        # Default-provider Smallest fallback
        if (
            agent.tts_provider == "smallest"
            and not getattr(agent, "tts_provider_english", None)
            and not getattr(agent, "tts_provider_hindi", None)
        ):
            return await smallest_tts.synthesize(
                text=text,
                voice=agent.tts_voice or "emily",
                model=agent.tts_model or "lightning-v2",
            )

        # All Sarvam paths (default + dual config) handled centrally
        return await sarvam_tts.synthesize_for_call(
            text=text,
            agent=agent,
            language=language,
        )

    async def generate_welcome_audio(
        self,
        agent,
        lead_name: str = "there",
    ) -> bytes:
        """Welcome message audio when lead picks up."""
        welcome = agent.welcome_message or f"Hello! Am I speaking with {lead_name}?"
        welcome = welcome.replace("{name}", lead_name)
        return await self._get_tts_audio(
            text=welcome,
            language="en",
            agent=agent,
        )


voice_pipeline = VoicePipeline()
