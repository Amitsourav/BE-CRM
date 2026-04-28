"""Show how each Qualified lead got there.

Reads the lead_stage_logs audit trail to tell you whether each Qualified
lead was moved by AI auto-pipeline or by a human telecaller.

Usage:
    python -m scripts.inspect_qualified_leads
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.session import AsyncSessionLocal
from app.models.lead import Lead
from app.models.lead_stage_log import LeadStageLog
from app.models.profile import Profile


async def main():
    async with AsyncSessionLocal() as db:
        # All leads currently in qualified_lead
        result = await db.execute(
            select(Lead)
            .where(Lead.current_stage == "qualified_lead", Lead.is_deleted == False)
            .order_by(Lead.updated_at.desc())
        )
        leads = result.scalars().all()
        print(f"\nFound {len(leads)} leads in Qualified stage.\n")

        for lead in leads:
            print("=" * 78)
            print(f"  {lead.full_name}  ({lead.phone})")
            print(f"  Lead ID: {lead.id}")
            print(f"  Created: {lead.created_at}  |  Last updated: {lead.updated_at}")
            print()

            # Get every stage transition for this lead, oldest first
            logs_result = await db.execute(
                select(LeadStageLog)
                .where(LeadStageLog.lead_id == lead.id)
                .order_by(LeadStageLog.created_at.asc())
            )
            logs = logs_result.scalars().all()

            if not logs:
                print("  ⚠️  No stage logs found — historical migration?")
                print()
                continue

            print(f"  Stage history ({len(logs)} transitions):")
            for log in logs:
                # Look up who changed it
                actor_name = "(unknown)"
                if log.changed_by:
                    p_result = await db.execute(
                        select(Profile).where(Profile.id == log.changed_by)
                    )
                    p = p_result.scalar_one_or_none()
                    if p:
                        actor_name = f"{p.full_name or p.email}"

                from_s = log.from_stage or "(none)"
                arrow = f"{from_s:>14} → {log.to_stage:<14}"
                ts = log.created_at.strftime("%Y-%m-%d %H:%M")

                # Auto vs manual signal: notes starting with "Auto" mean
                # AI pipeline did it. Otherwise a telecaller typed notes.
                notes = log.conversation_notes or ""
                is_auto = notes.startswith("Auto")
                tag = "🤖 AUTO" if is_auto else "👤 HUMAN"

                print(f"    {ts}  {arrow}  {tag}  by {actor_name}")
                if notes:
                    notes_preview = notes[:200].replace("\n", " ")
                    print(f"      └─ \"{notes_preview}\"")
            print()


if __name__ == "__main__":
    asyncio.run(main())
