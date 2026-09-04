# Reconciliation dashboard — frontend build prompt

> For the dev/agent working in the **CRM-UI** repo.
> Backend is live. One new endpoint, no changes to anything existing.
> Written 2026-09-04 against production data.

---

## 1. What this is

FMC's commission book reconciles to its revenue tracker to the rupee.
Getting there took two days of hand-written SQL, because the CRM could
show you *rows* and could not answer a single question about the *book*.

This dashboard answers five:

1. How much of what lenders approved has actually come out?
2. How much is still to come, and what is it worth?
3. Are we collecting faster or slower than we are earning?
4. Who owes us, and how old is it?
5. What in here can't be trusted?

One page. Executive summary on top, operational detail below.

### Vocabulary — use these exact words on screen

| Term | Means |
|---|---|
| **Sanctioned** | What lenders approved |
| **Confirmed** | Student paid the PF. FMC's proof the loan is real and which lender won it |
| **Disbursed** | What the bank actually released, in tranches |
| **Earned** | Commission + GST on what was disbursed. Entitlement, not cash |
| **Collected** | Cash received + TDS withheld. Both discharge the debt |
| **Outstanding** | Earned − collected |
| **Undrawn** | Confirmed but not yet released — future commission |

**Sanctioned is not Disbursed.** A ₹10 L sanction comes out semester by
semester. FMC earns per release, never on the sanction. Confusing the two
is the single most expensive mistake in this domain — it produced ₹1.36
crore of wrong figures in the CRM before it was caught.

> **All amounts in the response are RUPEES.** No conversion. Format with
> Indian grouping — ₹15,15,50,551, never ₹151,550,551.

---

## 2. The endpoint

```http
GET /api/v1/reconciliation/dashboard?months=12
```

**Admin only** (403 otherwise — hide the nav item). **FMC only** (400 on
Admitverse — gate on brand). One call fills the whole page.

| Param | | |
|---|---|---|
| `months` | int, 1-24, default 12 | length of the trend series |

No other filters. Every panel is the whole book by definition — use
`GET /reconciliation` when the user wants to slice.

Cached 60s server-side. A hard refresh may return the same numbers; that
is intended.

```jsonc
{
  "funnel": {
    "sanctioned_total": 485316918.00,      // RUPEES
    "sanctioned_files": 175,
    "confirmed_total": 393248742.00,
    "confirmed_files": 130,
    "disbursed_total": 151550551.00,
    "tranches": 125,
    "earned_total": 1982600.14,            // commission + GST
    "collected_total": 1309816.19,         // received + TDS
    "outstanding_total": 672783.95,
    "confirmed_pct_of_sanctioned": 81.0,   // each step vs the one BEFORE it
    "disbursed_pct_of_confirmed": 38.5,
    "collected_pct_of_earned": 66.1
  },
  "pipeline_ahead": {
    "confirmed_files": 115,
    "sanctioned_total": 393248742.00,
    "drawn_total": 148855551.00,
    "undrawn_total": 244393191.00,
    "future_commission": 2371858.00,       // a FLOOR, see below
    "drawn_pct": 37.9,
    "files_missing_rate": 2
  },
  "monthly": [
    { "month": "2026-08", "tranches": 17, "disbursed": 29550290.00,
      "earned": 418974.00, "collected": 277378.00 }
  ],
  "by_lender": [
    { "bank_name": "UC Axis", "tranches": 21, "disbursed_total": 26073385.00,
      "earned_total": 307469.00, "collected_total": 138361.00,
      "outstanding_total": 169108.00, "collected_pct": 45.0 }
  ],
  "ageing": {
    "buckets": [ { "bucket": "0_30", "tranches": 14, "outstanding": 95532.00 } ],
    "total_outstanding": 673064.00,
    "undateable_outstanding": 376726.00,
    "undateable_pct": 56.0
  },
  "data_quality": {
    "tranches": 125,
    "tranches_without_date": 37,
    "payments_without_receipt_date": 61,
    "tranches_with_tds": 0,
    "tranches_awaiting_payment": 53,
    "tranches_short": 71,
    "tranches_materially_short": 4,
    "tranches_written_off": 0,
    "tranches_earning_nothing": 0,
    "live_files": 175,
    "files_without_sanctioned_amount": 41,
    "files_that_cannot_be_priced": 14,
    "files_on_aggregator": 14
  }
}
```

---

## 3. Row 1 — the funnel

```
┌──────────────────────────────────────────────────────────────────┐
│  Sanctioned      Confirmed       Disbursed      Earned  Collected│
│  ₹48.53 cr  →   ₹39.32 cr   →   ₹15.16 cr  →  ₹19.83 L → ₹13.10 L│
│               81% of sanctioned  38% of conf.          66% earned│
└──────────────────────────────────────────────────────────────────┘
```

Five tiles left to right. **Each percentage is against the step before
it, not against the top** — that is what makes a weak step visible. Show
the percentage under the tile it belongs to.

`disbursed_pct_of_confirmed` at 38% is the number that matters: most
approved money has not come out yet.

---

## 4. Row 2 — coming and owed

Two tiles side by side.

```
┌── STILL TO COME ──────────────┬── OWED TO US ─────────────────┐
│ ₹24.44 cr undrawn             │ ₹6,72,784                     │
│ → ₹23,71,858 commission       │ across 17 lenders             │
│ 37.9% of confirmed drawn      │ 56% of it cannot be aged      │
└───────────────────────────────┴───────────────────────────────┘
```

