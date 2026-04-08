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
            detected_lang = detect_language(message)
            lang_instruction = get_language_instruction(detected_lang)
            enhanced_message = f"{lang_instruction}\n\nUser: {message}"

            messages = [
                {"role": "system", "content": agent.system_prompt},
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
