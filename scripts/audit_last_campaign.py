"""Full audit of the most recent campaign.

Covers every angle needed to know if the campaign performed well:
- Configuration (hours, retries, agent)
- Dispatch + retry metrics
- Connection quality (pickup vs real conversation)
- Transcript distribution + sample
- AI summary / sentiment / extraction success rate
- Lead progression (campaign-scoped, not all leads)
- Cost breakdown (per attempt, per connect, per qualified)
- Time-of-day distribution
- Data-quality flags (missing names, weird durations, etc.)
- A list of the leads worth a human follow-up

Read-only. Produces a long report; pipe through `less` if you like.

Usage:
    python -m scripts.audit_last_campaign
    python -m scripts.audit_last_campaign --campaign-id <uuid>
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from collections import Counter
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func, and_, or_

from app.db.session import AsyncSessionLocal
from app.models.campaign import Campaign
from app.models.campaign_lead import CampaignLead
from app.models.lead import Lead
from app.models.lead_stage_log import LeadStageLog
from app.models.call_attempt import CallAttempt
from app.models.ai_agent import AIAgent


def _bar(n: int, total: int, width: int = 30) -> str:
    if total <= 0:
        return ""
    filled = int(round(n / total * width))
    return "█" * filled + "·" * (width - filled)


def _pct(n: int, total: int) -> str:
    return f"{(n / total * 100):.1f}%" if total else "0.0%"


def _hr(t: str) -> None:
    print(f"\n{'='*72}\n  {t}\n{'='*72}")


def _sub(t: str) -> None:
    print(f"\n── {t} ──")


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", type=str, default=None,
                        help="UUID of campaign to audit (default: most recent)")
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
            print("No campaign found.")
            return

        # ── Section 1: Configuration ────────────────────────────────
        _hr(f"Campaign: {campaign.name}")
        print(f"  ID:                  {campaign.id}")
        print(f"  Status:              {campaign.status}")
        print(f"  Created:             {campaign.created_at}")
        print(f"  Started:             {campaign.started_at}")
        print(f"  Completed/Stopped:   {campaign.completed_at}")
        if campaign.started_at and campaign.completed_at:
            dur = campaign.completed_at - campaign.started_at
            print(f"  Run duration:        {dur}")
        print(f"  Daily window:        {campaign.daily_start_time} – "
              f"{campaign.daily_end_time}  (tz={campaign.timezone})")
        print(f"  Skip weekends:       {campaign.skip_weekends}")
        print(f"  Max concurrent:      {campaign.max_concurrent_calls}")
        print(f"  Max retries:         {campaign.max_retries}  "
              f"(gap {campaign.retry_gap_hours}h)")

        # Agent
        if campaign.ai_agent_id:
            agent_result = await db.execute(
                select(AIAgent).where(AIAgent.id == campaign.ai_agent_id)
            )
            agent = agent_result.scalar_one_or_none()
            if agent:
                print(f"\n  AI Agent:            {agent.name}")
                print(f"    LLM model:         {getattr(agent, 'llm_model', None)}")
                print(f"    STT provider:      {getattr(agent, 'stt_provider', None)}")
                print(f"    TTS provider:      {getattr(agent, 'tts_provider', None)}")
                print(f"    TTS voice:         {getattr(agent, 'tts_voice', None)}")
                print(f"    Active:            {agent.is_active}")

        # ── Section 2: CampaignLead status breakdown ─────────────────
        _hr("Campaign-lead status")
        cl_rows = (await db.execute(
            select(CampaignLead.status, func.count())
            .where(CampaignLead.campaign_id == campaign.id)
            .group_by(CampaignLead.status)
        )).all()
        cl_total = sum(c for _, c in cl_rows)
        for s, c in sorted(cl_rows, key=lambda x: -x[1]):
            print(f"  {s:<12} {c:>5}  {_pct(c, cl_total):>6}  {_bar(c, cl_total)}")
        print(f"  {'TOTAL':<12} {cl_total:>5}")

        # Attempt distribution
        _sub("Attempt distribution")
        attempt_rows = (await db.execute(
            select(CampaignLead.attempt_count, func.count())
            .where(CampaignLead.campaign_id == campaign.id)
            .group_by(CampaignLead.attempt_count)
            .order_by(CampaignLead.attempt_count)
        )).all()
        for n, c in attempt_rows:
            label = "no attempts" if n == 0 else f"{n} attempt(s)"
            print(f"  {label:<14} {c:>5}  {_bar(c, cl_total)}")

        # ── Section 3: Pipeline stages of the leads in this campaign ──
        _hr("Pipeline stages of THIS campaign's leads")
        stage_rows = (await db.execute(
            select(Lead.current_stage, func.count())
            .join(CampaignLead, CampaignLead.lead_id == Lead.id)
            .where(CampaignLead.campaign_id == campaign.id)
            .group_by(Lead.current_stage)
        )).all()
        stages = dict(stage_rows)
        stage_order = ["lead", "called", "connected", "qualified_lead", "won", "lost"]
        for s in stage_order:
            c = stages.get(s, 0)
            print(f"  {s:<16} {c:>5}  {_pct(c, cl_total):>6}  {_bar(c, cl_total)}")

        # Auto vs manual stage transitions WITHIN the campaign window
        _sub("Stage transitions written during this campaign")
        if campaign.started_at:
            transition_rows = (await db.execute(
                select(LeadStageLog.from_stage, LeadStageLog.to_stage, func.count())
                .join(CampaignLead, CampaignLead.lead_id == LeadStageLog.lead_id)
                .where(
                    CampaignLead.campaign_id == campaign.id,
                    LeadStageLog.created_at >= campaign.started_at,
                )
                .group_by(LeadStageLog.from_stage, LeadStageLog.to_stage)
                .order_by(func.count().desc())
            )).all()
            if transition_rows:
                for f, t, c in transition_rows:
                    print(f"  {f or '(none)':>14} → {t:<16} {c}")
            else:
                print("  (no transitions logged)")

        # ── Section 4: Calls — dispatch and connection quality ────────
        _hr("Call attempts & connection quality")
        # Note: link via CampaignLead.last_call_id captures only the LATEST
        # call per lead (retries lose history). To get every call ever
        # attempted under this campaign, we need a different join.
        # Use call_type='ai_campaign' + company_id + time window as proxy.
        time_filter = []
        if campaign.started_at:
            time_filter.append(CallAttempt.created_at >= campaign.started_at)
        if campaign.completed_at:
            time_filter.append(CallAttempt.created_at <= campaign.completed_at)

        # Better: explicit join via CampaignLead.last_call_id (latest only)
        # AND compute totals from CampaignLead.attempt_count (true total)
        true_attempts = sum((cl_a or 0) * cl_c for cl_a, cl_c in attempt_rows)
        latest_calls = (await db.execute(
            select(
                func.count().label("rows"),
                func.count().filter(CallAttempt.started_at.isnot(None)).label("started"),
                func.count().filter(CallAttempt.call_duration_seconds > 10).label("real_conv"),
                func.count().filter(CallAttempt.call_duration_seconds > 30).label("substantial"),
                func.count().filter(CallAttempt.transcript.isnot(None)).filter(CallAttempt.transcript != "").label("with_transcript"),
                func.count().filter(CallAttempt.summary.isnot(None)).filter(CallAttempt.summary != "").label("with_summary"),
                func.avg(CallAttempt.call_duration_seconds).label("avg_dur"),
                func.sum(CallAttempt.call_duration_seconds).label("total_dur"),
                func.sum(CallAttempt.cost).label("total_cost"),
            )
            .join(CampaignLead, CampaignLead.last_call_id == CallAttempt.id)
            .where(CampaignLead.campaign_id == campaign.id)
        )).one()

        print(f"  Total attempts (sum of attempt_count):  {true_attempts}")
        print(f"  Campaign counter (calls_made):          {campaign.calls_made}")
        if true_attempts != campaign.calls_made:
            print(f"  ⚠️  Mismatch — denormalised counter drifted from real attempt count")
        print()
        print(f"  Latest call per lead (CampaignLead.last_call_id):")
        print(f"    Rows:                  {latest_calls.rows}")
        print(f"    started_at set:        {latest_calls.started} ({_pct(latest_calls.started or 0, latest_calls.rows or 0)})")
        print(f"    duration > 10s:        {latest_calls.real_conv} ({_pct(latest_calls.real_conv or 0, latest_calls.rows or 0)})  ← real conversation")
        print(f"    duration > 30s:        {latest_calls.substantial} ({_pct(latest_calls.substantial or 0, latest_calls.rows or 0)})  ← substantial")
        print(f"    With transcript:       {latest_calls.with_transcript} ({_pct(latest_calls.with_transcript or 0, latest_calls.rows or 0)})")
        print(f"    With AI summary:       {latest_calls.with_summary} ({_pct(latest_calls.with_summary or 0, latest_calls.rows or 0)})")
        if latest_calls.with_transcript and latest_calls.with_summary is not None:
            ai_loss = latest_calls.with_transcript - latest_calls.with_summary
            print(f"    Transcripts WITHOUT summary: {ai_loss} ← AI summary failures")
        print(f"    Avg duration:          {round(float(latest_calls.avg_dur or 0), 1)}s")
        print(f"    Total talk time:       {round(float(latest_calls.total_dur or 0)/60, 1)} min")
        print(f"    Total cost:            ${round(float(latest_calls.total_cost or 0), 4)}")

        # ── Section 5: Transcript size distribution ──────────────────
        _hr("Transcript size distribution")
        # bucket all transcripts (latest call per lead)
        ts_rows = (await db.execute(
            select(func.length(CallAttempt.transcript))
            .join(CampaignLead, CampaignLead.last_call_id == CallAttempt.id)
            .where(
                CampaignLead.campaign_id == campaign.id,
                CallAttempt.transcript.isnot(None),
                CallAttempt.transcript != "",
            )
        )).all()
        lens = [r[0] for r in ts_rows if r[0] is not None]
        if lens:
            buckets = {
                "0-50":      sum(1 for l in lens if l <= 50),
                "51-150":    sum(1 for l in lens if 50 < l <= 150),
                "151-300":   sum(1 for l in lens if 150 < l <= 300),
                "301-500":   sum(1 for l in lens if 300 < l <= 500),
                "501-1000":  sum(1 for l in lens if 500 < l <= 1000),
                "1000+":     sum(1 for l in lens if l > 1000),
            }
            for label, c in buckets.items():
                print(f"  {label:<10} chars  {c:>4}  {_bar(c, len(lens))}")
            print(f"  {'TOTAL':<10}        {len(lens):>4}")
            print(f"\n  Median: {sorted(lens)[len(lens)//2]} chars")
            print(f"  Max:    {max(lens)} chars")

        # ── Section 6: Sentiment + interest distribution ──────────────
        _hr("AI sentiment / interest distribution")
        sentiment_rows = (await db.execute(
            select(CallAttempt.sentiment, func.count())
            .join(CampaignLead, CampaignLead.last_call_id == CallAttempt.id)
            .where(
                CampaignLead.campaign_id == campaign.id,
                CallAttempt.sentiment.isnot(None),
            )
            .group_by(CallAttempt.sentiment)
        )).all()
        if sentiment_rows:
            total_s = sum(c for _, c in sentiment_rows)
            for s, c in sorted(sentiment_rows, key=lambda x: -x[1]):
                print(f"  {s:<10} {c:>4}  {_pct(c, total_s):>6}  {_bar(c, total_s)}")
        else:
            print("  (no calls had sentiment populated)")

        # ── Section 7: Time-of-day distribution ──────────────────────
        _hr("Calls by hour of day (UTC)")
        hour_rows = (await db.execute(
            select(
                func.extract('hour', CallAttempt.created_at).label("h"),
                func.count(),
            )
            .join(CampaignLead, CampaignLead.last_call_id == CallAttempt.id)
            .where(CampaignLead.campaign_id == campaign.id)
            .group_by("h")
            .order_by("h")
        )).all()
        if hour_rows:
            mx = max(c for _, c in hour_rows)
            for h, c in hour_rows:
                ist = (int(h) + 5) % 24  # +5h30m, drop the 30 for display
                print(f"  {int(h):02d}:00 UTC ({ist:02d}h IST)  {c:>3}  {_bar(c, mx)}")

        # ── Section 8: Data-quality flags ────────────────────────────
        _hr("Data quality flags")
        # Leads with placeholder names
        placeholder_names = (await db.execute(
            select(func.count())
            .select_from(Lead)
            .join(CampaignLead, CampaignLead.lead_id == Lead.id)
            .where(
                CampaignLead.campaign_id == campaign.id,
                or_(
                    Lead.full_name.in_(["Lead", "lead", "Unknown", ""]),
                    Lead.full_name.is_(None),
                ),
            )
        )).scalar() or 0
        print(f"  Leads with placeholder name:        {placeholder_names}  / {cl_total}  ← will trigger 'May I know your name?'")

        # Leads with no phone (shouldn't exist in this campaign)
        no_phone = (await db.execute(
            select(func.count())
            .select_from(Lead)
            .join(CampaignLead, CampaignLead.lead_id == Lead.id)
            .where(
                CampaignLead.campaign_id == campaign.id,
                or_(Lead.phone.is_(None), Lead.phone == ""),
            )
        )).scalar() or 0
        print(f"  Leads with NO phone (bad import):   {no_phone}")

        # Soft-deleted leads still in campaign (worker should skip)
        soft_deleted = (await db.execute(
            select(func.count())
            .select_from(Lead)
            .join(CampaignLead, CampaignLead.lead_id == Lead.id)
            .where(
                CampaignLead.campaign_id == campaign.id,
                Lead.is_deleted == True,  # noqa: E712
            )
        )).scalar() or 0
        print(f"  Soft-deleted leads in campaign:     {soft_deleted}  ← worker now skips these")

        # Calls flagged 'connected' but no real conversation
        ghost_connect = (await db.execute(
            select(func.count())
            .select_from(CallAttempt)
            .join(CampaignLead, CampaignLead.last_call_id == CallAttempt.id)
            .where(
                CampaignLead.campaign_id == campaign.id,
                CallAttempt.started_at.isnot(None),
                or_(
                    CallAttempt.call_duration_seconds < 10,
                    CallAttempt.call_duration_seconds.is_(None),
                ),
            )
        )).scalar() or 0
        print(f"  Connected but conversation < 10s:   {ghost_connect}  ← inflating Connected count")

        # ── Section 9: Cost economics ────────────────────────────────
        _hr("Cost economics")
        total_cost = float(latest_calls.total_cost or 0)
        connected = latest_calls.real_conv or 0
        qualified = stages.get("qualified_lead", 0)
        print(f"  Total spend:                  ${total_cost:.4f}")
        if connected > 0:
            print(f"  Cost per real conversation:   ${total_cost/connected:.4f}")
        if qualified > 0:
            print(f"  Cost per qualified lead:      ${total_cost/qualified:.4f}")
        else:
            print(f"  Cost per qualified lead:      n/a (no qualified leads yet)")
        # Average cost per minute
        total_min = (latest_calls.total_dur or 0) / 60
        if total_min > 0:
            print(f"  Average $/minute:             ${total_cost/total_min:.4f}")

        # ── Section 10: Worth a human follow-up ──────────────────────
        _hr("Top 10 candidates worth a human callback")
        # Heuristic: substantial transcript + (positive sentiment OR
        # qualified stage). Surface real conversations that didn't get
        # auto-promoted but might warrant manual review.
        candidates = (await db.execute(
            select(CallAttempt, Lead)
            .join(Lead, Lead.id == CallAttempt.lead_id)
            .join(CampaignLead, CampaignLead.last_call_id == CallAttempt.id)
            .where(
                CampaignLead.campaign_id == campaign.id,
                CallAttempt.call_duration_seconds > 30,
                or_(
                    CallAttempt.sentiment == "positive",
                    Lead.current_stage.in_(["connected", "qualified_lead"]),
                ),
            )
            .order_by(CallAttempt.call_duration_seconds.desc())
            .limit(10)
        )).all()
        if not candidates:
            print("  (none — no substantial calls with positive signal)")
        for call, lead in candidates:
            ai = (lead.custom_fields or {}).get("ai_last_call", {}) or {}
            facts = []
            for k in ("loan_amount", "college", "study_location", "course", "intake"):
                if ai.get(k):
                    facts.append(f"{k}={ai[k]}")
            facts_str = " | ".join(facts) if facts else "(no structured extraction)"
            print(f"  {lead.full_name:<22} {lead.phone:<16} "
                  f"{lead.current_stage:<14} dur={call.call_duration_seconds}s  "
                  f"sentiment={call.sentiment}")
            print(f"    {facts_str}")

        print()


if __name__ == "__main__":
    asyncio.run(main())
