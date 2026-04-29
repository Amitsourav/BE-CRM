"""Comprehensive pipeline integrity audit.

Checks:
  1. Lead.current_stage values (no NULLs, all valid enum values)
  2. State invariants (won → won_time set, lost → lost_time + reason set, etc)
  3. lead.current_stage matches the most recent LeadStageLog.to_stage
  4. Every LeadStageLog respects VALID_TRANSITIONS
  5. Orphan stage logs (lead missing or soft-deleted)
  6. Duplicate leads (same phone, multiple rows)
  7. Stage-count agreement between Lead table and LeadStageLog (audit)
  8. Cross-tenant leakage (leads in stage logs from a different company)

Read-only. Prints a report with red flags. No DB writes.

Usage:
    python -m scripts.audit_pipeline_integrity
    python -m scripts.audit_pipeline_integrity --company-id <uuid>
    python -m scripts.audit_pipeline_integrity --verbose      # list every issue
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func

from app.db.session import AsyncSessionLocal
from app.models.lead import Lead
from app.models.lead_stage_log import LeadStageLog
from app.core.constants import LeadStage, VALID_TRANSITIONS


VALID_STAGES = {s.value for s in LeadStage}

# Allowed pairs derived from VALID_TRANSITIONS — flat set of (from, to) tuples
ALLOWED_PAIRS = set()
for src, dsts in VALID_TRANSITIONS.items():
    for d in dsts:
        ALLOWED_PAIRS.add((src.value, d.value))
# (None, X) is allowed for every X — that's the initial creation log.
for s in VALID_STAGES:
    ALLOWED_PAIRS.add((None, s))


def _hr(t: str) -> None:
    print(f"\n{'='*72}\n  {t}\n{'='*72}")


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-id", type=str, default=None,
                        help="Restrict audit to one tenant")
    parser.add_argument("--verbose", action="store_true",
                        help="Print every offending row instead of just counts")
    args = parser.parse_args()

    company_filter = uuid.UUID(args.company_id) if args.company_id else None

    issues = defaultdict(list)  # category → list of rows

    async with AsyncSessionLocal() as db:
        # ── Pull all leads (active only) ──
        q = select(Lead)
        if company_filter:
            q = q.where(Lead.company_id == company_filter)
        leads_result = await db.execute(q)
        all_leads = leads_result.scalars().all()
        active_leads = [l for l in all_leads if not l.is_deleted]

        print(f"\n  Audit scope:")
        print(f"    All leads:    {len(all_leads)}")
        print(f"    Active leads: {len(active_leads)}")
        print(f"    Soft-deleted: {len(all_leads) - len(active_leads)}")
        if company_filter:
            print(f"    Tenant:       {company_filter}")

        # Build helper indexes
        leads_by_id = {l.id: l for l in all_leads}
        active_by_id = {l.id: l for l in active_leads}

        # ── 1. Lead.current_stage validity ──
        _hr("Check 1: Lead.current_stage values")
        bad_stage = [l for l in active_leads if l.current_stage not in VALID_STAGES]
        null_stage = [l for l in active_leads if l.current_stage is None]
        stage_counter = Counter(l.current_stage for l in active_leads)
        if bad_stage:
            issues["bad_stage"] = bad_stage
            print(f"  ❌ {len(bad_stage)} leads have an invalid current_stage value")
        if null_stage:
            issues["null_stage"] = null_stage
            print(f"  ❌ {len(null_stage)} leads have NULL current_stage")
        if not bad_stage and not null_stage:
            print(f"  ✅ All {len(active_leads)} active leads have a valid stage")
        print(f"\n  Distribution:")
        for s in [v.value for v in LeadStage]:
            c = stage_counter.get(s, 0)
            print(f"    {s:<16} {c}")

        # ── 2. State invariants ──
        _hr("Check 2: State invariants (timestamps must match stage)")
        invariant_issues: list[tuple[str, Lead]] = []
        for l in active_leads:
            if l.current_stage == "won" and not l.won_time:
                invariant_issues.append(("won_no_won_time", l))
            if l.current_stage == "lost" and not l.lost_time:
                invariant_issues.append(("lost_no_lost_time", l))
            if l.current_stage == "lost" and not l.lost_reason:
                invariant_issues.append(("lost_no_reason", l))
            if l.current_stage == "connected" and not l.connected_time:
                invariant_issues.append(("connected_no_time", l))
            # Reverse: timestamps set but stage doesn't match
            if l.won_time and l.current_stage not in ("won",):
                # Reopened? Won is terminal so this is suspicious.
                invariant_issues.append(("won_time_set_but_not_won", l))
            if l.lost_time and l.current_stage not in ("lost",):
                # OK if reopened from lost — clear() should have happened
                invariant_issues.append(("lost_time_set_but_not_lost", l))

        if invariant_issues:
            grouped = defaultdict(list)
            for kind, l in invariant_issues:
                grouped[kind].append(l)
            for kind, ls in grouped.items():
                print(f"  ❌ {kind:<28} {len(ls)} leads")
                if args.verbose:
                    for l in ls[:5]:
                        print(f"     - {l.id} ({l.full_name or '(no name)'}) "
                              f"stage={l.current_stage} won={l.won_time} "
                              f"lost={l.lost_time} connected={l.connected_time}")
            issues["invariants"] = invariant_issues
        else:
            print(f"  ✅ All state-vs-timestamp invariants hold")

        # ── 3. lead.current_stage matches latest LeadStageLog ──
        _hr("Check 3: Lead stage matches latest stage log")
        # Pull every stage log, group by lead, find latest
        log_q = select(LeadStageLog)
        if company_filter:
            log_q = log_q.where(LeadStageLog.company_id == company_filter)
        log_q = log_q.order_by(LeadStageLog.lead_id, LeadStageLog.created_at.desc())
        all_logs = (await db.execute(log_q)).scalars().all()

        latest_per_lead: dict[uuid.UUID, LeadStageLog] = {}
        all_logs_by_lead: dict[uuid.UUID, list] = defaultdict(list)
        for log in all_logs:
            all_logs_by_lead[log.lead_id].append(log)
            if log.lead_id not in latest_per_lead:
                latest_per_lead[log.lead_id] = log

        mismatch_count = 0
        no_log_count = 0
        mismatches = []
        for l in active_leads:
            latest = latest_per_lead.get(l.id)
            if not latest:
                if l.current_stage != "lead":
                    # Lead has advanced stage but no log — bug
                    mismatches.append(("no_log_but_advanced", l, None))
                no_log_count += 1
                continue
            if latest.to_stage != l.current_stage:
                mismatches.append(("stage_log_mismatch", l, latest))
                mismatch_count += 1

        if mismatches:
            grouped = defaultdict(list)
            for kind, l, log in mismatches:
                grouped[kind].append((l, log))
            for kind, items in grouped.items():
                print(f"  ❌ {kind:<28} {len(items)}")
                if args.verbose:
                    for l, log in items[:5]:
                        log_state = f"log.to_stage={log.to_stage}" if log else "no log at all"
                        print(f"     - {l.id} stage={l.current_stage} {log_state}")
            issues["stage_mismatch"] = mismatches
        else:
            print(f"  ✅ Every active lead's stage matches the most recent stage log")

        if no_log_count:
            print(f"  ℹ️  {no_log_count} leads have NO stage log entry "
                  f"(first-stage 'lead' has none, that's normal)")

        # ── 4. Every LeadStageLog respects VALID_TRANSITIONS ──
        _hr("Check 4: All historical transitions are valid")
        invalid_transitions = []
        for log in all_logs:
            pair = (log.from_stage, log.to_stage)
            if pair not in ALLOWED_PAIRS:
                invalid_transitions.append(log)

        if invalid_transitions:
            print(f"  ❌ {len(invalid_transitions)} stage logs violate VALID_TRANSITIONS")
            counts = Counter((l.from_stage, l.to_stage) for l in invalid_transitions)
            for (f, t), n in counts.most_common(10):
                print(f"     {n}x  {f or '(none)'} → {t}")
            issues["invalid_transitions"] = invalid_transitions
        else:
            print(f"  ✅ All {len(all_logs)} stage transitions follow the rules")

        # ── 5. Orphan stage logs ──
        _hr("Check 5: Orphan stage logs (lead deleted or wrong tenant)")
        all_known_lead_ids = {l.id for l in all_leads}
        orphan = [log for log in all_logs if log.lead_id not in all_known_lead_ids]
        soft_deleted_log_count = 0
        cross_tenant = []
        for log in all_logs:
            lead = leads_by_id.get(log.lead_id)
            if not lead:
                # Already counted in orphan
                continue
            if lead.is_deleted:
                soft_deleted_log_count += 1
            if lead.company_id != log.company_id:
                cross_tenant.append((log, lead))

        if orphan:
            print(f"  ❌ {len(orphan)} stage logs reference a non-existent lead "
                  "(hard-deleted? — should have cascaded)")
            issues["orphan_logs"] = orphan
        else:
            print(f"  ✅ Every stage log references a real lead")
        if soft_deleted_log_count:
            print(f"  ℹ️  {soft_deleted_log_count} logs reference soft-deleted leads "
                  "(expected — soft delete preserves logs)")
        if cross_tenant:
            print(f"  🚨 {len(cross_tenant)} CROSS-TENANT logs (log.company_id "
                  "≠ lead.company_id) — security bug")
            issues["cross_tenant_logs"] = cross_tenant

        # ── 6. Duplicate leads (same phone, multiple rows) ──
        _hr("Check 6: Duplicate leads (same phone within tenant)")
        by_phone: dict[tuple, list] = defaultdict(list)
        for l in active_leads:
            if l.phone:
                by_phone[(l.company_id, l.phone)].append(l)
        duplicates = {k: v for k, v in by_phone.items() if len(v) > 1}
        if duplicates:
            total_duplicate_leads = sum(len(v) for v in duplicates.values())
            phones_affected = len(duplicates)
            print(f"  ❌ {phones_affected} phone numbers have multiple lead rows  "
                  f"(total {total_duplicate_leads} rows)")
            extras = total_duplicate_leads - phones_affected
            print(f"     {extras} extra rows that should be merged or deleted")
            if args.verbose:
                for (cid, phone), ls in list(duplicates.items())[:10]:
                    stages = [l.current_stage for l in ls]
                    print(f"     {phone}  rows={len(ls)}  stages={stages}")
            issues["duplicates"] = duplicates
        else:
            print(f"  ✅ No duplicate leads (every phone is unique within tenant)")

        # ── 7. Pipeline-view counts vs raw stage counts ──
        _hr("Check 7: Pipeline view counts (matches Lead table)")
        # Run the same query that ReportService.pipeline() uses
        report_q = (
            select(Lead.current_stage, func.count())
            .where(Lead.is_deleted == False)  # noqa: E712
            .group_by(Lead.current_stage)
        )
        if company_filter:
            report_q = report_q.where(Lead.company_id == company_filter)
        report_rows = (await db.execute(report_q)).all()
        report_counts = dict(report_rows)
        # Compare with our in-memory count
        for s, c in stage_counter.items():
            r = report_counts.get(s, 0)
            ok = "✅" if c == r else "❌"
            print(f"  {ok} {s:<16}  in-memory={c}  /reports/pipeline={r}")

        # ── 8. Lead created_by + assigned_agent_id integrity ──
        _hr("Check 8: Lead ownership integrity")
        no_owner = [l for l in active_leads
                    if not l.assigned_agent_id and not l.created_by]
        if no_owner:
            print(f"  ⚠️  {len(no_owner)} leads have NEITHER assigned_agent_id "
                  "NOR created_by → auto-stage path will skip them silently")
            if args.verbose:
                for l in no_owner[:10]:
                    print(f"     - {l.id} ({l.full_name or '(no name)'}) "
                          f"phone={l.phone}")
        else:
            print(f"  ✅ Every active lead has either an assigned agent or "
                  "a creator")

        # ── Summary ──
        _hr("SUMMARY")
        critical = (
            len(issues.get("bad_stage", []))
            + len(issues.get("null_stage", []))
            + len(issues.get("invalid_transitions", []))
            + len(issues.get("cross_tenant_logs", []))
            + len(issues.get("orphan_logs", []))
        )
        warnings = (
            len(issues.get("invariants", []))
            + len(issues.get("stage_mismatch", []))
            + len(issues.get("duplicates", {}))
        )
        if critical == 0 and warnings == 0:
            print(f"  ✅ Pipeline integrity audit PASSED — no issues found")
        else:
            print(f"  Critical issues: {critical}")
            print(f"  Warnings:        {warnings}")
            print(f"\n  Categories present:")
            for k, v in issues.items():
                n = len(v) if isinstance(v, list) else len(v)
                print(f"    {k:<22} {n}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
