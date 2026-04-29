"""Dump every transcript from a campaign for human / LLM review.

Read-only. Prints each call with: lead phone, name, duration, sentiment,
current stage, and the FULL transcript text. Sorted by duration desc so
the meatiest conversations come first.

Usage:
    python -m scripts.dump_campaign_transcripts
    python -m scripts.dump_campaign_transcripts --campaign-id <uuid>
    python -m scripts.dump_campaign_transcripts --min-duration 30
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.campaign import Campaign
from app.models.campaign_lead import CampaignLead
from app.models.lead import Lead
from app.models.call_attempt import CallAttempt


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", type=str, default=None)
    parser.add_argument("--min-duration", type=int, default=0,
                        help="Skip calls shorter than this (seconds)")
    parser.add_argument("--min-chars", type=int, default=0,
                        help="Skip transcripts shorter than this (chars)")
    args = parser.parse_args()

    async with AsyncSessionLocal() as db:
        if args.campaign_id:
            cid = uuid.UUID(args.campaign_id)
            result = await db.execute(select(Campaign).where(Campaign.id == cid))
        else:
            result = await db.execute(
                select(Campaign).order_by(Campaign.created_at.desc()).limit(1)
            )
        campaign = result.scalar_one_or_none()
        if not campaign:
            print("No campaign found")
            return

        print(f"# Campaign: {campaign.name}  ({campaign.id})")
        print(f"# Started: {campaign.started_at}\n")

        rows = (await db.execute(
            select(CallAttempt, Lead)
            .join(Lead, Lead.id == CallAttempt.lead_id)
            .join(CampaignLead, CampaignLead.last_call_id == CallAttempt.id)
            .where(
                CampaignLead.campaign_id == campaign.id,
                CallAttempt.transcript.isnot(None),
                CallAttempt.transcript != "",
            )
            .order_by(CallAttempt.call_duration_seconds.desc().nullslast())
        )).all()

        printed = 0
        for call, lead in rows:
            dur = call.call_duration_seconds or 0
            tlen = len(call.transcript or "")
            if dur < args.min_duration:
                continue
            if tlen < args.min_chars:
                continue
            printed += 1
            print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print(f"#{printed}  {lead.full_name}  {lead.phone}")
            print(f"  call_id={call.id}  lead_id={lead.id}")
            print(f"  duration={dur}s  transcript_len={tlen}  "
                  f"stage={lead.current_stage}  sentiment={call.sentiment}  "
                  f"score={call.sentiment_score}")
            ai = (lead.custom_fields or {}).get("ai_last_call") or {}
            if ai:
                facts = []
                for k in ("loan_amount", "college", "study_location",
                          "course", "intake", "next_action"):
                    v = ai.get(k)
                    if v:
                        facts.append(f"{k}={v}")
                if ai.get("banks_tried"):
                    facts.append(f"banks_tried={ai['banks_tried']}")
                if facts:
                    print(f"  extracted: {' | '.join(facts)}")
            print()
            print(call.transcript)
            print()

        print(f"\n# Total transcripts printed: {printed}")


if __name__ == "__main__":
    asyncio.run(main())
