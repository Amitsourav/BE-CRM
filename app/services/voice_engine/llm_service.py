import json
import logging

import httpx

from app.config import get_settings
from app.services.language_detector import (
    detect_language,
    get_language_instruction,
)
from app.services.voice_engine.http_clients import get_openrouter_client

logger = logging.getLogger(__name__)


# Sentence terminators: English + Hindi (।)
_SENTENCE_ENDS = ".!?।"
# Early-flush boundaries for the FIRST chunk only — used to start TTS
# on the opening clause instead of waiting for a full sentence.
# Includes commas and colons which are natural breath points.
_EARLY_FLUSH = ".!?।,:;"
# Fallback: if we accumulate this many characters without any punctuation
# at all, flush on the last whitespace so TTS can start talking.
_MAX_EARLY_CHARS = 60


_HINGLISH_INSTRUCTION = (
    "[LANGUAGE RULES (strict):\n"
    "- Reply in natural Hinglish: mix Hindi and English in a single "
    "sentence, the way urban Indians speak casually.\n"
    "- Use Hindi words for verbs/connectors (hai, karunga, toh, aur, "
    "chahiye) and English for nouns/technical terms (loan, MBA, visa, "
    "collateral, eligibility).\n"
    "- Write Hindi words in ROMAN script (Hinglish) — NEVER in "
    "Devanagari/Hindi script.\n"
    "- Do NOT reply in pure Hindi. Do NOT reply in pure English.\n"
    "- Use female Hindi grammar: karungi, sakti hoon, chahti hoon.]"
)


def _resolve_language_policy(
    message: str,
    style: str,
    primary: str,
    secondary: str,
    auto_switch: bool,
) -> tuple[str, str]:
    """Return (detected_lang, lang_instruction) based on agent policy.

    Supported language_style values:
      hinglish        — always Hinglish regardless of user language
      primary_only    — always reply in primary_language (no switching)
      mirror / mirror_user — reply in user's language (primary↔secondary)
      mirror_hinglish — reply in English when user speaks English, reply
                        in Hinglish when user speaks Hindi. Never pure
                        Hindi. This is what most Indian voice agents
                        actually want.
    """
    if style == "hinglish":
        return "hinglish", _HINGLISH_INSTRUCTION

    if style == "primary_only":
        return primary, get_language_instruction(primary)

    if style == "mirror_hinglish":
        # English stays English; anything detected as Hindi becomes Hinglish.
        #
        # IMPORTANT: short responses (1-3 words) like "yes", "ok", "haan"
        # are too ambiguous for reliable language detection — especially
        # because Sarvam STT translates English "yes" to Hindi "ठीक है"
        # when locked to hi-IN. For short messages, we keep the PREVIOUS
        # turn's language (from state) instead of re-detecting. This
        # prevents the "user said yes in English → agent flipped to
        # Hinglish" bug. Only messages with 4+ words trigger fresh
        # detection — long enough to reliably tell English from Hindi.
        word_count = len(message.split()) if message else 0
        if word_count <= 3:
            # Import here to avoid circular; only used for this branch
            from app.services.voice_engine.call_state import call_state_manager
            # Try to get the previous language from conversation state;
            # fall back to primary (English) if no state or first turn
            prev_lang = primary
            # _resolve_language_policy doesn't have call_id, so we can't
            # look up state here. Instead, use the DETECTED language but
            # bias toward English for ambiguous short phrases.
            raw = detect_language(message) if auto_switch else primary
            if raw == "en":
                return "en", get_language_instruction("en")
            # Short Hindi detection is unreliable (could be translated
            # English). Default to English for short responses.
            return "en", get_language_instruction("en")
        # Long message (4+ words): reliable detection
        raw = detect_language(message) if auto_switch else primary
        if raw == "en":
            return "en", get_language_instruction("en")
        return "hinglish", _HINGLISH_INSTRUCTION

    # mirror / mirror_user / anything else → mirror user's detected language
    if not auto_switch:
        detected_lang = primary
    else:
        raw = detect_language(message)
        if raw == primary:
            detected_lang = primary
        elif raw == secondary:
            detected_lang = secondary
        else:
            detected_lang = primary
    return detected_lang, get_language_instruction(detected_lang)


def _find_sentence_end(buf: str, early: bool = False) -> int:
    """Return index of first sentence-ending punctuation in buf, or -1.

    When early=True, also accepts commas/colons AND falls back to a
    whitespace split once buf exceeds _MAX_EARLY_CHARS. This is used for
    the very first chunk of each turn so we can begin TTS on a clause
    boundary rather than waiting for the first full sentence — typically
    saves 500-800ms on the perceived "agent started talking" latency.

    Skips decimal points in numbers (e.g. "3.14").
    """
    terminators = _EARLY_FLUSH if early else _SENTENCE_ENDS
    for i, ch in enumerate(buf):
        if ch in terminators:
            if ch == "." and 0 < i < len(buf) - 1:
                if buf[i - 1].isdigit() and buf[i + 1].isdigit():
                    continue
            return i
    if early and len(buf) >= _MAX_EARLY_CHARS:
        # No punctuation yet but we've buffered enough — flush on last
        # whitespace so we don't cut mid-word.
        ws = buf.rfind(" ", 0, _MAX_EARLY_CHARS)
        if ws > 0:
            return ws
    return -1


