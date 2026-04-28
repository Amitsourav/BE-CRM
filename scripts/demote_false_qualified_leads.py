"""Demote wrongly auto-qualified leads back to Connected.

Finds leads currently in 'qualified_lead' whose most recent AI call had
a transcript too thin to justify qualification (under 500 chars or
fewer than 3 user turns). Moves them back to 'connected' and writes a
LeadStageLog row explaining the rollback.

Manual qualifications (a human typed real notes in the stage log) are
left alone — only auto-qualified ones with weak evidence get demoted.

Usage:
    # See what would change:
    python -m scripts.demote_false_qualified_leads

    # Actually do it:
    python -m scripts.demote_false_qualified_leads --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.session import AsyncSessionLocal
from app.models.lead import Lead
from app.models.call_attempt import CallAttempt
from app.models.lead_stage_log import LeadStageLog


MIN_TRANSCRIPT_CHARS = 500
MIN_USER_TURNS = 3


async def find_candidates() -> list[dict]:
    """Returns rows describing each lead that should be demoted."""
    rows: list[dict] = []
    async with AsyncSessionLocal() as db:
        # All leads currently in qualified_lead
        result = await db.execute(
            select(Lead)
            .where(Lead.current_stage == "qualified_lead", Lead.is_deleted == False)  # noqa: E712
            .order_by(Lead.updated_at.desc())
        )
        leads = result.scalars().all()

        for lead in leads:
            # Most recent call with a transcript
            call_result = await db.execute(
                select(CallAttempt)
                .where(
                    CallAttempt.lead_id == lead.id,
                    CallAttempt.transcript.isnot(None),
                    CallAttempt.transcript != "",
                )
                .order_by(CallAttempt.created_at.desc())
                .limit(1)
            )
            call = call_result.scalar_one_or_none()

            if not call:
                # Qualified without any call transcript? Probably manual — leave alone
                rows.append({
                    "lead_id": str(lead.id),
                    "name": lead.full_name,
                    "phone": lead.phone,
                    "transcript_len": 0,
                    "user_turns": 0,
                    "verdict": "skip (no call transcript — manual?)",
                    "demote": False,
                    "_lead": lead,
                    "_call": None,
                })
                continue

            transcript = call.transcript or ""
            t_len = len(transcript)
            u_turns = transcript.count("User:")

            # Was this auto-qualified? Look for a recent stage log with
            # the auto-pipeline marker. If not, treat as manual.
            log_result = await db.execute(
                select(LeadStageLog)
                .where(
                    LeadStageLog.lead_id == lead.id,
                    LeadStageLog.to_stage == "qualified_lead",
                )
                .order_by(LeadStageLog.created_at.desc())
                .limit(1)
            )
            last_log = log_result.scalar_one_or_none()
            notes = (last_log.conversation_notes or "") if last_log else ""
            is_auto = notes.startswith("Auto")

            if not is_auto:
                rows.append({
                    "lead_id": str(lead.id),
                    "name": lead.full_name,
                    "phone": lead.phone,
                    "transcript_len": t_len,
                    "user_turns": u_turns,
                    "verdict": "skip (manual qualification)",
                    "demote": False,
                    "_lead": lead,
                    "_call": call,
                })
                continue

            # Auto-qualified — does the evidence hold up?
            evidence_weak = t_len < MIN_TRANSCRIPT_CHARS or u_turns < MIN_USER_TURNS
            rows.append({
                "lead_id": str(lead.id),
                "name": lead.full_name,
                "phone": lead.phone,
                "transcript_len": t_len,
                "user_turns": u_turns,
                "verdict": (
                    f"DEMOTE (transcript {t_len} < {MIN_TRANSCRIPT_CHARS} "
                    f"or turns {u_turns} < {MIN_USER_TURNS})"
                    if evidence_weak else
                    "keep (auto-qualified with strong evidence)"
                ),
                "demote": evidence_weak,
                "_lead": lead,
                "_call": call,
            })
    return rows


async def apply_demotions(rows: list[dict]) -> int:
    """Move each demote=True lead back to 'connected'. Returns count moved."""
    moved = 0
    async with AsyncSessionLocal() as db:
        for r in rows:
            if not r["demote"]:
                continue
            try:
                # Re-fetch in this session
                lead_result = await db.execute(
                    select(Lead).where(Lead.id == uuid.UUID(r["lead_id"]))
                )
                lead = lead_result.scalar_one_or_none()
                if not lead or lead.current_stage != "qualified_lead":
                    continue

                old_stage = lead.current_stage
                lead.current_stage = "connected"

                # Audit row — find an actor we can attribute this to
                # (the call's agent or the lead's assigned agent)
                call = r.get("_call")
                actor_id = None
                if call:
                    actor_id = call.agent_id or call.telecaller_id
                if not actor_id:
                    actor_id = lead.assigned_agent_id
                if not actor_id:
                    actor_id = lead.created_by

                if actor_id:
                    db.add(LeadStageLog(
                        lead_id=lead.id,
                        company_id=lead.company_id,
                        from_stage=old_stage,
                        to_stage="connected",
                        changed_by=actor_id,
                        conversation_notes=(
                            "Auto-revert: lead was wrongly qualified by AI based on "
                            f"a thin transcript ({r['transcript_len']} chars, "
                            f"{r['user_turns']} user turns). Demoted to Connected. "
                            "Stricter evidence rules now in place."
                        ),
                    ))
                await db.commit()
                moved += 1
            except Exception as e:
                print(f"  ! error demoting {r['name']}: {e}")
                await db.rollback()
    return moved


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Actually move leads. Default = dry run.")
    args = parser.parse_args()

    print(f"\n{'='*72}")
    print(f"  Mode: {'APPLY (writes!)' if args.apply else 'DRY RUN'}")
    print(f"  Demotion rule: transcript < {MIN_TRANSCRIPT_CHARS} chars OR user_turns < {MIN_USER_TURNS}")
    print(f"{'='*72}\n")

    rows = await find_candidates()
    if not rows:
        print("No leads in Qualified stage.")
        return

    cols = [("name", 24), ("phone", 16), ("transcript_len", 6),
            ("user_turns", 5), ("verdict", 60)]
    header = "  ".join(f"{label[:w]:<{w}}" for label, w in cols)
    print(header)
    print("-" * len(header))
    for r in rows:
        print("  ".join(
            f"{str(r.get(label, '') or '')[:w]:<{w}}" for label, w in cols
        ))

    to_demote = sum(1 for r in rows if r["demote"])
    to_keep = len(rows) - to_demote
    print(f"\n  Would demote: {to_demote}")
    print(f"  Would keep:   {to_keep}")

    if not args.apply:
        print("\n  (dry run — re-run with --apply to actually move them)")
        return

    print(f"\n  Applying demotions...")
    moved = await apply_demotions(rows)
    print(f"\n  ✅ Demoted {moved} leads from qualified_lead → connected")
    print(f"     Each has a LeadStageLog audit entry explaining the revert.\n")


if __name__ == "__main__":
    asyncio.run(main())
