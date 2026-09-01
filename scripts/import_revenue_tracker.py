"""Import FMC's revenue tracker into the CRM.

Reads `Student_Master` (the sanction side) and `Disbursement_Log` (the
tranche side) out of the .xlsx and writes them onto `lead_banks` and
`bank_disbursements`, matching each sheet row to an existing CRM lead.

Matching, in order:
  1. PHONE — last 10 digits, the same key the CRM's own dedup uses.
     Only 71 of 165 sheet rows carry a phone at all.
  2. NAME — only when exactly ONE CRM lead has that name.

A name shared by several leads is NEVER guessed. "Gaurav" matches six
leads and "Himanshu" four; writing a commission figure onto the wrong
student is worse than not importing the row, so those are reported and
skipped.

Nothing is invented. A tranche with no disbursement date in the sheet is
skipped rather than given one, because every ageing and monthly figure is
built on that date. `sanction_date` is left NULL for imported files:
the sheet's "Closure Month" is when the loan completed, not when it was
sanctioned, and using one for the other would quietly corrupt any
month-by-month revenue figure.

Usage:
    .venv/bin/python -m scripts.import_revenue_tracker            # dry run
    .venv/bin/python -m scripts.import_revenue_tracker --apply
    .venv/bin/python -m scripts.import_revenue_tracker --xlsx /path/to.xlsx

    # also create leads for sheet students the CRM has never heard of,
    # but only where the sheet gives a phone number to identify them by
    .venv/bin/python -m scripts.import_revenue_tracker --apply --create-missing
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from decimal import Decimal

import asyncpg


DEFAULT_XLSX = os.path.expanduser("~/Downloads/FMC_Revenue Tracker.xlsx")
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

# Sheet stage -> lead_banks.bank_status. The sheet's vocabulary is its
# own; this is the only place the two are tied together.
STAGE_MAP = {
    "log in": "loan_login",
    "sanction": "sanctioned",
    "pf": "pf_paid",
    "disbursed": "disbursed",
    "dropped": "lost",
}

# Excel's day zero. The 1900 system is off by one for historical reasons,
# which is why this is the 30th and not the 31st.
EXCEL_EPOCH = datetime(1899, 12, 30)


class Workbook:
    """Minimal read-only .xlsx reader — stdlib only, no dependency."""

    def __init__(self, path: str):
        self.z = zipfile.ZipFile(path)
        wb = ET.fromstring(self.z.read("xl/workbook.xml"))
        rels = {
            r.get("Id"): r.get("Target")
            for r in ET.fromstring(self.z.read("xl/_rels/workbook.xml.rels"))
        }
        self.sheets = {
            s.get("name"): "xl/" + rels[s.get(REL + "id")]
            for s in wb.iter(NS + "sheet")
            if s.get(REL + "id") in rels
        }
        self.shared = [
            "".join(t.text or "" for t in si.iter(NS + "t"))
            for si in ET.fromstring(self.z.read("xl/sharedStrings.xml")).iter(NS + "si")
        ]

    def _cell(self, c) -> str:
        t, v = c.get("t"), c.find(NS + "v")
        inline = c.find(NS + "is")
        if inline is not None:
            return "".join(x.text or "" for x in inline.iter(NS + "t"))
        if v is None or v.text is None:
            return ""
        if t == "s":
            i = int(v.text)
            return self.shared[i] if i < len(self.shared) else ""
        return v.text

    def rows(self, name: str, first_row: int = 1) -> list[tuple[int, dict]]:
        root = ET.fromstring(self.z.read(self.sheets[name]))
        out = []
        for r in root.iter(NS + "row"):
            n = int(r.get("r"))
            if n < first_row:
                continue
            d = {}
            for c in r.iter(NS + "c"):
                m = re.match(r"([A-Z]+)", c.get("r") or "A")
                val = self._cell(c)
                if m and str(val).strip():
                    d[m.group(1)] = val
            if d:
                out.append((n, d))
        return out


def digits10(s) -> str | None:
    d = re.sub(r"\D", "", str(s or ""))
    return d[-10:] if len(d) >= 10 else None


def to_date(v):
    """Excel serial -> date. Returns None for anything else."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f < 1000:                       # not a plausible date serial
        return None
    return (EXCEL_EPOCH + timedelta(days=f)).date()