class LLMService:

    OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
    OPENROUTER_PATH = "/api/v1/chat/completions"

    async def get_response(
        self,
        message: str,
        conversation_history: list,
        agent,
    ) -> dict:
        """Get AI response for user message with language injection."""
        settings = get_settings()
        try:
            # Language policy from agent config.
            # language_style takes precedence over auto_language_switch:
            #   mirror       → reply in same language as user (clamped to
            #                  primary/secondary if auto_switch is on,
            #                  else locked to primary)
            #   hinglish     → always reply Hindi+English mixed regardless
            #                  of what user spoke
            #   primary_only → always reply in primary_language, never switch
            primary = (getattr(agent, "primary_language", None) or "en").lower()
            secondary = (getattr(agent, "secondary_language", None) or "hi").lower()
            auto_switch = getattr(agent, "auto_language_switch", True)
            style = (getattr(agent, "language_style", None) or "mirror").lower()

            detected_lang, lang_instruction = _resolve_language_policy(
                message=message,
                style=style,
                primary=primary,
                secondary=secondary,
                auto_switch=auto_switch,
            )

            enhanced_message = f"{lang_instruction}\n\nUser: {message}"

            # Inject role/tone + max_response_words into system prompt
            max_words = getattr(agent, "max_response_words", None) or 25
            role = getattr(agent, "role", None) or "sales"
            tone = getattr(agent, "tone", None) or "friendly"
            persona_rule = (
                f"[PERSONA: You are a {role} agent speaking with a {tone} tone. "
                f"Stay in character throughout the call.]\n\n"
            )
            length_rule = (
                f"\n\n[LENGTH RULE: Keep responses to at most {max_words} words. "
                "Be concise — this is a phone call, not an email.]"
            )
            system_content = persona_rule + (agent.system_prompt or "") + length_rule

            messages = [
                {"role": "system", "content": system_content},
                *conversation_history,
                {"role": "user", "content": enhanced_message},
            ]

            client = get_openrouter_client()
            response = await client.post(
                self.OPENROUTER_PATH,
                headers={
                    "Authorization": f"Bearer {settings.openrouter_api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": settings.backend_url,
                    "X-Title": "BE-CRM Voice Agent",
                },
                json={
                    "model": agent.llm_model,
                    "messages": messages,
                    "max_tokens": agent.llm_max_tokens,
                    "temperature": agent.llm_temperature,
                },
            )
            if response.status_code != 200:
                return {
                    "response": "Sorry, I am having technical difficulties.",
                    "language": detected_lang,
                }

            try:
                data = response.json()
                ai_text = data["choices"][0]["message"]["content"].strip()
            except (ValueError, KeyError, IndexError):
                return {
                    "response": "Sorry, please repeat that.",
                    "language": detected_lang,
                }

            return {
                "response": ai_text,
                "language": detected_lang,
            }

        except (httpx.RequestError, httpx.TimeoutException) as e:
            return {
                "response": "Sorry, please repeat that.",
                "language": "en",
                "error": str(e),
            }


    async def get_response_stream(
        self,
        message: str,
        conversation_history: list,
        agent,
    ):
        """Streaming version of get_response.

        Yields dicts:
          {"type": "sentence", "text": "...", "language": "en"}
            — one per complete sentence as soon as it's ready
          {"type": "done", "text": "<full>", "language": "en"}
            — once at the end with the accumulated full response
          {"type": "error", "text": "...", "language": "en"}
            — if streaming failed; caller should fall back to get_response

        Designed for sentence-by-sentence TTS handoff so the user hears
        the first words while the LLM is still generating the rest.
        """
        settings = get_settings()

        # Reuse the same language + persona + length rules as get_response
        primary = (getattr(agent, "primary_language", None) or "en").lower()
        secondary = (getattr(agent, "secondary_language", None) or "hi").lower()
        auto_switch = getattr(agent, "auto_language_switch", True)
        style = (getattr(agent, "language_style", None) or "mirror").lower()

        detected_lang, lang_instruction = _resolve_language_policy(
            message=message,
            style=style,
            primary=primary,
            secondary=secondary,
            auto_switch=auto_switch,
        )

        enhanced_message = f"{lang_instruction}\n\nUser: {message}"

        max_words = getattr(agent, "max_response_words", None) or 25
        role = getattr(agent, "role", None) or "sales"
        tone = getattr(agent, "tone", None) or "friendly"
        persona_rule = (
            f"[PERSONA: You are a {role} agent speaking with a {tone} tone. "
            f"Stay in character throughout the call.]\n\n"
        )
        length_rule = (
            f"\n\n[LENGTH RULE: Keep responses to at most {max_words} words. "
            "Be concise — this is a phone call, not an email.]"
        )
        system_content = persona_rule + (agent.system_prompt or "") + length_rule

        messages = [
            {"role": "system", "content": system_content},
            *conversation_history,
            {"role": "user", "content": enhanced_message},
        ]

        buffer = ""
        full_response = ""
        first_chunk_flushed = False

        try:
            client = get_openrouter_client()
            async with client.stream(
                "POST",
                self.OPENROUTER_PATH,
                headers={
                    "Authorization": f"Bearer {settings.openrouter_api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": settings.backend_url,
                    "X-Title": "BE-CRM Voice Agent",
                },
                json={
                    "model": agent.llm_model,
                    "messages": messages,
                    "max_tokens": agent.llm_max_tokens,
                    "temperature": agent.llm_temperature,
                    "stream": True,
                },
            ) as response:
                if response.status_code != 200:
                    err_body = (await response.aread()).decode("utf-8", "replace")[:200]
                    logger.warning("LLM stream HTTP %s: %s", response.status_code, err_body)
                    yield {"type": "error", "text": "", "language": detected_lang}
                    return

                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    payload = line[6:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        data = json.loads(payload)
                        delta = (
                            data.get("choices", [{}])[0]
                            .get("delta", {})
                            .get("content", "")
                        )
                    except (ValueError, KeyError, IndexError):
                        continue
                    if not delta:
                        continue

                    buffer += delta
                    full_response += delta

                    # First chunk: use early-flush (comma/colon/maxchars)
                    # so TTS can start on the opening clause. Subsequent
                    # chunks use strict sentence-end only.
                    #
                    # But: if the very first "sentence" is an ultra-short
                    # acknowledgment like "Great!", "Okay!", "Sure!" —
                    # don't flush it alone or the user hears the filler
                    # and then a 2-3s gap before the real reply. Merge
                    # it with the next sentence instead.
                    while True:
                        use_early = not first_chunk_flushed
                        idx = _find_sentence_end(buffer, early=use_early)
                        if idx == -1:
                            break
                        candidate = buffer[: idx + 1].strip()
                        remaining = buffer[idx + 1 :].lstrip()
                        # Hold ultra-short first sentences unless we've
                        # already flushed once or buffered too much.
                        # Thresholds tuned for phone calls (latency > batching):
                        #   <8 chars = single filler word ("Achha,")
                        #   buffer <40 = haven't accumulated much yet
                        if (
                            not first_chunk_flushed
                            and len(candidate) < 8
                            and len(buffer) < 40
                        ):
                            break
                        buffer = remaining
                        if candidate:
                            first_chunk_flushed = True
                            yield {
                                "type": "sentence",
                                "text": candidate,
                                "language": detected_lang,
                            }

            # Flush any trailing fragment as one last sentence
            tail = buffer.strip()
            if tail:
                yield {
                    "type": "sentence",
                    "text": tail,
                    "language": detected_lang,
                }

            yield {
                "type": "done",
                "text": full_response.strip(),
                "language": detected_lang,
            }
        except (httpx.RequestError, httpx.TimeoutException) as e:
            logger.warning("LLM stream network error: %s", e)
            yield {"type": "error", "text": str(e), "language": detected_lang}
        except Exception as e:
            logger.exception("LLM stream unexpected error: %s", e)
            yield {"type": "error", "text": str(e), "language": detected_lang}


    async def warmup(self, model: str) -> None:
        """Fire a tiny chat completion to wake the model on Groq/OpenRouter.

        Groq keeps a model hot for ~5 minutes after use, then evicts it.
        A cold first request costs 3-6s of first-token latency while the
        model is reloaded onto the LPU. Calling this during phone ring
        time (~5-8s of idle wait) lets the real turn be served warm.

        Returns once we've received any response (ignores errors).
        Uses max_tokens=1 so it costs essentially nothing.
        """
        settings = get_settings()
        try:
            client = get_openrouter_client()
            await client.post(
                self.OPENROUTER_PATH,
                headers={
                    "Authorization": f"Bearer {settings.openrouter_api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": settings.backend_url,
                    "X-Title": "BE-CRM Voice Agent",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "user", "content": "ping"},
                    ],
                    "max_tokens": 1,
                    "temperature": 0,
                },
                timeout=10.0,
            )
        except Exception as e:
            logger.debug("llm warmup ping failed (non-fatal): %s", e)


llm_service = LLMService()
