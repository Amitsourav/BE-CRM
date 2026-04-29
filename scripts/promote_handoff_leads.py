"""Promote the 13 hand-pick leads to qualified_lead.

These were identified by reading every transcript from the
'whatsapp campigen' campaign and judging interest manually:
4 truly qualified (concrete details given) + 9 callback-worthy
(gave name, agent didn't get to extract details).

For each lead:
  - update full_name if it's the placeholder "Lead" and we know
    the real name from the transcript
  - if currently 'connected', transition to 'qualified_lead'
  - write a LeadStageLog row attributing the move to 'manual
    review by deep-transcript audit'

Skips leads that are already 'qualified_lead'. Only advances —
never moves backward.

Usage:
    # Preview:
    python -m scripts.promote_handoff_leads
    # Apply:
    python -m scripts.promote_handoff_leads --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.lead import Lead
from app.models.lead_stage_log import LeadStageLog
from app.models.profile import Profile
from app.core.constants import UserRole
from app.utils.date_helpers import now_utc, add_business_days


async def _fallback_actor(db, company_id):
    """Find an admin / manager in the company to attribute manual-audit moves
    to when the lead has no assigned_agent_id and no created_by.
    """
    result = await db.execute(
        select(Profile)
        .where(
            Profile.company_id == company_id,
            Profile.role.in_([UserRole.ADMIN, UserRole.MANAGER]),
            Profile.is_active == True,  # noqa: E712
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


# (phone_e164, name_from_transcript, my_analysis_note)
HANDOFF = [
    # Top 4 — strong concrete signals
    ("+917075531238", "Diraj",
     "Wants 20 lakhs for Welingkar Bangalore (India). Asked about "
     "collateral. 4.5-min call, fully engaged."),
    ("+919131632300", None,  # already qualified_lead — no rename
     "Wants 9 lakhs for MBA General Management at 'Bamni Camp Pune' "
     "(likely ICFAI Pune). Father is farmer with 10 acres land as "
     "potential collateral."),
    ("+918090438602", None,
     "Already applied with Central Bank. Comparing NIA Pune vs IMT "
     "Nagpur. Asked about SBI rates. Buyer-stage research."),
    ("+917692869542", "Nikhilesh Shah",
     "Wants IIFT (Tier 1). Open to India OR Ireland. High-ticket abroad "
     "potential."),
    # Medium 9 — gave name, conversation cut short
    ("+919952496706", "Navaneethan",
     "Said not yet applied, not yet decided on college. Open to "
     "discussion. Worth a human callback."),
    ("+919503234085", "Arpita Giri",
     "Gave name, call dropped before extracting details. Callback."),
    ("+918668265812", "Kabaleen Kaur",
     "Gave name, call ended early. Callback."),
    ("+919569342560", "Yuvraj Vishwakarma",
     "Gave name, call ended early. Callback."),
    ("+916379130155", "Akaliya",
     "Gave name, call ended early. Callback."),
    ("+918830149739", "Darshan",
     "Gave name, call ended early. Callback."),
    # Note: Kapil shares phone with Kabaleen Kaur — different lead row
    # but same number. The dump showed two distinct lead_ids.
    ("+916303949547", "Keat",
     "Gave name, call ended early. Callback."),
    ("+917980790077", "Siddharthi Lahuri",
     "Gave name, call ended early. Callback."),
]


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Actually write changes. Default = dry run.")
    args = parser.parse_args()

    print(f"\n{'='*72}")
    print(f"  Mode: {'APPLY' if args.apply else 'DRY RUN'}")
    print(f"  Targets: {len(HANDOFF)} leads")
    print(f"{'='*72}\n")

    promoted = 0
    skipped_already = 0
    not_found = 0
    renamed = 0

    async with AsyncSessionLocal() as db:
        for phone, transcript_name, note in HANDOFF:
            # Find by phone (E.164 stored)
            result = await db.execute(
                select(Lead).where(
                    Lead.phone == phone,
                    Lead.is_deleted == False,  # noqa: E712
                )
            )
            leads = result.scalars().all()
            if not leads:
                print(f"  ❌ {phone}  no lead found")
                not_found += 1
                continue

            for lead in leads:
                old_stage = lead.current_stage
                old_name = lead.full_name

                # Decide name update
                will_rename = False
                new_name = old_name
                if transcript_name and (
                    not old_name or old_name.strip().lower() in (
                        "", "lead", "user", "unknown", "no name", "n/a",
                        "there", "you"
                    )
                ):
                    new_name = transcript_name
                    will_rename = True

                # Decide stage move (only forward)
                will_move = old_stage == "connected"
                target_stage = "qualified_lead" if will_move else old_stage

                tag = "→" if (will_move or will_rename) else "  "
                action = []
                if will_rename:
                    action.append(f"rename '{old_name}' → '{new_name}'")
                if will_move:
                    action.append(f"stage {old_stage} → qualified_lead")
                else:
                    action.append(f"stage stays at {old_stage}")
                print(f"  {tag} {phone}  ({lead.id})")
                print(f"     {' | '.join(action)}")

                if not args.apply:
                    if will_move:
                        promoted += 1
                    if will_rename:
                        renamed += 1
                    if old_stage == "qualified_lead":
                        skipped_already += 1
                    continue

                # APPLY
                if will_rename:
                    lead.full_name = new_name
                    renamed += 1
                if will_move:
                    actor = lead.assigned_agent_id or lead.created_by
                    if not actor:
                        # Lead has no owner on file — fall back to any admin
                        # / manager in the same company so the LeadStageLog
                        # NOT-NULL constraint on changed_by is satisfied.
                        fb = await _fallback_actor(db, lead.company_id)
                        if fb:
                            actor = fb.id
                    if not actor:
                        print(f"     ⚠️  no actor available; skipping stage move")
                        await db.rollback()
                        continue

                    lead.current_stage = "qualified_lead"
                    lead.due_date = add_business_days(now_utc(), 1)
                    db.add(LeadStageLog(
                        company_id=lead.company_id,
                        lead_id=lead.id,
                        from_stage=old_stage,
                        to_stage="qualified_lead",
                        changed_by=actor,
                        conversation_notes=(
                            "Manual review (deep transcript audit): " + note
                        ),
                        agent_agenda=(
                            "Human callback — pass to closer. See lead notes "
                            "for transcript and AI extraction."
                        ),
                    ))
                    promoted += 1
                if old_stage == "qualified_lead":
                    skipped_already += 1

                try:
                    await db.commit()
                    await db.refresh(lead)
                except Exception as e:
                    print(f"     ❌ commit failed: {str(e)[:120]}")
                    await db.rollback()

    print(f"\n{'='*72}")
    print(f"  Promoted to qualified_lead:  {promoted}")
    print(f"  Renamed (placeholder→real):  {renamed}")
    print(f"  Already qualified, kept:     {skipped_already}")
    print(f"  Not found:                   {not_found}")
    print(f"{'='*72}\n")
    if not args.apply:
        print("  (dry run — re-run with --apply to actually move them)")


if __name__ == "__main__":
    asyncio.run(main())