**`future_commission` is a FLOOR, not an estimate.** Files whose lender
has no configured rate are excluded rather than counted as zero.
When `files_missing_rate > 0`, say "at least" before the figure.

---

## 5. Row 3 — earned vs collected, by month

Grouped bars or two lines. `earned` by disbursement month, `collected` by
receipt month.

**They are different dates on purpose.** A tranche earned in June and
paid in August appears in both months, in different columns. Do not try
to reconcile a single month's two bars.

Months with no activity are absent, not zero-filled. Plot the months you
are given, in order.

**Tranches with no date appear in NO month.** On this book that is 37
tranches and ₹3.90 cr of disbursement. Put a footnote under the chart
reading off `data_quality.tranches_without_date` — without it the trend
understates every month and nobody knows why.

---

## 6. Row 4 — who owes, and how old

Side by side: lender table left, ageing right.

```
LENDER                 tr   disbursed      owed   collected
UC Axis                21  ₹2,60,73,385  ₹1,69,108    45%
Nomad Normal            4  ₹1,90,16,195  ₹1,66,515    54%
Axis Direct (UC Code)  30  ₹2,68,72,314  ₹1,32,755    69%
```

Already sorted by what is owed, then by size. Render in the order given.
Lenders owing nothing still appear — seeing your biggest route matters
even in a month it owes nothing.

```
AGEING          tranches      owed
0-30 days             14   ₹95,532
31-60                  9  ₹1,24,988
61-90                 18   ₹22,694
90+                   46   ₹53,124   ← red
no date               37  ₹3,76,726   ← amber, 56%
```

`bucket` is one of `0_30` `31_60` `61_90` `over_90` `no_date`.

**`no_date` is a real bucket, not an error.** It holds the majority of
everything outstanding here. A panel that hides it understates the debt
by more than half. Render it last, visually distinct, labelled
"Cannot be aged — no disbursement date".

Only rows still owing appear; settled rows leave the buckets entirely.

> `ageing.total_outstanding` can exceed `funnel.outstanding_total` by a
> few rupees. That is correct. Ageing sums per-row shortfall, which floors
> at zero, so a lender that overpaid one tranche cannot cancel out what it
> owes on another. The funnel is the net position; ageing is what is
> chaseable. Do not "fix" the difference.

---

## 7. Row 5 — needs attention

A row of small counters. Not decoration — these say why a number above
might be wrong, and every dashboard that hides them ends up trusted more
than it deserves.

| Counter | Label | Why it matters |
|---|---|---|
| `tranches_without_date` | Undateable | Missing from every month and from ageing |
| `payments_without_receipt_date` | Payments with no date | Collection trend understated |
| `files_without_sanctioned_amount` | Files with no amount | Excluded from sanctioned and from future commission |
| `files_on_aggregator` | On an aggregator | **Can never earn** until moved to a real route |
| `tranches_materially_short` | Genuinely underpaid | The only "short" number worth acting on |

### The three "short" counters are not the same thing

- `tranches_awaiting_payment` — nothing received at all (53)
- `tranches_short` — paid, but light (71)
- `tranches_materially_short` — short by over ₹100 **and** over 2% (4)

**Show `tranches_materially_short` as the headline.** The gap between it
and `tranches_short` is rounding between the lender's arithmetic and
ours — on this book, 67 of the 71 are trivial. A tile reading "71 lenders
underpaid us" is wrong and will be ignored within a week.

**`files_on_aggregator`** are files parked on UniCred / Nomad / Axis
rather than the specific route beneath. An aggregator fronts several
banks at different rates, so it has none of its own. These earn nothing
until someone moves them — make it clickable through to the lender list
if you can.

---

## 8. Deliberately absent: invoicing

There are no invoice figures here and no `to_bill` / `billed` split, on
purpose. FMC raises its bills outside the CRM — `invoice_service` never
touches disbursements, so `invoice_id` is only ever set by a direct
database write and the `billed` status is unreachable through the API.

**Do not add an "unbilled" tile.** It would report a gap that is really a
workflow living somewhere else. The existing reconciliation report still
shows the five row statuses; that is a different screen.

---

## 9. Errors

| Situation | Response |
|---|---|
| Not an admin | 403 — hide the page from the nav entirely |
| Admitverse tenant | 400 with a readable `detail` — gate on brand, don't show and fail |
| `months` outside 1-24 | 422 |

---

## 10. Notes

- The first load takes a few seconds (nine aggregates against Supabase
  Korea). It is cached 60s after that. Show a skeleton, not a spinner on
  an empty page.
- Every money field is `Decimal` serialised as a JSON number. Do not
  round for display beyond whole rupees; do not do arithmetic on them in
  the client — every figure you need is already computed.
- Nothing here is editable. It is a read surface.

---

## Acceptance

- [ ] Funnel shows 5 tiles, each percentage against the previous step
- [ ] "Still to come" says "at least" when `files_missing_rate > 0`
- [ ] Trend plots earned and collected as separate series, months in order
- [ ] Footnote under the trend naming the undateable tranche count
- [ ] Lender table rendered in the order given, not re-sorted
- [ ] Ageing shows all five buckets including `no_date`, visually distinct
- [ ] `tranches_materially_short` is the headline, not `tranches_short`
- [ ] No invoice tile anywhere
- [ ] Indian digit grouping throughout (₹15,15,50,551)
- [ ] 403 hides the page; 400 on Admitverse never reaches the user
