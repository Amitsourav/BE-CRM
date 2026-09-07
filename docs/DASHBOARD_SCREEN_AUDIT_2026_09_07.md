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

## Pipeline tab — all correct

booked ₹17.23 L · unlockable ₹23.31 L · if fully drawn ₹40.54 L · 38.9%
drawn on ₹24.03 cr undrawn · stage rows (43 / 15 / 10 / 107 / 11,152 with
₹0.50 cr / ₹5.90 cr / ₹6.77 cr / ₹32.48 cr / ₹2.88 cr) · every one of the
25 opportunity rows recomputes exactly as `pending × rate × 0.80`
(Bhavya Singhal ₹2.00 cr → ₹1,60,000; Soumyadeep Pal ₹40,27,606 @1.6% →
₹51,553; Devanshu Chaudhary ₹1.03 cr @0.7% → ₹57,680).

## Forecast tab — all correct

slipped ₹27.66 cr across 7 months · 31 students with no closure month ·
every month row ties (Jun 2026: 33 students, ₹13.65 cr, ₹3.86 cr drawn,
₹9.78 cr pending).

## Revenue & Collections — all correct

Aug-26 tooltip: earned ₹6,14,859, collected ₹2,77,378, 24 releases,
₹4,25,52,050 — matches `monthly` exactly.

## Lender Performance — all correct

UC Axis 23 rel ₹3,01,41,385 owed ₹2,15,454 19.4% 39% · Nomad Normal ·
Axis Direct · UC PNB · Nomad Axis Domestic · Kuhoo · Credila · Propelld ·
PNB — every row ties. Ageing 17 / 18 / 35 / 56 / 0 and ₹7,21,679.

## Source Performance — all correct

Calling 19 students, 21 releases, ₹3,66,43,501, ₹4,88,079, ₹25,688 per
student · all 19 source rows tie · unattributed 1 student ₹8,95,000 0.6%.

## Data Control Centre — all correct

2 / 15 / 61 / 36 / 4 = 118, and the register returns exactly 118.

---

# Findings

| # | Severity | Screen | Finding |
|---|---|---|---|
| 1 | low — wording | Overview donut **and** Pipeline "Where the students are" | 11,152 leads labelled **"Unknown"**. They are the `other` bucket — created / contacted / dnp / lost. "Unknown" reads as a data fault; it is simply the top of the funnel. Relabel **"Other stages"**. It also dwarfs the four loan stages, so the bars that matter are unreadable. |
| 2 | **medium — misleading** | Pipeline → revenue bridge caption | Reads *"3 files excluded — no lender rate"*. There are **two** exclusions and both happen to be 3: `files_missing_rate: 3` **and** `files_missing_sanction: 3`. If they are different files, 6 are excluded and the caption understates it. Say *"3 files with no lender rate and 3 with no sanctioned amount are excluded"*, or sum them. |
| 3 | medium — chart | Pipeline → revenue bridge | The three bars (₹17.23 L, ₹23.31 L, ₹40.54 L) appear to be drawn at roughly **equal width**. If so the chart encodes nothing and the shapes actively mislead — ₹40.54 L should be ~2.4× the width of ₹17.23 L. Confirm against the rendered widths. |
| 4 | low — formatting | Lender Performance → PNB row | Renders as **₹18.00.000** and **₹14.868** — dots where the other rows use commas. Looks like a locale leak (de-DE style grouping) on that row. Every other row is correct, which suggests a value arriving as a differently-typed number. |
| 5 | low — duplication | Revenue & Collections **and** Lender Performance | "Who owes what" and "How old it is" are the **same two panels on both tabs**. One of them has nothing else on it. Either give Lender Performance its own content (concentration via `share_of_disbursed_pct`, collection speed) or fold the tabs together. |
| 6 | trivial | Lender Performance → ageing | *"Cannot be aged — no disbursement date · 0 · ₹0"* renders a row for a bucket that is now empty. Hide at zero. |

**No incorrect figures were found on any tab.**

