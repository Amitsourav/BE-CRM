from __future__ import annotations

import hmac
import hashlib
import logging
import httpx
from fastapi import HTTPException
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class BolnaService:
    """Service layer for Bolna AI voice platform.

    Handles provider config construction, agent payload building,
    and outbound call initiation.
    """

    def __init__(self):
        self.api_key = settings.bolna_api_key
        self.base_url = settings.bolna_base_url
        self.webhook_secret = settings.bolna_webhook_secret
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    # ── Provider Config Builders ──────────────────────────────────────

    def get_transcriber_config(self, agent) -> dict:
        """Sarvam AI for STT — handles Hindi + English + Hinglish."""
        return {
            "provider": "sarvam",
            "model": "saaras:v3",
            "language": agent.language or "en",
            "stream": True,
            "keywords": [],
        }

    def get_llm_config(self, agent) -> dict:
        """OpenRouter with GPT-4o mini."""
        return {
            "provider": "openrouter",
            "model": agent.model_name or "openai/gpt-4o-mini",
            "api_key": settings.openrouter_api_key,
            "temperature": 0.7,
            "max_tokens": 150,
        }

    def get_synthesizer_config(self, agent) -> dict:
        """Smallest.ai for English, Sarvam for Hindi."""
        language = agent.language or "en"
        if language in ("hi", "hi-IN"):
            return {
                "provider": "sarvam",
                "voice_id": agent.voice_id or "meera",
                "model": "bulbul:v2",
                "language": "hi",
                "stream": True,
            }
        return {
            "provider": "smallest",
            "voice_id": agent.voice_id or "emily",
            "model": "lightning",
            "language": "en",
            "stream": True,
        }

    def get_telephony_config(self) -> dict:
        """Plivo telephony config."""
        return {
            "provider": "plivo",
            "auth_id": settings.plivo_auth_id,
            "auth_token": settings.plivo_auth_token,
            "from_number": settings.plivo_phone_number,
        }

    # ── Build Complete Call Payload ────────────────────────────────────

    def build_call_payload(self, agent, lead, call_id) -> dict:
        """Builds the full Bolna outbound call payload."""
        return {
            "agent_config": {
                "agent_name": agent.name,
                "agent_type": "outbound",
                "agent_welcome_message": f"Hello, am I speaking with {lead.full_name or 'you'}?",
                "tasks": [
                    {
                        "task_type": "conversation",
                        "task_config": {
                            "hangup_after_silence": 30,
                            "call_cancellation_prompt": "Call ended by user",
                        },
                        "toolchain": {
                            "execution": "parallel",
                            "pipelines": [
                                ["transcriber", "llm", "synthesizer"]
                            ],
                        },
                        "tools_config": {
                            "transcriber": self.get_transcriber_config(agent),
                            "llm_agent": self.get_llm_config(agent),
                            "synthesizer": self.get_synthesizer_config(agent),
                            "input": {
                                "provider": "plivo",
                                "format": "pcm",
                            },
                            "output": {
                                "provider": "plivo",
                                "format": "pcm",
                            },
                        },
                    }
                ],
            },
            "agent_prompts": {
                "task_1": {
                    "system_prompt": agent.system_prompt,
                }
            },
            "call_details": {
                "to_phone_number": lead.phone,
                "from_phone_number": settings.plivo_phone_number,
                "telephony": self.get_telephony_config(),
            },
            "metadata": {
                "call_id": str(call_id),
                "lead_id": str(lead.id),
                "company_id": str(lead.company_id),
                "agent_id": str(agent.id),
            },
            "webhook_url": f"{settings.backend_url}/api/v1/webhooks/bolna",
        }

    # ── Initiate Outbound Call ────────────────────────────────────────

    async def initiate_call(self, agent, lead, call_id) -> dict:
        """Triggers an outbound call via Bolna API.

        Returns the Bolna API response dict (contains provider call_id).
        Raises HTTPException on failure.
        """
        payload = self.build_call_payload(agent, lead, call_id)

        logger.info("[BOLNA] Initiating call %s to %s via agent '%s'", call_id, lead.phone, agent.name)

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/call/outbound",
                    headers=self.headers,
                    json=payload,
                    timeout=30.0,
                )

            if response.status_code not in (200, 201):
                logger.error("[BOLNA] API error %d: %s", response.status_code, response.text)
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Bolna API error: {response.text}",
                )

            result = response.json()
            logger.info("[BOLNA] Call initiated successfully: %s → provider_id=%s", call_id, result.get("call_id"))
            return result

        except httpx.TimeoutException:
            logger.error("[BOLNA] Timeout initiating call %s", call_id)
            raise HTTPException(status_code=504, detail="Bolna API timeout")
        except httpx.RequestError as e:
            logger.error("[BOLNA] Connection error for call %s: %s", call_id, e)
            raise HTTPException(status_code=502, detail=f"Bolna API connection error: {e}")

    # ── Webhook Signature Verification ────────────────────────────────

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """Verify HMAC-SHA256 signature from Bolna webhook."""
        if not self.webhook_secret or not signature:
            return False
        expected = hmac.new(
            self.webhook_secret.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)


# Singleton instance
bolna_service = BolnaService()
