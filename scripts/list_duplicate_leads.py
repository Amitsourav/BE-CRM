"""List duplicate leads (same phone within tenant). Read-only."""
import asyncio, os, sys
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.models.lead import Lead


async def main():
    async with AsyncSessionLocal() as db:
        leads = (await db.execute(
            select(Lead).where(Lead.is_deleted == False)  # noqa: E712
        )).scalars().all()

        by_phone = defaultdict(list)
        for l in leads:
            if l.phone:
                by_phone[(l.company_id, l.phone)].append(l)

        dups = {k: v for k, v in by_phone.items() if len(v) > 1}

        # Counts by group size
        sizes = Counter(len(v) for v in dups.values())

        print(f"\n  Total active leads: {len(leads)}")
        print(f"  Phones with duplicates: {len(dups)}")
        print(f"  Total duplicate rows: {sum(len(v) for v in dups.values())}")
        print(f"  Extra rows (would be removed by dedupe): "
              f"{sum(len(v)-1 for v in dups.values())}")
        print(f"\n  Group size distribution:")
        for size, n in sorted(sizes.items()):
            print(f"    {size}-row groups: {n}  → {n*size} total rows, "
                  f"{n*(size-1)} extras")

        # Stage breakdown of dup groups
        only_lead = sum(1 for v in dups.values()
                        if all(l.current_stage == "lead" for l in v))
        with_advance = sum(1 for v in dups.values()
                           if any(l.current_stage != "lead" for l in v))
        print(f"\n  Of the {len(dups)} duplicate groups:")
        print(f"    All rows still at 'lead':   {only_lead}  "
              "(safe — pick any, delete rest)")
        print(f"    At least one advanced:      {with_advance}  "
              "(must keep the advanced one, merge from others)")

        # Show 20 most interesting examples
        print(f"\n  20 examples (mixed stages first, then by call count):\n")
        # Sort: advanced groups first, then by total call_attempt_count desc
        def key(v):
            advanced = max(
                ["lead","called","connected","qualified_lead","won","lost"].index(l.current_stage)
                for l in v
            )
            calls = sum(l.call_attempt_count or 0 for l in v)
            return (-advanced, -calls)

        rows = sorted(dups.values(), key=key)[:20]
        for v in rows:
            phone = v[0].phone
            stages = [l.current_stage for l in v]
            calls = [l.call_attempt_count or 0 for l in v]
            names = [l.full_name or "(no name)" for l in v]
            print(f"  {phone}  {len(v)} rows")
            for l in v:
                print(f"    id={str(l.id)[:8]}  stage={l.current_stage:<14} "
                      f"calls={l.call_attempt_count or 0}  "
                      f"name={l.full_name or '(no name)':<22}  "
                      f"updated={l.updated_at}")
            print()


if __name__ == "__main__":
    asyncio.run(main())