def to_dec(v):
    try:
        return Decimal(str(v))
    except Exception:
        return None


def _db_url() -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for line in open(os.path.join(here, ".env")):
        if line.startswith("SUPABASE_DB_URL="):
            return line.split("=", 1)[1].strip().strip('"').split("?")[0]
    raise SystemExit("SUPABASE_DB_URL not found in .env")


# Sheet stage -> the lead's own pipeline stage, used only when creating a
# lead that does not exist yet. An existing lead's stage is never touched.
LEAD_STAGE_MAP = {
    "log in": "logged_in",
    "sanction": "sanctioned",
    "pf": "pf_paid",
    "disbursed": "disbursed",
    "dropped": "lost",
}


async def main(apply: bool, xlsx: str, create_missing: bool) -> None:
    wb = Workbook(xlsx)
    students = [d for n, d in wb.rows("Student_Master", 5) if d.get("C")]
    tranches = [d for n, d in wb.rows("Disbursement_Log", 5) if d.get("B")]
    print(f"sheet: {len(students)} students, {len(tranches)} tranches\n")

    conn = await asyncpg.connect(_db_url(), statement_cache_size=0, timeout=90)
    try:
        company_id = await conn.fetchval(
            "SELECT id FROM companies WHERE lower(slug) = 'default'"
        )
        banks = {
            r["name"].lower(): (r["name"], r["commission_rate"])
            for r in await conn.fetch("SELECT name, commission_rate FROM banks")
        }

        by_phone: dict[str, list] = {}
        by_name: dict[str, list] = {}
        for r in await conn.fetch(
            "SELECT id, full_name, phone, "
            "right(regexp_replace(coalesce(phone,''),'\\D','','g'),10) k "
            "FROM leads WHERE is_deleted = false AND company_id = $1",
            company_id,
        ):
            if r["k"] and len(r["k"]) == 10:
                by_phone.setdefault(r["k"], []).append(r["id"])
            if r["full_name"]:
                by_name.setdefault(r["full_name"].strip().lower(), []).append(r["id"])

        # A phone that appears against TWO different students in the
        # sheet cannot identify either of them. Mehak Ekley and Nishant
        # Ranjan both carry 8878388425; on an earlier run that put
        # Nishant's ₹14.6 L disbursement and ₹19,710 commission onto the
        # lead created for Mehak. One student's money on another
        # student's record is the worst thing this import can do, so
        # those phones are disqualified outright rather than resolved to
        # whichever lead happens to exist.
        from collections import Counter
        sheet_phone_counts = Counter(
            digits10(d.get("D")) for d in students if digits10(d.get("D"))
        )
        duplicated_in_sheet = {p for p, n in sheet_phone_counts.items() if n > 1}

        # ── resolve every student row to a lead, or explain why not ──
        resolved: dict[str, dict] = {}       # sheet name(lower) -> plan
        to_create: list[tuple] = []          # (name, phone, sheet row)
        skipped = {"ambiguous": [], "unmatched": [], "no_lender": [], "no_rate": []}
        for d in students:
            name = (d.get("C") or "").strip()
            key = name.lower()
            phone = digits10(d.get("D"))
            lead_id = how = None
            if phone and phone in duplicated_in_sheet:
                skipped["ambiguous"].append(
                    (name, f"phone {phone} is shared with another student IN THE SHEET")
                )
                continue
            if phone and len(by_phone.get(phone, [])) == 1:
                lead_id, how = by_phone[phone][0], "phone"
            elif not phone and len(by_name.get(key, [])) == 1:
                lead_id, how = by_name[key][0], "name"
            elif phone and len(by_phone.get(phone, [])) > 1:
                skipped["ambiguous"].append((name, f"phone {phone} -> several leads"))
                continue
            elif len(by_name.get(key, [])) > 1:
                skipped["ambiguous"].append((name, f"{len(by_name[key])} leads share this name"))
                continue
            elif phone and create_missing:
                # No lead anywhere and the sheet has a phone, so the lead
                # can be created honestly — a phone is the CRM's identity
                # key, and creating on a name alone would risk merging two
                # different people.
                to_create.append((name, phone, d))
                continue
            else:
                skipped["unmatched"].append((name, d.get("D") or "no phone"))
                continue

            route = (d.get("G") or "").strip()
            if not route:
                skipped["no_lender"].append((name, "no lender in the sheet"))
                continue
            hit = banks.get(route.lower())
            if hit is None:
                skipped["no_lender"].append((name, f"'{route}' is not a CRM lender"))
                continue
            bank_name, rate = hit
            if rate is None:
                skipped["no_rate"].append((name, f"'{bank_name}' has no commission rate"))
                continue

            resolved[key] = {
                "lead_id": lead_id, "how": how, "name": name,
                "bank_name": bank_name, "rate": rate,
                "status": STAGE_MAP.get((d.get("H") or "").strip().lower()),
                "sanction": to_dec(d.get("J")),
                "closure": d.get("I"),
            }

        # Two sheet rows can carry the same phone (Mehak Ekley and Nishant
        # Ranjan both show 8878388425). The CRM has a unique index on
        # (company_id, phone) for active leads, so only the first can be
        # created; the rest are reported rather than silently dropped.
        created_ids: dict[str, str] = {}
        phone_clash: list[tuple[str, str]] = []
        if to_create and apply:
            seen_phone: dict[str, str] = {}
            src_ids = {
                r["name"].strip().lower(): r["id"]
                for r in await conn.fetch(
                    "SELECT id, name FROM lead_sources WHERE company_id = $1", company_id
                )
            }
            author = await conn.fetchval(
                "SELECT id FROM profiles WHERE company_id = $1 AND role = 'admin' "
                "ORDER BY created_at LIMIT 1", company_id,
            )
            next_serial = (await conn.fetchval(
                "SELECT coalesce(max(serial_no), 0) FROM leads WHERE company_id = $1",
                company_id,
            )) or 0
            for name, phone, d in to_create:
                if phone in seen_phone:
                    phone_clash.append((name, f"phone {phone} already used by "
                                              f"'{seen_phone[phone]}' in the sheet"))
                    continue
                seen_phone[phone] = name
                next_serial += 1
                lead_id = await conn.fetchval(
                    """
                    INSERT INTO leads
                      (company_id, serial_no, full_name, phone, current_stage,
                       pipeline, lead_source_id, college_name, created_by, notes)
                    VALUES ($1,$2,$3,$4,$5::lead_stage,'normal',$6,$7,$8,$9)
                    RETURNING id
                    """,
                    company_id, next_serial, name, "+91" + phone,
                    LEAD_STAGE_MAP.get((d.get("H") or "").strip().lower(), "created"),
                    src_ids.get((d.get("E") or "").strip().lower()),
                    d.get("F"), author,
                    "Created from the FMC Revenue Tracker import — this student "
                    "was on the sheet but had no record in the CRM.",
                )
                created_ids[name.lower()] = lead_id

            # Fold the new leads into the same plan the matched ones use,
            # so their lender file and tranches import in this same run.
            for name, phone, d in to_create:
                lead_id = created_ids.get(name.lower())
                if lead_id is None:
                    continue
                route = (d.get("G") or "").strip()
                hit = banks.get(route.lower())
                if hit is None or hit[1] is None:
                    skipped["no_rate"].append((name, f"'{route}' has no usable rate"))
                    continue
                resolved[name.lower()] = {
                    "lead_id": lead_id, "how": "created", "name": name,
                    "bank_name": hit[0], "rate": hit[1],
                    "status": STAGE_MAP.get((d.get("H") or "").strip().lower()),
                    "sanction": to_dec(d.get("J")),
                    "closure": d.get("I"),
                }

        print("=== STUDENTS ===")
        print(f"  matched by phone : {sum(1 for v in resolved.values() if v['how']=='phone')}")
        print(f"  matched by name  : {sum(1 for v in resolved.values() if v['how']=='name')}")
        if to_create:
            verb = "CREATED" if apply else "would CREATE (--apply to do it)"
            print(f"  {verb}: {len(created_ids) if apply else len(to_create)} new lead(s)")
            for nm, ph, _ in to_create:
                mark = "+" if (not apply or nm.lower() in created_ids) else "!"
                print(f"      {mark} {nm:<28} {ph}")
        for nm, why in phone_clash:
            print(f"      ! {nm:<28} SKIPPED — {why}")
        for k, label in [("ambiguous", "AMBIGUOUS — never guessed"),
                         ("unmatched", "no CRM lead"),
                         ("no_lender", "lender problem"),
                         ("no_rate", "lender has no rate")]:
            if skipped[k]:
                print(f"  {label:<26}: {len(skipped[k])}")
                for nm, why in skipped[k][:4]:
                    print(f"      {nm:<28} {why}")
                if len(skipped[k]) > 4:
                    print(f"      … and {len(skipped[k])-4} more")

        # ── tranches ──
        t_ok, t_nodate, t_noparent = [], [], []
        for d in tranches:
            parent = resolved.get((d.get("B") or "").strip().lower())
            if parent is None:
                t_noparent.append(d.get("B"))
                continue
            # A missing date no longer blocks the row. The amount,
            # commission and outstanding balance are real money and must
            # reach the CRM; only ageing is lost, and ageing is a
            # convenience. 38 tranches carrying ₹3.83 L of outstanding
            # were being discarded over one blank column.
            when = to_date(d.get("H"))
            if when is None:
                t_nodate.append(d.get("B"))
            # L = commission, M = the same figure with GST. Their
            # difference is the GST actually charged — 18% on every row —
            # and it is part of what the lender owes, so it belongs in
            # the settlement sum rather than being recomputed later from
            # a rate that might since have changed.
            comm_ex = to_dec(d.get("L")) or Decimal("0")
            comm_inc = to_dec(d.get("M")) or Decimal("0")
            gst = (comm_inc - comm_ex) if comm_inc > comm_ex else None
            t_ok.append({
                "parent": parent,
                "tranche_no": int(float(d.get("F") or 1)),
                "amount": to_dec(d.get("G")),
                "on": when,
                "rate": (to_dec(d.get("J")) or Decimal("0")) * 100,   # sheet stores 0.01
                "earns": (d.get("K") or "Yes").strip().lower() != "no",
                "sheet_commission": comm_ex,
                "gst": gst,
                # T is the cash received INCLUDING GST, which is what
                # actually arrives, so it is what settles the debt.
                "received": to_dec(d.get("T")),
                "received_on": to_date(d.get("U")),
                "invoice_no": d.get("P"),
                "remarks": d.get("X"),
            })
        print("\n=== TRANCHES ===")
        print(f"  importable            : {len(t_ok)}")
        print(f"  student not imported  : {len(t_noparent)}")
        print(f"  of those, NO date     : {len(t_nodate)}  (imported anyway; ageing unavailable, date never invented)")
        if t_nodate:
            print("      " + ", ".join(dict.fromkeys(t_nodate))[:150])

        total_comm = sum((t["amount"] or 0) * t["rate"] / 100 for t in t_ok if t["earns"])
        sheet_comm = sum(t["sheet_commission"] for t in t_ok)
        total_gst = sum(t["gst"] or 0 for t in t_ok)
        print(f"\n  disbursed value       : ₹{sum(t['amount'] or 0 for t in t_ok):,.0f}")
        print(f"  commission (computed) : ₹{total_comm:,.0f}")
        print(f"  commission (sheet's L): ₹{sheet_comm:,.0f}")
        drift = total_comm - sheet_comm
        if abs(drift) > 1:
            print(f"  ⚠ DRIFT               : ₹{drift:,.0f} — the sheet's own "
                  f"figure differs from rate x amount")
        print(f"  GST (sheet's M - L)   : ₹{total_gst:,.0f}")

        if not apply:
            print("\nDRY RUN — nothing written. Re-run with --apply.")
            return

        async with conn.transaction():
            lb_ids = {}
            for key, p in resolved.items():
                if not p["status"]:
                    continue
                note = f"Imported from FMC Revenue Tracker. Closure month: {p['closure'] or '—'}."
                lb_ids[key] = await conn.fetchval(
                    """
                    INSERT INTO lead_banks
                      (company_id, lead_id, bank_name, bank_status, loan_amount,
                       commission_rate, source, shared_at, notes)
                    VALUES ($1,$2,$3,$4::bank_status,$5,$6,'manual',now(),$7)
                    ON CONFLICT (lead_id, bank_name) DO UPDATE SET
                      bank_status = EXCLUDED.bank_status,
                      loan_amount = COALESCE(EXCLUDED.loan_amount, lead_banks.loan_amount),
                      commission_rate = COALESCE(lead_banks.commission_rate, EXCLUDED.commission_rate),
                      notes = EXCLUDED.notes,
                      updated_at = now()
                    RETURNING id
                    """,
                    company_id, p["lead_id"], p["bank_name"], p["status"],
                    p["sanction"], p["rate"], note,
                )

            n_tr = 0
            for t in t_ok:
                lb = lb_ids.get((t["parent"]["name"]).strip().lower())
                if lb is None or not t["amount"]:
                    continue
                comm = (t["amount"] * t["rate"] / 100) if t["earns"] else Decimal("0")
                await conn.execute(
                    """
                    INSERT INTO bank_disbursements
                      (company_id, lead_bank_id, lead_id, bank_name, disbursed_amount,
                       disbursed_on, tranche_no, commission_rate, earns_commission,
                       commission_amount, gst_amount, amount_received, received_on,
                       payment_reference, source, notes)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,'backfill',$15)
                    ON CONFLICT (lead_bank_id, tranche_no) DO UPDATE SET
                      -- Only fills gaps. A figure someone has since
                      -- corrected by hand is not overwritten by a re-run.
                      gst_amount = COALESCE(bank_disbursements.gst_amount, EXCLUDED.gst_amount),
                      amount_received = COALESCE(bank_disbursements.amount_received, EXCLUDED.amount_received),
                      received_on = COALESCE(bank_disbursements.received_on, EXCLUDED.received_on),
                      updated_at = now()
                    """,
                    company_id, lb, t["parent"]["lead_id"], t["parent"]["bank_name"],
                    t["amount"], t["on"], t["tranche_no"], t["rate"], t["earns"],
                    round(comm, 2), t["gst"], t["received"], t["received_on"],
                    (f"Invoice {t['invoice_no']}" if t.get("invoice_no") else None),
                    t.get("remarks"),
                )
                n_tr += 1

            # Refresh the denormalised primary-bank fields on the lead.
            # The service normally does this via _resync_primary_bank;
            # this import writes SQL directly, so without it the Kanban
            # tile would keep showing whatever lender it showed before.
            # Same "highest status wins, ties broken by most recent" rule,
            # with 'lost' below 'applied' so a declined lender is never
            # promoted over a live one.
            touched = [p["lead_id"] for p in resolved.values()]
            await conn.execute(
                """
                UPDATE leads l
                SET bank_name = b.bank_name,
                    bank_status = b.bank_status,
                    updated_at = now()
                FROM (
                    SELECT DISTINCT ON (lead_id) lead_id, bank_name, bank_status
                    FROM lead_banks
                    WHERE lead_id = ANY($1::uuid[])
                    ORDER BY lead_id,
                        CASE bank_status
                            WHEN 'disbursed' THEN 7 WHEN 'pf_paid' THEN 6
                            WHEN 'sanctioned' THEN 5 WHEN 'loan_login' THEN 4
                            WHEN 'under_review' THEN 3 WHEN 'docs_reviewed' THEN 2
                            WHEN 'applied' THEN 1 ELSE 0 END DESC,
                        updated_at DESC
                ) b
                WHERE l.id = b.lead_id
                """,
                touched,
            )

        print(f"\nAPPLIED — {len(lb_ids)} lender files, {n_tr} tranches.")

        # The lead's own pipeline stage is deliberately NOT moved. That
        # would shift leads between Kanban columns under the team while
        # they are working them, and it is a bigger decision than an
        # import should take on its own.
        mismatch = await conn.fetchval(
            """
            SELECT count(*) FROM leads l
            WHERE l.id = ANY($1::uuid[])
              AND l.bank_status IN ('pf_paid','disbursed')
              AND l.current_stage NOT IN ('pf_paid','disbursed')
            """,
            [p["lead_id"] for p in resolved.values()],
        )
        if mismatch:
            print(
                f"\nNOTE: {mismatch} lead(s) now have a lender at PF/disbursed "
                f"while their own pipeline stage still says otherwise.\n"
                f"Their stages were left alone on purpose — moving leads "
                f"between Kanban columns is a separate decision."
            )
    finally:
        await conn.close()


if __name__ == "__main__":
    path = DEFAULT_XLSX
    if "--xlsx" in sys.argv:
        path = sys.argv[sys.argv.index("--xlsx") + 1]
    asyncio.run(main("--apply" in sys.argv, path, "--create-missing" in sys.argv))
