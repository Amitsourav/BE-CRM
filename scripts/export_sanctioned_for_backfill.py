"""Export sanctioned files that are missing the figures gross theoretical
revenue needs, as a CSV to fill in by hand.

Gross theoretical revenue is the lender's rate applied to the amount it
SANCTIONED. Marking a file sanctioned now captures that amount and the
sanction date, but the files that reached that status earlier did not:
as of 2026-08-31, 48 of 79 carried no amount and 77 carried no date. Those
files are excluded from GTR rather than counted as zero, so until they are
filled in the figure is a floor, not the whole book.

Nothing here is guessed. The lender comes from the CRM, and where the lead
carries its own free-text loan figure it is offered as a HINT in its own
column — never written into the amount column, because that figure is what
the student asked for, not what the bank approved. The sanction date is
left blank on purpose: a wrong date silently corrupts every monthly GTR
figure built on top of it, and there is nothing in the database to derive
it from.

Usage:
    .venv/bin/python -m scripts.export_sanctioned_for_backfill

Writes exports/sanctioned_backfill_<today>.csv. Fill in
`sanctioned_amount_lakh` and `sanction_date`, then load it back through
PATCH /leads/{lead_id}/banks/{entry_id}.
"""
from __future__ import annotations

import asyncio
import csv
import os
from datetime import date

import asyncpg


QUERY = """
SELECT
    l.serial_no,
    l.full_name,
    l.phone,
    b.bank_name,
    b.bank_status,
    b.id            AS entry_id,
    l.id            AS lead_id,
    b.loan_amount   AS sanctioned_amount_rupees,
    b.sanction_date,
    b.commission_rate,
    -- HINT ONLY. This is the lead's own free-text figure — what the
    -- student asked for, not what the bank approved. Never copy it into
    -- the amount column without checking.
    l.loan_amount   AS lead_stated_amount_hint,
    p.full_name     AS counsellor
FROM lead_banks b
JOIN leads l ON l.id = b.lead_id
LEFT JOIN profiles p ON p.id = l.assigned_agent_id
WHERE b.bank_status IN ('sanctioned', 'pf_paid', 'disbursed')
  AND l.is_deleted = false
  AND (b.loan_amount IS NULL OR b.sanction_date IS NULL)
ORDER BY b.bank_name, l.serial_no
"""

COLUMNS = [
    "serial_no", "full_name", "phone", "bank_name", "bank_status",
    "lead_stated_amount_hint", "counsellor",
    # ── fill these two in ──
    "sanctioned_amount_lakh", "sanction_date",
    # ── identifiers, do not edit ──
    "entry_id", "lead_id",
]


def _db_url() -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for line in open(os.path.join(here, ".env")):
        if line.startswith("SUPABASE_DB_URL="):
            return line.split("=", 1)[1].strip().strip('"').split("?")[0]
    raise SystemExit("SUPABASE_DB_URL not found in .env")


async def main() -> None:
    conn = await asyncpg.connect(_db_url(), statement_cache_size=0, timeout=60)
    try:
        rows = await conn.fetch(QUERY)
    finally:
        await conn.close()

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(here, "exports")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"sanctioned_backfill_{date.today()}.csv")

    missing_amount = missing_date = 0
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            if r["sanctioned_amount_rupees"] is None:
                missing_amount += 1
            if r["sanction_date"] is None:
                missing_date += 1
            w.writerow({
                "serial_no": r["serial_no"] or "",
                "full_name": r["full_name"] or "",
                "phone": r["phone"] or "",
                "bank_name": r["bank_name"],
                "bank_status": r["bank_status"],
                "lead_stated_amount_hint": r["lead_stated_amount_hint"] or "",
                "counsellor": r["counsellor"] or "",
                # Pre-filled ONLY where the amount is already known, so the
                # person filling this in can see at a glance which rows
                # actually need work.
                "sanctioned_amount_lakh": (
                    r["sanctioned_amount_rupees"] / 100000
                    if r["sanctioned_amount_rupees"] is not None else ""
                ),
                "sanction_date": r["sanction_date"] or "",
                "entry_id": str(r["entry_id"]),
                "lead_id": str(r["lead_id"]),
            })

    print(f"{len(rows)} file(s) need attention -> {out}")
    print(f"  missing the sanctioned amount: {missing_amount}")
    print(f"  missing the sanction date:     {missing_date}")
    print(
        "\nFill in sanctioned_amount_lakh (in LAKHS) and sanction_date "
        "(YYYY-MM-DD).\nLeave a row blank if the figure genuinely isn't "
        "known — a guessed date is worse\nthan a missing one, because every "
        "monthly figure is built on it."
    )


if __name__ == "__main__":
    asyncio.run(main())
