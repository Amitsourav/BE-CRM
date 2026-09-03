# Reconciliation total mismatch — RESOLVED 2026-09-03

> Kept as a record. No action outstanding for CRM-UI.

## What was reported

The reconciliation screen showed **Total Disbursed ₹15,64,42,667** while
the API's unfiltered total was **₹15,94,01,706** — ₹29,59,039 apart.

## Actual cause

The page applied a **status filter on arrival**:
`?status=to_bill&status=short_paid`, a default introduced from an earlier
brief ("to_bill and short_paid are the money, default the view to them").

The backend then correctly computed `totals` for that filtered set. The
displayed figure was a real API number the whole time — the disbursed
total of the two unsettled statuses.

Verified against production:

| Request | Files | Disbursed |
|---|---|---|
| `?status=to_bill&status=short_paid` | 126 | **₹15,64,42,667** |
| `?status=billed&status=paid&status=written_off` | 3 | ₹29,59,039 |
| no filter | 129 | ₹15,94,01,706 |

The three excluded files were Prit Jain (Credila ₹19,73,521), Rohit
(Kuhoo ₹5,35,512) and Aditya Vaish (GyanDhan ₹4,50,006) — all `paid`.

The real defect was never the arithmetic: **a financial headline was
silently scoped to a subset with nothing on screen saying so.**

## What the original version of this document got wrong

It asserted the frontend was summing `items` instead of reading `totals`.
That was wrong — nothing on the page ever summed rows. The error was
diagnostic: four *unfiltered* paths were checked, all agreed, and that was
read as "no API produces this number" when it only established that no
*unfiltered* API call does. The filtered case was never tried.

A subset-sum did surface the exact three rows making up the difference,
and they were dismissed as coincidence. The check not run was "do these
three share a property?" — they were all `paid`.

## Fix shipped (CRM-UI)

1. **No filter on arrival** — the page opens unfiltered, headline reads
   ₹15,94,01,706 on load.
2. **The headline states its scope** — "Totals cover all 129 files", or a
   Filtered badge reading "Totals cover the N matching files, not the
   whole book" with a Clear link. A headline can no longer disagree with
   the whole book without saying why.
3. **"Needs chasing" button** applies `to_bill` + `short_paid` in one
   click — the worklist the old default existed for, now something the
   user does visibly.

## Backend follow-up (done separately)

`/reconciliation/summary` computed `outstanding_total` as
`commission − (received + tds)`, omitting GST, while `/reconciliation`
used `(commission + gst) − (received + tds)`. The two disagreed by exactly
`gst_total` (₹3,03,763.37 on 2026-09-03). The summary now uses the same
formula and returns `gst_total` per lender.

Not reachable from any headline — the By-lender tab produces no total —
so no UI change was needed.
