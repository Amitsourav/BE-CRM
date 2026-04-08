import httpx
from app.config import get_settings
from app.services.language_detector import (
    detect_language,
    get_language_instruction,
)


class LLMService:

    OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

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

            if style == "hinglish":
                detected_lang = "hinglish"
                lang_instruction = (
                    "[LANGUAGE RULES (strict):\n"
                    "- Reply in natural Hinglish: mix Hindi and English in a "
                    "single sentence, the way urban Indians speak casually.\n"
                    "- Use Hindi for verbs/connectors (hai, karunga, toh, aur) "
                    "and English for nouns/technical terms (loan, MBA, visa).\n"
                    "- Do NOT reply in pure Hindi or pure English.\n"
                    "- Use female Hindi grammar: karungi, sakti hoon, chahti hoon.]"
                )
            elif style == "primary_only":
                detected_lang = primary
                lang_instruction = get_language_instruction(primary)
            else:  # mirror (default)
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
                lang_instruction = get_language_instruction(detected_lang)

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

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.OPENROUTER_URL,
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


llm_service = LLMService()
