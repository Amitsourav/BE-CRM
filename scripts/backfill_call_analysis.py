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


async def process_one_id(call_id: uuid.UUID, *, apply: bool, reanalyze: bool) -> dict:
    """Process a single call by ID with its own DB session.

    Per-call sessions are slower but resilient to Supabase connection drops
    (the Korea region tends to drop idle / long-held connections from
    laptops). A drop now affects one call, not the whole batch.
    """
    # Capture string IDs upfront so even if the session detaches mid-run,
    # the report row still has identifiers.
    row = {
        "call_id": str(call_id),
        "lead_id": None,
        "transcript_len": 0,
        "lead_stage_before": None,
        "lead_stage_after": None,
        "ran_llm": False,
        "ai_sentiment": None,
        "ai_interest": None,
        "stage_advanced": False,
        "skipped_reason": None,
    }

    # Up to 3 attempts on connection-reset errors.
    for attempt in range(3):
        try:
            async with AsyncSessionLocal() as db:
                # Reload eager — avoid lazy loads that detach later.
                result = await db.execute(
                    select(CallAttempt)
                    .options(selectinload(CallAttempt.lead))
                    .where(CallAttempt.id == call_id)
                )
                call = result.scalar_one_or_none()
                if not call:
                    row["skipped_reason"] = "call disappeared"
                    return row

                lead = call.lead
                row["lead_id"] = str(call.lead_id)
                row["transcript_len"] = len(call.transcript or "")

                if not lead:
                    row["skipped_reason"] = "lead missing"
                    return row
                if lead.is_deleted:
                    row["skipped_reason"] = "lead soft-deleted"
                    return row
                if lead.current_stage in TERMINAL_STAGES:
                    row["skipped_reason"] = f"terminal ({lead.current_stage})"
                    return row

                row["lead_stage_before"] = lead.current_stage

                needs_llm = reanalyze or not (call.summary and call.summary.strip())
                if needs_llm:
                    row["ran_llm"] = True
                    analysis = await _analyze_call(call.transcript)
                    row["ai_sentiment"] = analysis.get("sentiment")
                    row["ai_interest"] = analysis.get("interest_level")
                    sentiment = analysis.get("sentiment", "neutral")
                    interest = analysis.get("interest_level", "low")
                    if apply:
                        call.summary = analysis.get("summary", "") or call.summary
                        call.sentiment = sentiment
                        confidence = analysis.get("confidence", 0)
                        try:
                            call.sentiment_score = max(0.0, min(1.0, float(confidence) / 100.0))
                        except (TypeError, ValueError):
                            call.sentiment_score = 0.0
                else:
                    sentiment = call.sentiment or "neutral"
                    if sentiment == "positive" and (call.sentiment_score or 0) >= 0.75:
                        interest = "high"
                    elif sentiment == "positive":
                        interest = "medium"
                    else:
                        interest = "low"
                    row["ai_sentiment"] = sentiment
                    row["ai_interest"] = interest

                if apply:
                    try:
                        call_agent = call.agent_id or call.telecaller_id
                        await _auto_update_lead_stage(
                            db, call, lead,
                            sentiment=sentiment,
                            interest_level=interest,
                            call_summary=call.summary or "",
                            call_agent_id=call_agent,
                        )
                        await db.commit()
                    except Exception as e:
                        row["skipped_reason"] = f"stage error: {str(e)[:80]}"
                        try:
                            await db.rollback()
                        except Exception:
                            pass
                        return row

                row["lead_stage_after"] = lead.current_stage
                row["stage_advanced"] = (
                    row["lead_stage_after"] != row["lead_stage_before"]
                )
                return row

        except Exception as e:
            err_msg = str(e)[:120]
            # Retry transient connection errors; bail on real bugs.
            if any(s in err_msg for s in (
                "ConnectionDoesNotExist", "Connection reset", "timeout",
                "MissingGreenlet", "connection was closed", "PoolTimeout",
            )) and attempt < 2:
                await asyncio.sleep(1 + attempt)  # 1s, 2s
                continue
            row["skipped_reason"] = f"unexpected: {err_msg}"
            return row

    row["skipped_reason"] = "exhausted retries"
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

    # Fetch only the IDs upfront. Cheap query that finishes quickly even
    # on a flaky connection. Then close the session before the long loop.
    candidate_ids: list[uuid.UUID] = []
    async with AsyncSessionLocal() as db:
        # Build the same predicate as fetch_candidates but project only id.
        q = (
            select(CallAttempt.id)
            .where(
                CallAttempt.transcript.isnot(None),
                CallAttempt.transcript != "",
                CallAttempt.call_duration_seconds.isnot(None),
                CallAttempt.call_duration_seconds > 10,
            )
            .order_by(CallAttempt.created_at.desc())
        )
        if args.since_days:
            cutoff = now_utc() - timedelta(days=args.since_days)
            q = q.where(CallAttempt.created_at >= cutoff)
        if company_id:
            q = q.where(CallAttempt.company_id == company_id)
        if not args.reanalyze:
            q = q.where(
                CallAttempt.summary.is_(None) | (CallAttempt.summary == "")
            )
        if args.limit:
            q = q.limit(args.limit)
        result = await db.execute(q)
        candidate_ids = [r[0] for r in result.all()]

    print(f"Found {len(candidate_ids)} candidate calls.\n")
    if not candidate_ids:
        return

    est_cost = len(candidate_ids) * 0.0002
    print(f"Expected LLM calls: ~{len(candidate_ids)}  (estimated cost: ${est_cost:.2f})\n")

    if not args.apply:
        print("⚠️  Dry run — no DB writes, no LLM calls.\n")
        print(f"  → Run with --apply to actually process all {len(candidate_ids)} calls.")
        return

    # APPLY — each call uses its own session; a connection drop affects
    # only that one call (with retry).
    rows = []
    advanced = 0
    ran_llm = 0
    skipped = 0
    qualified = 0
    for i, cid in enumerate(candidate_ids, 1):
        row = await process_one_id(cid, apply=True, reanalyze=args.reanalyze)
        rows.append(row)
        if row.get("ran_llm"):
            ran_llm += 1
        if row.get("stage_advanced"):
            advanced += 1
            if row.get("lead_stage_after") == "qualified_lead":
                qualified += 1
        if row.get("skipped_reason"):
            skipped += 1
        if i % 10 == 0 or i == len(candidate_ids):
            print(f"  ...processed {i}/{len(candidate_ids)}  "
                  f"(advanced {advanced}, qualified {qualified}, skipped {skipped})")

    print(f"\n{fmt_table(rows[:50])}")
    if len(rows) > 50:
        print(f"\n  (showing first 50 of {len(rows)} rows)")

    print(f"\n{'='*70}")
    print(f"  Total processed:    {len(rows)}")
    print(f"  LLM calls made:     {ran_llm}")
    print(f"  Stages advanced:    {advanced}")
    print(f"  → Qualified leads:  {qualified}")
    print(f"  Skipped/errored:    {skipped}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    asyncio.run(main())
