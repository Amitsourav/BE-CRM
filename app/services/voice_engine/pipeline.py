from app.services.voice_engine.sarvam_stt import sarvam_stt
from app.services.voice_engine.sarvam_tts import sarvam_tts
from app.services.voice_engine.smallest_tts import smallest_tts
from app.services.voice_engine.llm_service import llm_service
from app.services.voice_engine.call_state import call_state_manager
from app.services.voice_engine.retry import retry_async


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

        # STEP 1: STT — try streaming first (lower latency), fall back to batch.
        # Use agent.stt_model from DB so dashboard changes take effect.
        stt_model = getattr(agent, "stt_model", None) or "saaras:v3"
        stt_result = await retry_async(
            lambda: sarvam_stt.transcribe_stream(
                audio_bytes=audio_bytes,
                model=stt_model,
            ),
            attempts=1,  # transcribe_stream already has internal fallback
            fallback={"transcript": "", "language_code": "en-IN", "detected_language": "en"},
            label="sarvam_stt_stream",
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
