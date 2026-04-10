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
# Sarvam bulbul:v3 authoritative speaker catalog, confirmed from Sarvam's
# own model-specific 400 error on 2026-04-09:
#
#   "Speaker '...' is not compatible with model bulbul:v3.
#    Available speakers for bulbul:v3 are: aditya, ritu, ashutosh,
#    priya, neha, rahul, pooja, rohan, simran, kavya, amit, dev, ..."
#
# Agent 3 has been confirmed working with tts_voice=simran (see
# TURN_TIMING logs). simran is the safest default.
#
# Voices that are NOT compatible with bulbul:v3 (from repeated 400s):
#   anushka, abhilash, manisha, vidya, arya, karun, hitesh,
#   meera, pavithra, maitreyi, misha, diya, maya, arjun, amol,
#   amartya, arvind, neel, vian
# (these belong to other bulbul model versions; re-listing them here
# will cause silent calls)
#
# If you upgrade/downgrade the bulbul model version, the voice list here
# MUST be updated to match. Paste-testing a voice? Set it on an agent,
# make a call, and watch railway logs for 'sarvam TTS failed status=400'.
PROVIDER_OPTIONS = {
    "stt_providers": [
        {"value": "sarvam", "label": "Sarvam AI (Hindi+English best)"},
        {"value": "deepgram", "label": "Deepgram (English reliable)"},
        {"value": "openai", "label": "OpenAI Whisper/GPT"},
        {"value": "azure", "label": "Azure STT (not yet wired)"},
    ],
    "stt_models": {
        "sarvam": [
            {"value": "saaras:v3", "label": "saaras:v3 (recommended, multi-mode)"},
            {"value": "saarika:v2.5", "label": "saarika:v2.5 (legacy, being deprecated)"},
            {"value": "saarika:v2", "label": "saarika:v2 (legacy)"},
            {"value": "saaras:v2", "label": "saaras:v2 (legacy)"},
        ],
        "deepgram": [
            {"value": "nova-3", "label": "Nova-3 (latest, best Hindi)"},
            {"value": "nova-2-general", "label": "Nova-2 General"},
            {"value": "nova-2-meeting", "label": "Nova-2 Meeting"},
            {"value": "enhanced-general", "label": "Enhanced General"},
        ],
        "openai": [
            {"value": "gpt-4o-mini-transcribe", "label": "GPT-4o Mini Transcribe (recommended)"},
            {"value": "gpt-4o-transcribe", "label": "GPT-4o Transcribe"},
            {"value": "whisper-1", "label": "Whisper-1 (legacy)"},
        ],
        "azure": [
            {"value": "en-IN", "label": "English (India)"},
            {"value": "hi-IN", "label": "Hindi (India)"},
        ],
    },
    "tts_providers": [
        {"value": "sarvam", "label": "Sarvam AI (Indian voices, natural)"},
        {"value": "smallest", "label": "Smallest AI (fast, Hindi+English)"},
        {"value": "elevenlabs", "label": "ElevenLabs (premium, not yet wired)"},
        {"value": "cartesia", "label": "Cartesia (low latency, not yet wired)"},
    ],
    # Per-provider TTS model catalog consumed by the dashboard.
    # Frontend reads options.tts_models?.[provider] for the dropdown.
    # NOTE: Sarvam catalog must match the PROVIDER_OPTIONS.voices.sarvam
    # set — each voice is tied to a specific bulbul version. Mixing a
    # voice with the wrong model version produces HTTP 400 / silent calls.
    "tts_models": {
        "sarvam": [
            {"value": "bulbul:v3", "label": "bulbul:v3 (latest, 35+ voices)"},
            {"value": "bulbul:v2", "label": "bulbul:v2 (older, different voices)"},
        ],
        "smallest": [
            {"value": "lightning-v3", "label": "Lightning v3 (latest, Hindi support)"},
            {"value": "lightning-v2", "label": "Lightning v2"},
            {"value": "lightning", "label": "Lightning v1 (legacy)"},
        ],
        "elevenlabs": [
            {"value": "eleven_turbo_v2_5", "label": "Turbo v2.5 (fast)"},
            {"value": "eleven_multilingual_v2", "label": "Multilingual v2 (best quality)"},
            {"value": "eleven_flash_v2_5", "label": "Flash v2.5 (ultra low latency)"},
        ],
        "cartesia": [
            {"value": "sonic-2", "label": "Sonic 2 (latest)"},
            {"value": "sonic", "label": "Sonic (legacy)"},
        ],
    },
    # Gender toggle for TTS voice dropdowns
    "tts_genders": [
        {"value": "female", "label": "Female"},
        {"value": "male", "label": "Male"},
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
            {"value": "simran", "label": "Simran (Female Indian English)"},
            {"value": "priya", "label": "Priya (Female Indian English)"},
            {"value": "neha", "label": "Neha (Female Indian English)"},
            {"value": "pooja", "label": "Pooja (Female Indian English)"},
            {"value": "rahul", "label": "Rahul (Male Indian English)"},
            {"value": "aditya", "label": "Aditya (Male Indian English)"},
        ],
    },
    "tts_providers_hindi": [
        {"value": "sarvam", "label": "Sarvam AI (best Hindi quality)"},
        {"value": "smallest", "label": "Smallest AI (fast Hindi)"},
    ],
    "tts_voices_hindi": {
        "sarvam": [
            {"value": "simran", "label": "Simran (Female Hindi)"},
            {"value": "priya", "label": "Priya (Female Hindi)"},
            {"value": "neha", "label": "Neha (Female Hindi)"},
            {"value": "pooja", "label": "Pooja (Female Hindi)"},
            {"value": "ritu", "label": "Ritu (Female Hindi)"},
            {"value": "kavya", "label": "Kavya (Female Hindi)"},
            {"value": "ishita", "label": "Ishita (Female Hindi)"},
            {"value": "shreya", "label": "Shreya (Female Hindi)"},
            {"value": "tanya", "label": "Tanya (Female Hindi)"},
            {"value": "rahul", "label": "Rahul (Male Hindi)"},
            {"value": "rohan", "label": "Rohan (Male Hindi)"},
            {"value": "aditya", "label": "Aditya (Male Hindi)"},
            {"value": "ashutosh", "label": "Ashutosh (Male Hindi)"},
            {"value": "shubh", "label": "Shubh (Male Hindi)"},
        ],
        "smallest": [
            {"value": "mithali", "label": "Mithali (Female Hindi, fast)"},
        ],
    },
    "llm_providers": [
        {"value": "openrouter", "label": "OpenRouter"},
        {"value": "openai", "label": "OpenAI"},
        {"value": "anthropic", "label": "Anthropic"},
    ],
    "llm_models": [
        # Groq-backed models on OpenRouter — lowest first-token latency.
        # OpenRouter auto-routes these to the fastest available provider
        # (usually Groq) which runs on custom LPU hardware (~200ms TTFT).
        {"value": "meta-llama/llama-3.3-70b-instruct", "label": "Llama 3.3 70B (fastest, Groq)"},
        {"value": "meta-llama/llama-3.1-8b-instruct", "label": "Llama 3.1 8B (ultra-fast, smaller)"},
        {"value": "google/gemini-flash-1.5-8b", "label": "Gemini Flash 8B (very fast)"},
        # OpenAI — higher quality, higher latency
        {"value": "openai/gpt-4o-mini", "label": "GPT-4o Mini (balanced)"},
        {"value": "openai/gpt-4.1-mini", "label": "GPT-4.1 Mini (latest)"},
        {"value": "openai/gpt-4.1-nano", "label": "GPT-4.1 Nano (cheapest)"},
        {"value": "openai/gpt-4o", "label": "GPT-4o (powerful)"},
        {"value": "openai/gpt-4.1", "label": "GPT-4.1 (latest powerful)"},
        # Anthropic — reasoning/quality
        {"value": "anthropic/claude-3-haiku-20240307", "label": "Claude Haiku (fast)"},
    ],
    # Gender-indexed voice catalog consumed by the dashboard
    # options.voices?.[provider]?.[gender] dropdown. Keep in sync with
    # the confirmed-working set in the comment at top of this file.
    "voices": {
        "sarvam": {
            "female": [
                {"value": "simran", "label": "Simran"},
                {"value": "priya", "label": "Priya"},
                {"value": "neha", "label": "Neha"},
                {"value": "pooja", "label": "Pooja"},
                {"value": "ritu", "label": "Ritu"},
                {"value": "kavya", "label": "Kavya"},
                {"value": "ishita", "label": "Ishita"},
                {"value": "shreya", "label": "Shreya"},
                {"value": "tanya", "label": "Tanya"},
                {"value": "roopa", "label": "Roopa"},
                {"value": "shruti", "label": "Shruti"},
                {"value": "suhani", "label": "Suhani"},
                {"value": "kavitha", "label": "Kavitha"},
                {"value": "rupali", "label": "Rupali"},
            ],
            "male": [
                {"value": "rahul", "label": "Rahul"},
                {"value": "aditya", "label": "Aditya"},
                {"value": "ashutosh", "label": "Ashutosh"},
                {"value": "rohan", "label": "Rohan"},
                {"value": "amit", "label": "Amit"},
                {"value": "dev", "label": "Dev"},
                {"value": "shubh", "label": "Shubh"},
                {"value": "ratan", "label": "Ratan"},
                {"value": "varun", "label": "Varun"},
                {"value": "manan", "label": "Manan"},
                {"value": "sumit", "label": "Sumit"},
                {"value": "kabir", "label": "Kabir"},
                {"value": "vijay", "label": "Vijay"},
                {"value": "mohit", "label": "Mohit"},
                {"value": "sunny", "label": "Sunny"},
            ],
        },
        "smallest": {
            "female": [
                {"value": "mithali", "label": "Mithali (Hindi/English)"},
                {"value": "emily", "label": "Emily (English)"},
                {"value": "sarah", "label": "Sarah (English)"},
                {"value": "luna", "label": "Luna (English)"},
            ],
            "male": [
                {"value": "john", "label": "John (English)"},
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
        {"value": "mirror_hinglish", "label": "English→English, Hindi→Hinglish (recommended)"},
        {"value": "hinglish", "label": "Hinglish always (Hindi+English mix)"},
        {"value": "mirror_user", "label": "Mirror user language (English or pure Hindi)"},
        {"value": "primary_only", "label": "Always primary language"},
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
