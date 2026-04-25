from __future__ import annotations

import json
import uuid
import logging
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.call_attempt import CallAttempt
from app.models.lead import Lead
from app.models.lead_stage_log import LeadStageLog
from app.core.constants import LeadStage, VALID_TRANSITIONS
from app.config import get_settings
from app.utils.date_helpers import now_utc

logger = logging.getLogger(__name__)
settings = get_settings()

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Expected transient failures from the OpenRouter/HTTP path — anything else
# (ImportError, AttributeError, SQL errors) should propagate so we notice.
_EXPECTED_AI_ERRORS = (httpx.HTTPError, json.JSONDecodeError, ValueError, KeyError)


async def post_call_pipeline(
    db: AsyncSession, call_id: uuid.UUID, company_id: uuid.UUID | None = None,
) -> None:
    """Run post-call AI pipeline: summary, sentiment, lead update.

    The caller may pass ``company_id`` for backward compatibility, but we
    always derive the tenant from the call row itself so a spoofed or stale
    ``company_id`` cannot cause writes against another tenant.
    """
    logger.info("[POST-CALL] Starting pipeline for call %s", call_id)

    # 1. Get call — trust only the row's own company_id.
    result = await db.execute(select(CallAttempt).where(CallAttempt.id == call_id))
    call = result.scalar_one_or_none()
    if not call:
        logger.warning("[POST-CALL] Call %s not found, skipping", call_id)
        return

    if company_id is not None and call.company_id != company_id:
        logger.warning(
            "[POST-CALL] Company mismatch for call %s (expected=%s, row=%s) — using row's value",
            call_id, company_id, call.company_id,
        )
    company_id = call.company_id

    if not call.transcript:
        logger.info("[POST-CALL] No transcript for call %s, skipping AI analysis", call_id)
        return

    # 2. Generate summary
    try:
        summary = await generate_summary(call.transcript)
        if summary:
            call.summary = summary
            logger.info("[POST-CALL] Summary generated for call %s", call_id)
    except _EXPECTED_AI_ERRORS:
        logger.exception("[POST-CALL] Summary generation failed for call %s", call_id)

    # 3. Analyze sentiment
    try:
        sentiment, score = await analyze_sentiment(call.transcript)
        call.sentiment = sentiment
        call.sentiment_score = score
        logger.info("[POST-CALL] Sentiment: %s (%.2f) for call %s", sentiment, score, call_id)
    except _EXPECTED_AI_ERRORS:
        logger.exception("[POST-CALL] Sentiment analysis failed for call %s", call_id)

    await db.commit()

    # 4. Auto update lead status based on sentiment
    try:
        await auto_update_lead_status(
            db,
            company_id=company_id,
            lead_id=call.lead_id,
            sentiment=call.sentiment,
            sentiment_score=call.sentiment_score,
            changed_by=call.agent_id,
        )
    except Exception:
        # Stage updates write a LeadStageLog row with FKs; don't let a bad
        # transition or FK error poison the whole pipeline.
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
    *,
    company_id: uuid.UUID,
    lead_id: uuid.UUID,
    sentiment: str | None,
    sentiment_score: float | None = None,
    changed_by: uuid.UUID,
) -> None:
    """Update lead stage based on call outcome.

    Conservative mapping — we only ever *advance* the lead or leave it where it
    was. We don't auto-close (lost) based on sentiment alone: that needs a
    lost_reason and usually human judgement. Transitions written are:

      lead                          → called      (call happened at all)
      called + positive ≥ 0.6       → connected   (real conversation)
      connected + positive ≥ 0.75   → qualified_lead

    Each change also writes a LeadStageLog entry so the timeline shows that
    the AI pipeline — not a human — moved the lead.
    """
    result = await db.execute(
        select(Lead).where(Lead.id == lead_id, Lead.company_id == company_id, Lead.is_deleted == False)  # noqa: E712
    )
    lead = result.scalar_one_or_none()
    if not lead:
        logger.warning(
            "[POST-CALL] Lead %s not found (deleted or wrong tenant) — skipping stage update",
            lead_id,
        )
        return

    try:
        current = LeadStage(lead.current_stage)
    except ValueError:
        logger.warning("[POST-CALL] Lead %s has unknown stage %r", lead_id, lead.current_stage)
        return

    target: LeadStage | None = None
    score = float(sentiment_score or 0.0)

    if current == LeadStage.LEAD:
        target = LeadStage.CALLED
    elif current == LeadStage.CALLED and sentiment == "positive" and score >= 0.6:
        target = LeadStage.CONNECTED
    elif current == LeadStage.CONNECTED and sentiment == "positive" and score >= 0.75:
        target = LeadStage.QUALIFIED_LEAD

    if target is None:
        return

    if target not in VALID_TRANSITIONS.get(current, []):
        logger.warning(
            "[POST-CALL] Skipping auto-transition %s→%s for lead %s (not in valid transitions)",
            current.value, target.value, lead_id,
        )
        return

    lead.current_stage = target.value
    if target == LeadStage.CONNECTED and not lead.connected_time:
        lead.connected_time = now_utc()

    db.add(LeadStageLog(
        company_id=company_id,
        lead_id=lead.id,
        from_stage=current.value,
        to_stage=target.value,
        changed_by=changed_by,
        conversation_notes=f"Auto-transition by post-call pipeline (sentiment={sentiment}, score={score:.2f})",
    ))
    await db.commit()
    logger.info(
        "[POST-CALL] Lead %s auto-transitioned %s → %s (sentiment=%s, score=%.2f)",
        lead_id, current.value, target.value, sentiment, score,
    )
