from __future__ import annotations

import uuid
from typing import Optional
from datetime import datetime
from sqlalchemy import String, Text, Boolean, Float, Integer, DateTime, text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.models.base import Base, TimestampMixin


class AIAgent(Base, TimestampMixin):
    __tablename__ = "ai_agents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True
    )

    # SECTION 1 — IDENTITY
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False, server_default=text("'sales'"))
    tone: Mapped[str] = mapped_column(String(50), nullable=False, server_default=text("'friendly'"))
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    # SECTION 2 — PROMPT
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    welcome_message: Mapped[str] = mapped_column(
        String(300), nullable=False, server_default=text("'Hello! Am I speaking with {name}?'")
    )
    final_message_en: Mapped[str] = mapped_column(
        String(300), nullable=False, server_default=text("'Thank you for your time! Have a great day. Goodbye!'")
    )
    final_message_hi: Mapped[str] = mapped_column(
        String(300), nullable=False, server_default=text("'Bahut shukriya! Aapka din achha rahe. Alvida!'")
    )
    silence_message_en: Mapped[str] = mapped_column(
        String(200), nullable=False, server_default=text("'Hey, are you still there?'")
    )
    silence_message_hi: Mapped[str] = mapped_column(
        String(200), nullable=False, server_default=text("'Hello? Kya aap abhi bhi wahan hain?'")
    )

    # SECTION 3 — LLM
    llm_provider: Mapped[str] = mapped_column(String(50), nullable=False, server_default=text("'openrouter'"))
    llm_model: Mapped[str] = mapped_column(String(100), nullable=False, server_default=text("'openai/gpt-4o-mini'"))
    llm_temperature: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0.8"))
    llm_max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("100"))

    # SECTION 4 — STT (LISTENING)
    stt_provider: Mapped[str] = mapped_column(String(50), nullable=False, server_default=text("'sarvam'"))
    stt_model: Mapped[str] = mapped_column(String(100), nullable=False, server_default=text("'saaras:v3'"))
    stt_keywords: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # SECTION 5 — TTS (VOICE)
    tts_provider: Mapped[str] = mapped_column(String(50), nullable=False, server_default=text("'sarvam'"))
    tts_model: Mapped[str] = mapped_column(String(100), nullable=False, server_default=text("'bulbul:v3'"))
    tts_voice: Mapped[str] = mapped_column(String(100), nullable=False, server_default=text("'simran'"))
    tts_gender: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'female'"))
    tts_speed: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("1.0"))
    tts_buffer_size: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("200"))
    tts_stability: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0.5"))
    tts_similarity_boost: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0.75"))

    # DUAL TTS SUPPORT (optional, falls back to tts_provider/model/voice)
    tts_provider_english: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, default=None)
    tts_model_english: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default=None)
    tts_voice_english: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default=None)
    tts_provider_hindi: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, default=None)
    tts_model_hindi: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default=None)
    tts_voice_hindi: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default=None)

    # SECTION 6 — LANGUAGE SWITCHING
    primary_language: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'en'"))
    secondary_language: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'hi'"))
    auto_language_switch: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    language_style: Mapped[str] = mapped_column(String(50), nullable=False, server_default=text("'hinglish'"))

    # SECTION 7 — CALL TIMING
    endpointing_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("250"))
    linear_delay_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("400"))
    words_before_interrupt: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))
    max_response_words: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("25"))
    precise_transcript: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    # SECTION 8 — TELEPHONY
    telephony_provider: Mapped[str] = mapped_column(String(50), nullable=False, server_default=text("'plivo'"))
    phone_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    call_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("600"))
    hangup_on_silence_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("10"))
    call_start_time: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'09:00'"))
    call_end_time: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'19:00'"))
    restrict_call_hours: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    voicemail_detection: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    # SECTION 9 — AUDIO QUALITY
    noise_cancellation: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    noise_cancellation_level: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("60"))
    ambient_noise: Mapped[str] = mapped_column(String(50), nullable=False, server_default=text("'office-ambience'"))
    silence_detection_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("9"))

    # SECTION 11 — WEBHOOK
    webhook_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # SOFT DELETE
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # RELATIONSHIPS
    company = relationship("Company", back_populates="ai_agents")
    calls = relationship("CallAttempt", back_populates="ai_agent")
    creator = relationship("Profile", foreign_keys=[created_by])
