"""
Backfill AI summary + sentiment + lead stage updates for past AI calls.

Re-runs the post-call pipeline against every CallAttempt that has a
transcript. Useful when the AI summary path was failing silently and
calls accumulated with empty summaries / un-advanced lead stages.

Reuses the production logic from app/api/v1/voice.py (_analyze_call,
_auto_update_lead_stage) so behavior matches new calls exactly.

Usage:
    # Dry run — see what would change, no writes:
    python -m scripts.backfill_call_analysis

    # Actually apply the changes:
    python -m scripts.backfill_call_analysis --apply

    # Limit to recent calls:
    python -m scripts.backfill_call_analysis --since-days 14 --apply

    # Limit to one company:
    python -m scripts.backfill_call_analysis --company-id <uuid> --apply

    # Force re-analysis even when a summary already exists:
    python -m scripts.backfill_call_analysis --reanalyze --apply

    # Preview a few candidates first:
    python -m scripts.backfill_call_analysis --limit 10

Stage advancement rules (matches voice.py:_auto_update_lead_stage):
    - lead → called           (call connected at all)
    - called → connected      (positive sentiment OR call connected)
    - connected → qualified   (positive sentiment AND high interest)
    Never moves backward. Never auto-marks lost. Steps through stages
    one at a time. Writes a LeadStageLog entry per step.

Cost: each --reanalyze call hits OpenRouter ($~0.0001/call on gpt-4o-mini).
Without --reanalyze, only calls with empty summaries get a fresh LLM hit.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import timedelta
from typing import Optional

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func, and_
from sqlalchemy.orm import selectinload

from app.db.session import AsyncSessionLocal
from app.models.call_attempt import CallAttempt
from app.models.lead import Lead
from app.models.lead_stage_log import LeadStageLog
from app.utils.date_helpers import now_utc

# Re-use production logic (do NOT duplicate)
from app.api.v1.voice import _analyze_call, _auto_update_lead_stage


# Stages we'll consider "needs processing" — qualified/won/lost are terminal
# and we don't want to interfere with leads that humans have already moved.
TERMINAL_STAGES = {"won", "lost"}


async def fetch_candidates(
    db, *, since_days: Optional[int], company_id: Optional[uuid.UUID],
    limit: Optional[int], reanalyze: bool,
):
    """Pick CallAttempts that need backfilling.

    Eligible if:
      - transcript is non-empty (need something to analyze)
      - call_duration_seconds > 10 (filter out flash hangups)
      - lead exists and is not in a terminal stage
      - either summary is empty/marker-style OR --reanalyze is set
    """
    q = (
        select(CallAttempt)
        .options(selectinload(CallAttempt.lead))
        .where(
            CallAttempt.transcript.isnot(None),
            CallAttempt.transcript != "",
            CallAttempt.call_duration_seconds.isnot(None),
            CallAttempt.call_duration_seconds > 10,
        )
        .order_by(CallAttempt.created_at.desc())
    )
    if since_days:
        cutoff = now_utc() - timedelta(days=since_days)
        q = q.where(CallAttempt.created_at >= cutoff)
    if company_id:
        q = q.where(CallAttempt.company_id == company_id)
    if not reanalyze:
        # Only re-process calls that look unprocessed
        q = q.where(
            and_(
                # Empty / null summary OR a marker from the new code path
                CallAttempt.summary.is_(None) | (CallAttempt.summary == "")
            )
        )
    if limit:
        q = q.limit(limit)

    result = await db.execute(q)
    return result.scalars().all()


async def process_one(db, call: CallAttempt, *, apply: bool, reanalyze: bool) -> dict:
    """Process a single call. Returns a row for the report."""
    row = {
        "call_id": str(call.id),
        "lead_id": str(call.lead_id),
        "started_at": call.started_at.isoformat() if call.started_at else None,
        "duration": call.call_duration_seconds,
        "transcript_len": len(call.transcript or ""),
        "old_summary_len": len(call.summary or ""),
        "old_sentiment": call.sentiment,
        "lead_stage_before": None,
        "lead_stage_after": None,
        "ran_llm": False,
        "ai_sentiment": None,
        "ai_interest": None,
        "stage_advanced": False,
        "skipped_reason": None,
    }

    lead = call.lead
    if not lead:
        row["skipped_reason"] = "lead missing"
        return row
    if lead.is_deleted:
        row["skipped_reason"] = "lead soft-deleted"
        return row
    if lead.current_stage in TERMINAL_STAGES:
        row["skipped_reason"] = f"lead in terminal stage ({lead.current_stage})"
        return row

    row["lead_stage_before"] = lead.current_stage

    # Decide whether to run LLM
    needs_llm = reanalyze or not (call.summary and call.summary.strip())
    if needs_llm:
        row["ran_llm"] = True
        analysis = await _analyze_call(call.transcript)
        row["ai_sentiment"] = analysis.get("sentiment")
        row["ai_interest"] = analysis.get("interest_level")
        if apply:
            call.summary = analysis.get("summary", "") or call.summary
            call.sentiment = analysis.get("sentiment") or call.sentiment
            confidence = analysis.get("confidence", 0)
            try:
                call.sentiment_score = max(0.0, min(1.0, float(confidence) / 100.0))
            except (TypeError, ValueError):
                call.sentiment_score = 0.0
        sentiment = analysis.get("sentiment", "neutral")
        interest = analysis.get("interest_level", "low")
    else:
        sentiment = call.sentiment or "neutral"
        # We don't store interest_level on CallAttempt — infer conservatively
        # from sentiment + score so existing rows don't get falsely qualified.
        if sentiment == "positive" and (call.sentiment_score or 0) >= 0.75:
            interest = "high"
        elif sentiment == "positive":
            interest = "medium"
        else:
            interest = "low"
        row["ai_sentiment"] = sentiment
        row["ai_interest"] = interest

    if apply:
        # _auto_update_lead_stage is the same function the live hangup handler
        # uses for new calls. It will:
        #   - never move stage backward
        #   - step through (lead→called→connected→qualified) one at a time
        #   - write a LeadStageLog row per step with conversation_notes
        try:
            call_agent = call.agent_id or call.telecaller_id
            await _auto_update_lead_stage(
                db, call, lead,
                sentiment=sentiment,
                interest_level=interest,
                call_summary=call.summary or "",
                call_agent_id=call_agent,
            )
        except Exception as e:
            row["skipped_reason"] = f"stage update error: {e}"
            await db.rollback()
            return row

        await db.commit()
        await db.refresh(lead)

    row["lead_stage_after"] = lead.current_stage
    row["stage_advanced"] = (
        row["lead_stage_after"] != row["lead_stage_before"]
    )
    return row


def fmt_table(rows: list[dict]) -> str:
    """Compact summary table for the console."""
    if not rows:
        return "(no candidates)"
    lines = []
    cols = [
        ("call_id", 8), ("lead_stage_before", 12), ("lead_stage_after", 12),
        ("ai_sentiment", 9), ("ai_interest", 7),
        ("ran_llm", 5), ("stage_advanced", 8),
        ("transcript_len", 6), ("skipped_reason", 28),
    ]
    header = "  ".join(f"{label[:w]:<{w}}" for label, w in cols)
    lines.append(header)
    lines.append("-" * len(header))
    for r in rows:
        lines.append("  ".join(
            f"{str(r.get(label, '') or '')[:w]:<{w}}" for label, w in cols
        ))
    return "\n".join(lines)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Actually write changes. Default = dry run.")
    parser.add_argument("--reanalyze", action="store_true",
                        help="Re-run LLM even if summary exists.")
    parser.add_argument("--since-days", type=int, default=None,
                        help="Only consider calls newer than N days.")
    parser.add_argument("--company-id", type=str, default=None,
                        help="Restrict to one tenant UUID.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Stop after N candidates (debug / preview).")
    args = parser.parse_args()

    company_id = uuid.UUID(args.company_id) if args.company_id else None

    print(f"\n{'='*70}")
    print(f"  Backfill mode:        {'APPLY (writes!)' if args.apply else 'DRY RUN (read-only)'}")
    print(f"  Re-analyze existing:  {args.reanalyze}")
    print(f"  Since days:           {args.since_days or 'all time'}")
    print(f"  Company:              {company_id or 'all tenants'}")
    print(f"  Limit:                {args.limit or 'no limit'}")
    print(f"{'='*70}\n")

    async with AsyncSessionLocal() as db:
        candidates = await fetch_candidates(
            db,
            since_days=args.since_days,
            company_id=company_id,
            limit=args.limit,
            reanalyze=args.reanalyze,
        )
        print(f"Found {len(candidates)} candidate calls.\n")

        if not candidates:
            return

        # Cost preview if we'd run LLM on all of them
        will_run_llm = sum(
            1 for c in candidates
            if args.reanalyze or not (c.summary and c.summary.strip())
        )
        # gpt-4o-mini ≈ $0.0002/call worst-case (transcript 3000 chars in, 400 tokens out)
        est_cost = will_run_llm * 0.0002
        print(f"Expected LLM calls: {will_run_llm}  (estimated cost: ${est_cost:.2f})\n")

        if not args.apply:
            print("⚠️  Dry run — no DB writes, no LLM calls. Showing first 25 candidates:\n")
            preview = []
            for c in candidates[:25]:
                preview.append({
                    "call_id": str(c.id),
                    "lead_stage_before": c.lead.current_stage if c.lead else None,
                    "lead_stage_after": "(would advance)",
                    "ai_sentiment": c.sentiment or "(needs LLM)",
                    "ai_interest": "?",
                    "ran_llm": "would" if (args.reanalyze or not c.summary) else "no",
                    "stage_advanced": "?",
                    "transcript_len": len(c.transcript or ""),
                    "skipped_reason": (
                        "lead missing" if not c.lead else
                        "lead deleted" if c.lead.is_deleted else
                        "terminal stage" if c.lead.current_stage in TERMINAL_STAGES else
                        ""
                    ),
                })
            print(fmt_table(preview))
            print(f"\n  → Run with --apply to actually process all {len(candidates)} calls.")
            return

        # APPLY — process each call, commit per-call so a single failure
        # doesn't unwind the whole batch.
        rows = []
        advanced = 0
        ran_llm = 0
        skipped = 0
        for i, call in enumerate(candidates, 1):
            try:
                row = await process_one(db, call, apply=True, reanalyze=args.reanalyze)
            except Exception as e:
                row = {"call_id": str(call.id), "skipped_reason": f"unexpected: {e}"}
                await db.rollback()
            rows.append(row)
            if row.get("ran_llm"):
                ran_llm += 1
            if row.get("stage_advanced"):
                advanced += 1
            if row.get("skipped_reason"):
                skipped += 1
            if i % 10 == 0:
                print(f"  ...processed {i}/{len(candidates)}")

        print(f"\n{fmt_table(rows[:50])}")
        if len(rows) > 50:
            print(f"\n  (showing first 50 of {len(rows)} rows)")

        print(f"\n{'='*70}")
        print(f"  Total processed:  {len(rows)}")
        print(f"  LLM calls made:   {ran_llm}")
        print(f"  Stages advanced:  {advanced}")
        print(f"  Skipped:          {skipped}")
        print(f"{'='*70}\n")


if __name__ == "__main__":
    asyncio.run(main())
