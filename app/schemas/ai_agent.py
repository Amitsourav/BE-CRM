from __future__ import annotations

import uuid
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field


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
            {"value": "amelia", "label": "Amelia (Indian English)"},
            {"value": "anushka", "label": "Anushka (Indian English)"},
        ],
    },
    "tts_providers_hindi": [
        {"value": "sarvam", "label": "Sarvam AI (best for Hindi)"},
    ],
    "tts_voices_hindi": {
        "sarvam": [
            {"value": "simran", "label": "Simran (Female Hindi)"},
            {"value": "anushka", "label": "Anushka (Female Hindi)"},
            {"value": "priya", "label": "Priya (Female Hindi)"},
            {"value": "pooja", "label": "Pooja (Female Hindi)"},
            {"value": "ishita", "label": "Ishita (Female Hindi)"},
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
    "voices": {
        "sarvam": {
            "female": [
                {"value": "simran", "label": "Simran (Hindi/English)"},
                {"value": "anushka", "label": "Anushka (Hindi/English)"},
                {"value": "priya", "label": "Priya (Hindi/English)"},
                {"value": "pooja", "label": "Pooja (Hindi/English)"},
                {"value": "ishita", "label": "Ishita (Hindi/English)"},
                {"value": "shreya", "label": "Shreya (Hindi/English)"},
                {"value": "meera", "label": "Meera (Hindi)"},
                {"value": "amelia", "label": "Amelia (English)"},
            ],
            "male": [
                {"value": "arjun", "label": "Arjun (Hindi/English)"},
                {"value": "rahul", "label": "Rahul (Hindi/English)"},
                {"value": "aditya", "label": "Aditya (Hindi/English)"},
                {"value": "kabir", "label": "Kabir (Hindi/English)"},
            ],
        }
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
