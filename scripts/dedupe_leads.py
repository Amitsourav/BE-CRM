"""Dedupe leads — merge duplicate (company_id, phone) rows into one.

Background: the WhatsApp CSV upload was run twice → 441 phones now have
2 lead rows each. Pipeline view shows duplicate cards; reports
double-count; campaign worker dialled both copies.

Algorithm
---------
For every (company_id, phone) group with len > 1:

  1. Pick the WINNER:
       a. most-advanced stage (won > qualified_lead > connected > called > lead)
          (lost is treated as below 'lead' so won-ish records always win)
       b. higher call_attempt_count
       c. most recent updated_at
  2. MERGE FIELDS into winner where winner is missing them:
       - full_name (only if winner is a placeholder, e.g. "Lead")
       - email, alternate_phone, city, state, country, pincode, dob, gender
       - course / college / passing_year / percentage / preferred_*
       - assigned_agent_id, lead_source_id, created_by
       - notes (concat — "(merged from <id>): <loser notes>")
       - custom_fields (winner wins on conflict; missing keys taken from loser)
       - timestamps connected_time / won_time / lost_time / lost_reason —
         keep the earliest set value (so history is preserved)
  3. MIGRATE child rows from each loser → winner:
       - call_attempts.lead_id        FK CASCADE
       - lead_stage_logs.lead_id      FK CASCADE
       - tasks.lead_id                FK CASCADE (nullable)
       - notifications.lead_id        FK SET NULL
       - campaign_leads.lead_id       FK CASCADE — but skip if winner is
         already in that campaign (keeps winner's row, drops loser's)
  4. HARD-DELETE the loser rows.

Each group is one transaction. A failure on one group does not unwind
the whole job.

Usage
-----
    # Dry-run preview (default):
    python -m scripts.dedupe_leads

    # Show details for first N groups:
    python -m scripts.dedupe_leads --preview 10

    # Actually apply:
    python -m scripts.dedupe_leads --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, update, delete, and_

from app.db.session import AsyncSessionLocal
from app.models.lead import Lead
from app.models.call_attempt import CallAttempt
from app.models.lead_stage_log import LeadStageLog
from app.models.task import Task
from app.models.notification import Notification
from app.models.campaign_lead import CampaignLead


# Stage rank — higher = more advanced; lost ranks below lead so a lost-rest-vs-
# active-rest tie is won by the active one. Won ranks highest of all.
STAGE_RANK = {
    "lost": -1,
    "lead": 0,
    "called": 1,
    "connected": 2,
    "qualified_lead": 3,
    "won": 4,
}

NAME_PLACEHOLDERS = {
    "", "lead", "user", "unknown", "no name", "n/a",
    "there", "you", "sir", "ma'am", "madam",
}


def _is_placeholder_name(name) -> bool:
    if not name:
        return True
    return str(name).strip().lower() in NAME_PLACEHOLDERS


def _pick_winner(rows: list[Lead]) -> Lead:
    """Highest stage rank → highest call_attempt_count → most recent updated_at."""
    return max(
        rows,
        key=lambda l: (
            STAGE_RANK.get(l.current_stage, -2),
            l.call_attempt_count or 0,
            l.updated_at or l.created_at,
        ),
    )


def _earliest_set(*values):
    """Return the earliest non-None datetime, or None."""
    vals = [v for v in values if v is not None]
    return min(vals) if vals else None


async def _merge_into(db, winner: Lead, loser: Lead, *, apply: bool) -> dict:
    """Move loser's data into winner. Returns a stats dict for the report."""
    stats = {
        "calls_moved": 0,
        "stage_logs_moved": 0,
        "tasks_moved": 0,
        "notifications_moved": 0,
        "campaign_leads_moved": 0,
        "campaign_leads_dropped": 0,  # winner already in that campaign
        "fields_filled": [],
    }

    # ── Field merge — only fill where winner is empty / placeholder ──
    if _is_placeholder_name(winner.full_name) and not _is_placeholder_name(loser.full_name):
        if apply:
            winner.full_name = loser.full_name
        stats["fields_filled"].append("full_name")

    for field in (
        "email", "alternate_phone", "city", "state", "country", "pincode",
        "date_of_birth", "gender", "highest_qualification", "stream",
        "passing_year", "college_name", "university", "percentage",
        "target_degree", "target_intake", "preferred_countries",
        "preferred_universities", "lost_reason",
        "assigned_agent_id", "lead_source_id", "created_by",
    ):
        if getattr(winner, field, None) in (None, "", []) and getattr(loser, field, None) not in (None, "", []):
            if apply:
                setattr(winner, field, getattr(loser, field))
            stats["fields_filled"].append(field)

    # Notes — concat loser's into winner if non-empty and different
    if loser.notes and (loser.notes != winner.notes):
        merged_notes = (winner.notes or "")
        merged_notes += f"\n\n--- Merged from duplicate lead {str(loser.id)[:8]} ---\n"
        merged_notes += loser.notes
        if apply:
            winner.notes = merged_notes
        stats["fields_filled"].append("notes")

    # custom_fields — merge loser keys winner doesn't have
    if loser.custom_fields:
        merged_cf = dict(winner.custom_fields or {})
        for k, v in (loser.custom_fields or {}).items():
            merged_cf.setdefault(k, v)
        if apply:
            winner.custom_fields = merged_cf
        stats["fields_filled"].append("custom_fields")

    # tags — union
    if loser.tags:
        merged_tags = list(set((winner.tags or []) + list(loser.tags)))
        if apply:
            winner.tags = merged_tags
        stats["fields_filled"].append("tags")

    # Earliest-set timestamps — preserve historical ordering
    earliest_connected = _earliest_set(winner.connected_time, loser.connected_time)
    earliest_won = _earliest_set(winner.won_time, loser.won_time)
    earliest_lost = _earliest_set(winner.lost_time, loser.lost_time)
    if earliest_connected and earliest_connected != winner.connected_time:
        if apply:
            winner.connected_time = earliest_connected
        stats["fields_filled"].append("connected_time")
    if earliest_won and earliest_won != winner.won_time:
        if apply:
            winner.won_time = earliest_won
        stats["fields_filled"].append("won_time")
    if earliest_lost and earliest_lost != winner.lost_time:
        if apply:
            winner.lost_time = earliest_lost
        stats["fields_filled"].append("lost_time")

    # call_attempt_count — sum (we're combining histories)
    combined_calls = (winner.call_attempt_count or 0) + (loser.call_attempt_count or 0)
    if combined_calls != winner.call_attempt_count:
        if apply:
            winner.call_attempt_count = combined_calls
        # Note: this can over-count if there are double-dial duplicates;
        # unavoidable without per-call dedup which is out of scope here.

    if not apply:
        # Skip the per-loser child-row counts — they hammer Supabase
        # (5 queries × 441 losers = >2000 round trips) and the connection
        # pool to Korea drops mid-run. The preview lines below give
        # enough info to validate the plan; actual move counts come
        # from the --apply run.
        return stats

    # ── APPLY: migrate child rows ──
    # call_attempts
    r = await db.execute(
        update(CallAttempt)
        .where(CallAttempt.lead_id == loser.id)
        .values(lead_id=winner.id)
    )
    stats["calls_moved"] = r.rowcount or 0

    # lead_stage_logs
    r = await db.execute(
        update(LeadStageLog)
        .where(LeadStageLog.lead_id == loser.id)
        .values(lead_id=winner.id)
    )
    stats["stage_logs_moved"] = r.rowcount or 0

    # tasks
    r = await db.execute(
        update(Task)
        .where(Task.lead_id == loser.id)
        .values(lead_id=winner.id)
    )
    stats["tasks_moved"] = r.rowcount or 0

    # notifications
    r = await db.execute(
        update(Notification)
        .where(Notification.lead_id == loser.id)
        .values(lead_id=winner.id)
    )
    stats["notifications_moved"] = r.rowcount or 0

    # campaign_leads — careful: drop the loser's row if winner is already
    # in that campaign, otherwise migrate.
    winner_cls = (await db.execute(
        select(CampaignLead.campaign_id).where(CampaignLead.lead_id == winner.id)
    )).all()
    winner_campaign_ids = {row[0] for row in winner_cls}

    loser_cls = (await db.execute(
        select(CampaignLead).where(CampaignLead.lead_id == loser.id)
    )).scalars().all()
    for cl in loser_cls:
        if cl.campaign_id in winner_campaign_ids:
            # Winner is already in this campaign — drop loser's row
            await db.execute(
                delete(CampaignLead).where(CampaignLead.id == cl.id)
            )
            stats["campaign_leads_dropped"] += 1
        else:
            cl.lead_id = winner.id
            stats["campaign_leads_moved"] += 1

    # ── Hard-delete the loser ──
    await db.execute(delete(Lead).where(Lead.id == loser.id))

    return stats


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Actually merge + delete. Default = dry run.")
    parser.add_argument("--preview", type=int, default=5,
                        help="Show details for first N groups in dry run "
                             "(default: 5)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process only first N groups (debug / batch)")
    args = parser.parse_args()

    print(f"\n{'='*72}")
    print(f"  Mode: {'APPLY' if args.apply else 'DRY RUN'}")
    print(f"{'='*72}\n")

    # Load all active duplicates
    async with AsyncSessionLocal() as db:
        all_leads = (await db.execute(
            select(Lead).where(Lead.is_deleted == False)  # noqa: E712
        )).scalars().all()

    by_phone: dict[tuple, list[Lead]] = defaultdict(list)
    for l in all_leads:
        if l.phone:
            by_phone[(l.company_id, l.phone)].append(l)

    dups = {k: v for k, v in by_phone.items() if len(v) > 1}
    if not dups:
        print("  No duplicates found.")
        return

    print(f"  Total active leads:    {len(all_leads)}")
    print(f"  Duplicate groups:      {len(dups)}")
    print(f"  Extra rows to remove:  {sum(len(v)-1 for v in dups.values())}")

    groups = list(dups.items())
    if args.limit:
        groups = groups[:args.limit]
        print(f"  Limited to:            {len(groups)} groups")

    # ── Process each group ──
    totals = {
        "groups_processed": 0,
        "groups_failed": 0,
        "leads_deleted": 0,
        "calls_moved": 0,
        "stage_logs_moved": 0,
        "tasks_moved": 0,
        "notifications_moved": 0,
        "campaign_leads_moved": 0,
        "campaign_leads_dropped": 0,
    }

    shown = 0
    for (cid, phone), rows in groups:
        winner = _pick_winner(rows)
        losers = [r for r in rows if r.id != winner.id]

        if not args.apply and shown < args.preview:
            shown += 1
            print(f"\n  ─ {phone}  ({len(rows)} rows)")
            print(f"     winner: {str(winner.id)[:8]}  stage={winner.current_stage} "
                  f"calls={winner.call_attempt_count or 0}  "
                  f"name={winner.full_name or '(none)'}")
            for loser in losers:
                print(f"     loser:  {str(loser.id)[:8]}  stage={loser.current_stage} "
                      f"calls={loser.call_attempt_count or 0}  "
                      f"name={loser.full_name or '(none)'}")

        if not args.apply:
            # Count what we know without touching the DB
            totals["leads_deleted"] += len(losers)
            totals["groups_processed"] += 1
            continue

        # APPLY — one transaction per group
        async with AsyncSessionLocal() as db:
            try:
                # Re-fetch winner & losers in this session
                w = (await db.execute(
                    select(Lead).where(Lead.id == winner.id)
                )).scalar_one_or_none()
                if not w:
                    raise RuntimeError("winner disappeared")
                for loser_orig in losers:
                    l = (await db.execute(
                        select(Lead).where(Lead.id == loser_orig.id)
                    )).scalar_one_or_none()
                    if not l:
                        continue
                    s = await _merge_into(db, w, l, apply=True)
                    for k in ("calls_moved", "stage_logs_moved", "tasks_moved",
                              "notifications_moved", "campaign_leads_moved",
                              "campaign_leads_dropped"):
                        totals[k] += s[k]
                    totals["leads_deleted"] += 1
                await db.commit()
                totals["groups_processed"] += 1
            except Exception as e:
                await db.rollback()
                totals["groups_failed"] += 1
                print(f"  ❌ {phone}: {str(e)[:120]}")

        if totals["groups_processed"] % 50 == 0:
            print(f"  ...processed {totals['groups_processed']}/{len(groups)} groups")

    # ── Final report ──
    print(f"\n{'='*72}")
    print(f"  Groups processed:         {totals['groups_processed']}")
    if args.apply:
        print(f"  Groups failed:            {totals['groups_failed']}")
    print(f"  Lead rows deleted:        {totals['leads_deleted']}")
    print(f"  Call attempts moved:      {totals['calls_moved']}")
    print(f"  Stage logs moved:         {totals['stage_logs_moved']}")
    print(f"  Tasks moved:              {totals['tasks_moved']}")
    print(f"  Notifications moved:      {totals['notifications_moved']}")
    print(f"  Campaign-lead links moved:{totals['campaign_leads_moved']}")
    print(f"  Campaign-lead links dropped (winner already there): "
          f"{totals['campaign_leads_dropped']}")
    print(f"{'='*72}\n")
    if not args.apply:
        print("  (dry run — re-run with --apply to actually do this)")


if __name__ == "__main__":
    asyncio.run(main())
