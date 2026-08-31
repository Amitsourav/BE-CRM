"""Load FMC's commission rates from the revenue tracker into `banks`.

The tracker's "Lender" column is not a bank — it is a ROUTE to money.
`UC Axis` and `Axis Direct (UC Code)` are the same bank reached two ways
and paid at 1.00% and 1.35%; `Nomad US` and `Nomad Normal` are one lender
and two products at 3.00% and 1.60%. Rates therefore attach to the route,
which is why `banks` now holds routes rather than institutions.

Rates come from `Ref_Lists`, which is the table `Student_Master` reads.
Where `Bank_Commission` disagrees (Axis Direct: 1.35 vs 1.40) this uses
the figure `Student_Master` column K actually applies to revenue — 1.35.
That contradiction is in the sheet itself and is reported at the end.

Safe to re-run: existing rows have their rate updated, missing routes are
created. Nothing is deleted.

Usage:
    .venv/bin/python -m scripts.seed_commission_rates            # dry run
    .venv/bin/python -m scripts.seed_commission_rates --apply
"""
from __future__ import annotations

import asyncio
import os
import sys

import asyncpg


# Route -> commission %, transcribed from Ref_Lists (col B/C).
RATES: dict[str, float] = {
    "Propelld": 1.0,
    "Credila": 1.0,
    "IDFC": 1.0,
    "UC Axis": 1.0,
    "UC PNB": 0.7,
    "Kuhoo": 0.8,
    "PNB Direct": 0.7,
    "Axis Direct (UC Code)": 1.35,
    "Nomad US": 3.0,
    "Nomad Normal": 1.6,
    "GyanDhan": 1.0,          # sheet spells it "Gyandhan"; CRM spelling wins
    "ICICI": 1.0,
    "Tata Capital": 1.0,
    "Zolve": 1.25,
    "Edgro": 1.0,
    "Incred": 1.0,
    "BOI": 0.3,
    "Auxilo": 1.0,
    "Avanse": 1.0,
    "UBI": 0.7,
    "Yes Bank": 1.0,
    "Poonawalla": 0.9,
}

# Both UniCred routes to PNB pay 0.7%, so the flat name is unambiguous
# and can carry a rate safely.
UNAMBIGUOUS_FLAT = {"PNB": 0.7}

# Flat names in the CRM that map to MORE THAN ONE route at different
# rates. Deliberately left without a rate: guessing one would silently
# misprice every file on the other route, and the reconciliation report
# is built to report a missing rate rather than invent one.
AMBIGUOUS = {
    "Axis": "UC Axis 1.00% vs Axis Direct (UC Code) 1.35%",
    "Nomad": "Nomad Normal 1.60% vs Nomad US 3.00%",
}

# In the sheet but almost certainly a typo for the CRM's "Poonawalla"
# (one L vs two), and priced differently (1.00 vs 0.90). Not created —
# a near-duplicate lender is exactly the drift `banks` exists to prevent.
SUSPECTED_TYPOS = {"Poonawala": "Poonawalla"}


def _db_url() -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for line in open(os.path.join(here, ".env")):
        if line.startswith("SUPABASE_DB_URL="):
            return line.split("=", 1)[1].strip().strip('"').split("?")[0]
    raise SystemExit("SUPABASE_DB_URL not found in .env")


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_db_url(), statement_cache_size=0, timeout=60)
    try:
        existing = {
            r["name"].lower(): (r["name"], r["commission_rate"])
            for r in await conn.fetch("SELECT name, commission_rate FROM banks")
        }
        usage = {
            r["bank_name"]: r["n"]
            for r in await conn.fetch(
                "SELECT bank_name, count(*) n FROM lead_banks GROUP BY 1"
            )
        }
        max_sort = await conn.fetchval(
            "SELECT coalesce(max(sort_order), 0) FROM banks"
        )

        to_update, to_create = [], []
        for name, rate in {**RATES, **UNAMBIGUOUS_FLAT}.items():
            hit = existing.get(name.lower())
            if hit is None:
                to_create.append((name, rate))
            elif hit[1] is None or float(hit[1]) != rate:
                to_update.append((hit[0], hit[1], rate))

        print("=== rates to SET on existing lenders ===")
        for nm, old, new in to_update:
            print(f"  {nm:<26} {str(old or '—'):>6} -> {new}%   ({usage.get(nm,0)} files)")
        print(f"\n=== routes to CREATE ({len(to_create)}) ===")
        for nm, rate in to_create:
            print(f"  {nm:<26} {rate}%")

        print("\n=== left WITHOUT a rate on purpose ===")
        for nm, why in AMBIGUOUS.items():
            print(f"  {nm:<26} {usage.get(nm,0):>4} files — ambiguous: {why}")
        for nm in sorted(existing):
            real = existing[nm][0]
            if real in AMBIGUOUS or real.lower() in {k.lower() for k in {**RATES, **UNAMBIGUOUS_FLAT}}:
                continue
            print(f"  {real:<26} {usage.get(real,0):>4} files — not in the tracker at all")

        print("\n=== flagged for Amit ===")
        print("  Ref_Lists says Axis Direct (UC Code) = 1.40%,")
        print("  Bank_Commission says 1.35%. Student_Master applies 1.35%")
        print("  to revenue, so 1.35 is used here. One of those tables is wrong.")
        for typo, real in SUSPECTED_TYPOS.items():
            print(f"  '{typo}' in the tracker looks like a typo for '{real}' "
                  f"and is priced differently (1.00 vs 0.90). Not created.")

        if not apply:
            print("\nDRY RUN — nothing written. Re-run with --apply.")
            return

        async with conn.transaction():
            for nm, _old, new in to_update:
                await conn.execute(
                    "UPDATE banks SET commission_rate = $1, updated_at = now() "
                    "WHERE name = $2", new, nm,
                )
            for i, (nm, rate) in enumerate(to_create, start=1):
                await conn.execute(
                    "INSERT INTO banks (name, commission_rate, sort_order, is_active) "
                    "VALUES ($1, $2, $3, true)", nm, rate, max_sort + i,
                )
        print(f"\nAPPLIED — {len(to_update)} updated, {len(to_create)} created.")
        print("Restart the API or wait 60s for the bank-registry cache to expire.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv))
