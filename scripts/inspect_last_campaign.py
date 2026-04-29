"""Show details of the most recent campaign run.

Reports campaign status, call counts (with the corrected definitions),
distribution of lead stages, and a sample of recent calls so you can
spot-check transcripts and the new structured AI extraction.

Usage:
    python -m scripts.inspect_last_campaign
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func

from app.db.session import AsyncSessionLocal
from app.models.campaign import Campaign
from app.models.campaign_lead import CampaignLead
from app.models.lead import Lead
from app.models.call_attempt import CallAttempt


async def main():
    async with AsyncSessionLocal() as db:
        # Most recent campaign by created_at
        result = await db.execute(
            select(Campaign).order_by(Campaign.created_at.desc()).limit(1)
        )
        campaign = result.scalar_one_or_none()
        if not campaign:
            print("No campaigns found.")
            return

        print(f"\n{'='*72}")
        print(f"  Last campaign: {campaign.name}")
        print(f"{'='*72}")
        print(f"  ID:              {campaign.id}")
        print(f"  Status:          {campaign.status}")
        print(f"  Created:         {campaign.created_at}")
        print(f"  Started:         {campaign.started_at}")
        print(f"  Completed:       {campaign.completed_at}")
        print(f"  Total leads:     {campaign.total_leads}")
        print(f"  Calls made (attempts): {campaign.calls_made}")
        print(f"  Calls connected: {campaign.calls_connected}")
        print(f"  Calls failed:    {campaign.calls_failed}")
        print(f"  Total cost USD:  {campaign.total_cost_usd}")

        # Campaign lead distribution
        cl_result = await db.execute(
            select(CampaignLead.status, func.count())
            .where(CampaignLead.campaign_id == campaign.id)
            .group_by(CampaignLead.status)
        )
        cl_stats = dict(cl_result.all())
        print(f"\n  Campaign lead status breakdown:")
        for s, c in sorted(cl_stats.items()):
            print(f"    {s:<12} {c}")

        # Stage distribution of leads in this campaign
        stage_result = await db.execute(
            select(Lead.current_stage, func.count())
            .join(CampaignLead, CampaignLead.lead_id == Lead.id)
            .where(CampaignLead.campaign_id == campaign.id)
            .group_by(Lead.current_stage)
        )
        stages = dict(stage_result.all())
        print(f"\n  Pipeline stage of those leads (current state):")
        for s, c in sorted(stages.items()):
            print(f"    {s:<16} {c}")

        # Calls actually made for this campaign
        call_result = await db.execute(
            select(
                func.count().label("total"),
                func.count().filter(CallAttempt.started_at.isnot(None)).label("connected"),
                func.count().filter(CallAttempt.transcript.isnot(None)).label("with_transcript"),
                func.count().filter(CallAttempt.summary.isnot(None)).filter(CallAttempt.summary != "").label("with_summary"),
                func.avg(CallAttempt.call_duration_seconds).label("avg_dur"),
                func.sum(CallAttempt.cost).label("total_cost"),
            )
            .join(CampaignLead, CampaignLead.last_call_id == CallAttempt.id)
            .where(CampaignLead.campaign_id == campaign.id)
        )
        cs = call_result.one()
        print(f"\n  Calls (this campaign):")
        print(f"    Total attempts:       {cs.total}")
        print(f"    Actually connected:   {cs.connected}")
        print(f"    With transcript:      {cs.with_transcript}")
        print(f"    With AI summary:      {cs.with_summary}")
        print(f"    Avg duration (sec):   {round(float(cs.avg_dur or 0), 1)}")
        print(f"    Total cost (USD):     {round(float(cs.total_cost or 0), 2)}")

        # Sample of 5 most recent connected calls
        sample_result = await db.execute(
            select(CallAttempt, Lead)
            .join(Lead, Lead.id == CallAttempt.lead_id)
            .join(CampaignLead, CampaignLead.last_call_id == CallAttempt.id)
            .where(
                CampaignLead.campaign_id == campaign.id,
                CallAttempt.started_at.isnot(None),
            )
            .order_by(CallAttempt.created_at.desc())
            .limit(5)
        )
        rows = sample_result.all()
        print(f"\n  Sample of 5 most recent connected calls:")
        if not rows:
            print(f"    (no connected calls yet)")
        for call, lead in rows:
            print(f"\n  ─ {lead.full_name}  {lead.phone}  stage={lead.current_stage}")
            print(f"    Duration: {call.call_duration_seconds}s  "
                  f"Sentiment: {call.sentiment}  Score: {call.sentiment_score}")
            transcript_len = len(call.transcript or "")
            summary_len = len(call.summary or "")
            print(f"    Transcript: {transcript_len} chars   Summary: {summary_len} chars")
            if call.summary:
                preview = call.summary.replace("\n", " ")[:280]
                print(f"    Summary preview: {preview}")
            if lead.custom_fields and "ai_last_call" in (lead.custom_fields or {}):
                ai = lead.custom_fields["ai_last_call"]
                facts = []
                for k in ("loan_amount", "college", "study_location",
                          "course", "intake", "next_action"):
                    if ai.get(k):
                        facts.append(f"{k}={ai[k]}")
                if ai.get("banks_tried"):
                    facts.append(f"banks_tried={ai['banks_tried']}")
                if facts:
                    print(f"    Extracted: {' | '.join(facts)}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
