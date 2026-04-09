from __future__ import annotations

import uuid
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field


# IMPORTANT: voice lists here are the SINGLE SOURCE OF TRUTH for the
# agent dashboard dropdowns. Only list voices that have been confirmed
# working in production — Sarvam's catalog differs by bulbul model
# version and unsupported voices return HTTP 400 and cause silent calls.
#
# Sarvam bulbul:v2 / bulbul:v3 confirmed-working set (as of Apr 2026):
#   Female: meera, pavithra, maitreyi, misha, diya, maya
#   Male:   arjun, amol, amartya, arvind, neel, vian, rahul
#
# Voices previously listed but rejected by Sarvam (do NOT re-add without
# confirming via sarvam_tts logs first):
#   simran, anushka, priya, pooja, ishita, shreya, amelia, kabir, aditya
PROVIDER_OPTIONS = {
    "stt_providers": [
        {"value": "sarvam", "label": "Sarvam AI (Hindi+English best)"},
        {"value": "deepgram", "label": "Deepgram (English reliable)"},
        {"value": "azure", "label": "Azure STT"},
    ],
    "tts_providers": [
        {"value": "sarvam", "label": "Sarvam AI (Indian voices)"},
        {"value": "smallest", "label": "Smallest AI (fast English)"},
        {"value": "elevenlabs", "label": "ElevenLabs (premium)"},
        {"value": "cartesia", "label": "Cartesia (low latency)"},
    ],
    "tts_providers_english": [
        {"value": "smallest", "label": "Smallest AI (fast English)"},
        {"value": "elevenlabs", "label": "ElevenLabs (premium English)"},
        {"value": "cartesia", "label": "Cartesia (low latency)"},
        {"value": "sarvam", "label": "Sarvam AI (Indian English)"},
    ],
    "tts_voices_english": {
        "smallest": [
            {"value": "emily", "label": "Emily (Female English)"},
            {"value": "sarah", "label": "Sarah (Female English)"},
            {"value": "luna", "label": "Luna (Female English)"},
            {"value": "john", "label": "John (Male English)"},
        ],
        "elevenlabs": [
            {"value": "Rachel", "label": "Rachel (Female English)"},
            {"value": "Domi", "label": "Domi (Female English)"},
            {"value": "Bella", "label": "Bella (Female English)"},
        ],
        "cartesia": [
            {"value": "sonic-english", "label": "Sonic English (Female)"},
        ],
        "sarvam": [
            {"value": "meera", "label": "Meera (Female Indian English)"},
            {"value": "pavithra", "label": "Pavithra (Female Indian English)"},
            {"value": "arjun", "label": "Arjun (Male Indian English)"},
            {"value": "rahul", "label": "Rahul (Male Indian English)"},
        ],
    },
    "tts_providers_hindi": [
        {"value": "sarvam", "label": "Sarvam AI (best for Hindi)"},
    ],
    "tts_voices_hindi": {
        "sarvam": [
            {"value": "meera", "label": "Meera (Female Hindi)"},
            {"value": "pavithra", "label": "Pavithra (Female Hindi)"},
            {"value": "maitreyi", "label": "Maitreyi (Female Hindi)"},
            {"value": "diya", "label": "Diya (Female Hindi)"},
            {"value": "arjun", "label": "Arjun (Male Hindi)"},
            {"value": "rahul", "label": "Rahul (Male Hindi)"},
            {"value": "amol", "label": "Amol (Male Hindi)"},
        ],
    },
    "llm_providers": [
        {"value": "openrouter", "label": "OpenRouter"},
        {"value": "openai", "label": "OpenAI"},
        {"value": "anthropic", "label": "Anthropic"},
    ],
    "llm_models": [
        {"value": "openai/gpt-4o-mini", "label": "GPT-4o Mini (recommended)"},
        {"value": "openai/gpt-4.1-mini", "label": "GPT-4.1 Mini (latest)"},
        {"value": "openai/gpt-4.1-nano", "label": "GPT-4.1 Nano (cheapest)"},
        {"value": "openai/gpt-4o", "label": "GPT-4o (powerful)"},
        {"value": "openai/gpt-4.1", "label": "GPT-4.1 (latest powerful)"},
        {"value": "anthropic/claude-3-haiku-20240307", "label": "Claude Haiku (fast)"},
    ],
    # Gender-indexed voice catalog consumed by the dashboard
    # options.voices?.[provider]?.[gender] dropdown. Keep in sync with
    # the confirmed-working set in the comment at top of this file.
    "voices": {
        "sarvam": {
            "female": [
                {"value": "meera", "label": "Meera (Female Hindi/English)"},
                {"value": "pavithra", "label": "Pavithra (Female Hindi/English)"},
                {"value": "maitreyi", "label": "Maitreyi (Female Hindi/English)"},
                {"value": "diya", "label": "Diya (Female Hindi/English)"},
                {"value": "misha", "label": "Misha (Female Hindi/English)"},
                {"value": "maya", "label": "Maya (Female Hindi/English)"},
            ],
            "male": [
                {"value": "arjun", "label": "Arjun (Male Hindi/English)"},
                {"value": "rahul", "label": "Rahul (Male Hindi/English)"},
                {"value": "amol", "label": "Amol (Male Hindi/English)"},
                {"value": "amartya", "label": "Amartya (Male Hindi/English)"},
                {"value": "arvind", "label": "Arvind (Male Hindi/English)"},
                {"value": "neel", "label": "Neel (Male Hindi/English)"},
                {"value": "vian", "label": "Vian (Male Hindi/English)"},
            ],
        },
        "smallest": {
            "female": [
                {"value": "emily", "label": "Emily (Female English)"},
                {"value": "sarah", "label": "Sarah (Female English)"},
                {"value": "luna", "label": "Luna (Female English)"},
            ],
            "male": [
                {"value": "john", "label": "John (Male English)"},
            ],
        },
        "elevenlabs": {
            "female": [
                {"value": "Rachel", "label": "Rachel (Female English)"},
                {"value": "Domi", "label": "Domi (Female English)"},
                {"value": "Bella", "label": "Bella (Female English)"},
            ],
            "male": [
                {"value": "Adam", "label": "Adam (Male English)"},
                {"value": "Antoni", "label": "Antoni (Male English)"},
            ],
        },
        "cartesia": {
            "female": [
                {"value": "sonic-english", "label": "Sonic English (Female)"},
            ],
            "male": [],
        },
    },
    "languages": [
        {"value": "en", "label": "English"},
        {"value": "hi", "label": "Hindi"},
        {"value": "hi-en", "label": "India Multilingual (auto)"},
    ],
    "secondary_languages": [
        {"value": "hi", "label": "Hindi"},
        {"value": "en", "label": "English"},
        {"value": "ta", "label": "Tamil"},
        {"value": "te", "label": "Telugu"},
        {"value": "mr", "label": "Marathi"},
        {"value": "bn", "label": "Bengali"},
        {"value": "none", "label": "None"},
    ],
    "language_styles": [
        {"value": "hinglish", "label": "Hinglish always (Hindi+English mix)"},
        {"value": "mirror_user", "label": "Mirror user language"},
        {"value": "always_hindi", "label": "Always Hindi"},
        {"value": "always_english", "label": "Always English"},
    ],
    "roles": [
        {"value": "sales", "label": "Sales"},
        {"value": "support", "label": "Support"},
        {"value": "recruitment", "label": "Recruitment"},
        {"value": "survey", "label": "Survey"},
    ],
    "tones": [
        {"value": "friendly", "label": "Friendly"},
        {"value": "professional", "label": "Professional"},
        {"value": "casual", "label": "Casual"},
        {"value": "formal", "label": "Formal"},
    ],
    "ambient_noise_options": [
        {"value": "none", "label": "None"},
        {"value": "office-ambience", "label": "Office ambience"},
        {"value": "coffee-shop", "label": "Coffee shop"},
    ],
    "telephony_providers": [
        {"value": "plivo", "label": "Plivo"},
        {"value": "exotel", "label": "Exotel"},
        {"value": "twilio", "label": "Twilio"},
    ],
}


