"""Rename the company row from 'Admitverse' → 'FundMyCampus' to match
the rebrand. One-off; reusable if there are similar renames later.

Usage:
    python -m scripts.rename_company                # dry run
    python -m scripts.rename_company --apply        # actually rename
    python -m scripts.rename_company --from "X" --to "Y" --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, update
from app.db.session import AsyncSessionLocal
from app.models.company import Company


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="from_name", default="Admitverse")
    parser.add_argument("--to", dest="to_name", default="FundMyCampus")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    print(f"\n  Rename: {args.from_name!r} → {args.to_name!r}")
    print(f"  Mode:   {'APPLY' if args.apply else 'DRY RUN'}\n")

    async with AsyncSessionLocal() as db:
        # Show every company first so the operator sees full context.
        all_companies = (await db.execute(
            select(Company).order_by(Company.created_at)
        )).scalars().all()
        print(f"  Current companies in DB ({len(all_companies)}):")
        for c in all_companies:
            print(f"    {c.id}  {c.name!r}  created={c.created_at}")

        # Find matches
        matches = [c for c in all_companies if c.name == args.from_name]
        print(f"\n  Matching {args.from_name!r}: {len(matches)} row(s)")
        if not matches:
            print(f"  Nothing to rename.")
            return

        if not args.apply:
            print(f"\n  Would rename:")
            for c in matches:
                print(f"    {c.id}  {c.name!r} → {args.to_name!r}")
            print(f"\n  (dry run — re-run with --apply to actually rename)")
            return

        result = await db.execute(
            update(Company)
            .where(Company.name == args.from_name)
            .values(name=args.to_name)
        )
        await db.commit()
        print(f"\n  ✅ Renamed {result.rowcount} company row(s).")

        # Re-read for proof
        again = (await db.execute(
            select(Company).order_by(Company.created_at)
        )).scalars().all()
        print(f"\n  After:")
        for c in again:
            tag = " ← renamed" if c.name == args.to_name and c.id in {m.id for m in matches} else ""
            print(f"    {c.id}  {c.name!r}{tag}")


if __name__ == "__main__":
    asyncio.run(main())
