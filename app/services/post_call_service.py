from __future__ import annotations

import json
import uuid
import logging
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.call_attempt import CallAttempt
from app.models.lead import Lead
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


async def post_call_pipeline(
    db: AsyncSession, call_id: uuid.UUID, company_id: uuid.UUID,
) -> None:
    """Run post-call AI pipeline: summary, sentiment, lead update."""
    logger.info("[POST-CALL] Starting pipeline for call %s", call_id)

    # 1. Get call
    result = await db.execute(
        select(CallAttempt).where(
            CallAttempt.id == call_id,
            CallAttempt.company_id == company_id,
        )
    )
    call = result.scalar_one_or_none()
    if not call:
        logger.warning("[POST-CALL] Call %s not found, skipping", call_id)
        return

    if not call.transcript:
        logger.info("[POST-CALL] No transcript for call %s, skipping AI analysis", call_id)
        return

    # 2. Generate summary
    try:
        summary = await generate_summary(call.transcript)
        if summary:
            call.summary = summary
            logger.info("[POST-CALL] Summary generated for call %s", call_id)
    except Exception:
        logger.exception("[POST-CALL] Summary generation failed for call %s", call_id)

    # 3. Analyze sentiment
    try:
        sentiment, score = await analyze_sentiment(call.transcript)
        call.sentiment = sentiment
        call.sentiment_score = score
        logger.info("[POST-CALL] Sentiment: %s (%.2f) for call %s", sentiment, score, call_id)
    except Exception:
        logger.exception("[POST-CALL] Sentiment analysis failed for call %s", call_id)

    await db.commit()

    # 4. Auto update lead status
    try:
        await auto_update_lead_status(db, company_id, call.lead_id, call.sentiment)
    except Exception:
        logger.exception("[POST-CALL] Lead status update failed for call %s", call_id)

    logger.info("[POST-CALL] Pipeline complete for call %s", call_id)


async def _call_openrouter(prompt: str, max_tokens: int = 200) -> str | None:
    """Call OpenRouter API with a prompt. Returns response text or None."""
    if not settings.openrouter_api_key:
        logger.warning("[POST-CALL] OPENROUTER_API_KEY not set, skipping AI call")
        return None

    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "openai/gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                OPENROUTER_URL, headers=headers, json=payload, timeout=30.0,
            )
        if response.status_code == 200:
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()
        logger.error("[POST-CALL] OpenRouter error %d: %s", response.status_code, response.text[:200])
        return None
    except Exception as e:
        logger.error("[POST-CALL] OpenRouter request failed: %s", e)
        return None


async def generate_summary(transcript: str) -> str | None:
    """Generate a 2-3 sentence summary of the call."""
    if not transcript:
        return None

    prompt = (
        "Summarize this call transcript in 2-3 sentences. "
        "Focus on: what was discussed, the outcome, and any next steps.\n\n"
        f"Transcript:\n{transcript[:3000]}"
    )
    return await _call_openrouter(prompt, max_tokens=200)


async def analyze_sentiment(transcript: str) -> tuple[str, float]:
    """Analyze call sentiment. Returns (sentiment, score)."""
    if not transcript:
        return "neutral", 0.5

    prompt = (
        "Analyze the sentiment of this call transcript. "
        "Return ONLY valid JSON with no other text:\n"
        '{"sentiment": "positive", "score": 0.8}\n\n'
        "sentiment must be: positive, neutral, or negative\n"
        "score must be: 0.0 to 1.0\n\n"
        f"Transcript:\n{transcript[:2000]}"
    )
    result = await _call_openrouter(prompt, max_tokens=50)
    if not result:
        return "neutral", 0.5

    try:
        # Strip markdown code blocks if present
        cleaned = result.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        parsed = json.loads(cleaned)
        sentiment = parsed.get("sentiment", "neutral")
        score = float(parsed.get("score", 0.5))
        if sentiment not in ("positive", "neutral", "negative"):
            sentiment = "neutral"
        score = max(0.0, min(1.0, score))
        return sentiment, score
    except (json.JSONDecodeError, ValueError, KeyError):
        logger.warning("[POST-CALL] Failed to parse sentiment JSON: %s", result[:100])
        return "neutral", 0.5


async def auto_update_lead_status(
    db: AsyncSession,
    company_id: uuid.UUID,
    lead_id: uuid.UUID,
    sentiment: str | None,
) -> None:
    """Update lead stage based on call outcome."""
    result = await db.execute(
        select(Lead).where(Lead.id == lead_id, Lead.company_id == company_id)
    )
    lead = result.scalar_one_or_none()
    if not lead:
        return

    # Only auto-update if lead is still in early stages
    if lead.current_stage in ("lead",):
        lead.current_stage = "called"
        await db.commit()
        logger.info("[POST-CALL] Lead %s auto-moved to 'called'", lead_id)