class AIAgentBase(BaseModel):
    # Identity
    name: str = Field(..., min_length=2, max_length=100)
    role: str = "sales"
    tone: str = "friendly"
    is_default: bool = False
    is_active: bool = True

    # Prompt
    system_prompt: str = Field(..., min_length=10)
    welcome_message: str = "Hello! Am I speaking with {name}?"
    final_message_en: str = "Thank you for your time! Have a great day. Goodbye!"
    final_message_hi: str = "Bahut shukriya! Aapka din achha rahe. Alvida!"
    silence_message_en: str = "Hey, are you still there?"
    silence_message_hi: str = "Hello? Kya aap abhi bhi wahan hain?"

    # LLM
    llm_provider: str = "openrouter"
    llm_model: str = "openai/gpt-4o-mini"
    llm_temperature: float = 0.8
    llm_max_tokens: int = 100

    # STT
    stt_provider: str = "sarvam"
    stt_model: str = "saaras:v3"
    stt_keywords: Optional[str] = None

    # TTS
    tts_provider: str = "sarvam"
    tts_model: str = "bulbul:v3"
    tts_voice: str = "simran"
    tts_gender: str = "female"
    tts_speed: float = 1.0
    tts_buffer_size: int = 200
    tts_stability: float = 0.5
    tts_similarity_boost: float = 0.75

    # Dual TTS (optional)
    tts_provider_english: Optional[str] = None
    tts_model_english: Optional[str] = None
    tts_voice_english: Optional[str] = None
    tts_provider_hindi: Optional[str] = None
    tts_model_hindi: Optional[str] = None
    tts_voice_hindi: Optional[str] = None

    # Language
    primary_language: str = "en"
    secondary_language: str = "hi"
    auto_language_switch: bool = True
    language_style: str = "hinglish"

    # Timing
    endpointing_ms: int = 250
    linear_delay_ms: int = 400
    words_before_interrupt: int = 3
    max_response_words: int = 25
    precise_transcript: bool = True

    # Telephony
    telephony_provider: str = "plivo"
    phone_number: Optional[str] = None
    call_timeout_seconds: int = 600
    hangup_on_silence_seconds: int = 10
    call_start_time: str = "09:00"
    call_end_time: str = "19:00"
    restrict_call_hours: bool = True
    voicemail_detection: bool = True

    # Audio
    noise_cancellation: bool = True
    noise_cancellation_level: int = 60
    ambient_noise: str = "office-ambience"
    silence_detection_seconds: int = 9

    # Webhook
    webhook_url: Optional[str] = None


class AIAgentCreate(AIAgentBase):
    pass


class AIAgentUpdate(AIAgentBase):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    system_prompt: Optional[str] = Field(None, min_length=10)


class AIAgentResponse(AIAgentBase):
    id: uuid.UUID
    company_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    pricing: Optional[dict] = None

    model_config = {"from_attributes": True}


# Backward compatibility alias
AIAgentOut = AIAgentResponse
