"""Backfill leads.loan_amount_lakh from the free-text loan_amount column.

Dry-run by default — reports parsed/skipped/unparseable counts plus a
sample of each bucket. Pass --apply to persist the parsed values.

Why dry-run first: 6,000+ leads with messy free-text values. Better to
let the user eyeball "35 rows are genuinely weird ('ask brother',
'depends')" and decide before mutating the DB.

Usage:
    .venv/bin/python -m scripts.backfill_loan_amount_lakh         # dry-run
    .venv/bin/python -m scripts.backfill_loan_amount_lakh --apply # persist
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal as AsyncSessionFactory
from app.models.lead import Lead
from app.utils.loan_parser import parse_loan_amount


async def run(apply: bool) -> int:
    async with AsyncSessionFactory() as db:  # type: AsyncSession
        result = await db.execute(
            select(Lead.id, Lead.loan_amount, Lead.loan_amount_lakh)
            .where(Lead.is_deleted == False)  # noqa: E712
        )
        rows = result.all()

    total = len(rows)
    already_set = 0
    null_input = 0
    parsed_ok = 0
    unparseable = 0
    parsed_samples: list[tuple[str, Decimal]] = []
    unparseable_samples: list[str] = []

    to_apply: list[tuple] = []  # (lead_id, parsed_value)

    for lead_id, loan_amount, loan_amount_lakh in rows:
        if loan_amount_lakh is not None:
            already_set += 1
            continue
        if loan_amount is None or not str(loan_amount).strip():
            null_input += 1
            continue
        parsed = parse_loan_amount(loan_amount)
        if parsed is None:
            unparseable += 1
            if len(unparseable_samples) < 30:
                unparseable_samples.append(loan_amount)
            continue
        parsed_ok += 1
        if len(parsed_samples) < 15:
            parsed_samples.append((loan_amount, parsed))
        to_apply.append((lead_id, parsed))

    print()
    print("=" * 70)
    print(f"  LOAN_AMOUNT → LOAN_AMOUNT_LAKH backfill report")
    print("=" * 70)
    print(f"  Total active leads scanned: {total}")
    print(f"  Already populated (skipping): {already_set}")
    print(f"  Empty / NULL loan_amount: {null_input}")
    print(f"  Parsed cleanly: {parsed_ok}")
    print(f"  Unparseable (no number found): {unparseable}")
    print()
    if parsed_samples:
        print("  Sample parse results:")
        for raw, parsed in parsed_samples:
            print(f"    {raw!r:30}  →  {parsed} lakh")
        print()
    if unparseable_samples:
        print(f"  Sample unparseable values (first {len(unparseable_samples)}):")
        # Dedup-and-count so the user sees frequency, not noise.
        counts = Counter(unparseable_samples)
        for value, n in counts.most_common():
            print(f"    {value!r}  (×{n})")
        print()

    if not apply:
        print("  Dry-run complete. Re-run with --apply to persist.")
        return 0

    if not to_apply:
        print("  Nothing to apply. Exiting.")
        return 0

    print(f"  Applying {len(to_apply)} updates...")
    async with AsyncSessionFactory() as db:
        # Batch updates to keep the transaction small and avoid statement
        # cache blow-up on pgbouncer. 500 per chunk is well under any limit.
        chunk = 500
        for i in range(0, len(to_apply), chunk):
            batch = to_apply[i:i + chunk]
            for lead_id, parsed in batch:
                await db.execute(
                    update(Lead)
                    .where(Lead.id == lead_id)
                    .values(loan_amount_lakh=parsed)
                )
            await db.commit()
            print(f"    Committed {min(i + chunk, len(to_apply))}/{len(to_apply)}")
    print("  Done.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist parsed values. Without this flag, runs in dry-run mode.",
    )
    args = parser.parse_args()
    code = asyncio.run(run(apply=args.apply))
    sys.exit(code)


if __name__ == "__main__":
    main()
