# Loan Intelligence — screen-by-screen audit

Amit walked the live dashboard tab by tab on 2026-09-07 and I checked
every figure against the database. Findings collected here as they are
found; **discuss at the end, fix nothing until then.**

Backend at commit `4430365`, deployed 2026-09-07 11:07.

> Numbers move during the audit because the CRM is live. Harsh Agarwal
> #64 (UC Axis, ₹9,20,000, ₹9,200 commission) was entered at 11:27 and
> lifted commission from ₹17,13,572 to ₹17,22,772 mid-session. Where a
> screen disagrees with a later query by exactly that, the screen was
> right when it was taken.

---

## Overview tab — 22 figures checked, 22 correct

Sanctioned ₹48.53 cr / 149 files / 15 unpriced · You keep ₹16,58,510 ·
commission ₹17,13,572 · shared ₹55,062 with ₹4,217 to pay · GST
₹3,08,443 · disbursed ₹15.47 cr at 39% · commission due ₹20.22 L · cash
₹13.10 L at 65% · owed ₹7,12,479 across 17 lenders · 56 overdue >90d ·
61 payments with no date · 15 files with no amount · 2 on an aggregator ·
36 estimated dates · 0 undateable · 4 underpaid · 54 awaiting payment ·
undrawn ₹24.12 cr · ₹23,40,378 still to come · Bhavya Singhal ₹2.00 cr ·
lender pulse (UC Axis ₹2.92 cr, Nomad Normal ₹1.90 cr, Axis Direct
₹2.69 cr, UC PNB ₹49.00 L, Nomad Axis Domestic ₹30.56 L, Kuhoo ₹73.32 L).

**All tie.**

### Findings

| # | Severity | Screen | Finding |
|---|---|---|---|
| 1 | low — wording | Overview → Current stage mix | The donut labels 11,162 leads **"Unknown"**. They are not unknown: they are the leads at created / contacted / dnp / lost — the `other` bucket of `pipeline.stage_funnel`. "Unknown" reads as a data fault when it is simply the top of the funnel, and it dominates the chart so the four loan stages are unreadable. Relabel **"Other stages"**, and consider excluding it from the donut with a caption instead. |

---

*(further tabs appended as they are checked)*
