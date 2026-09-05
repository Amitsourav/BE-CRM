# Loan Intelligence Dashboard — frontend build brief

> For the dev/agent working in the **CRM-UI** repo.
> Built against Amit's design at
> `fundmycampus-loan-analytics.nishuash.chatgpt.site`.
> Every endpoint below is live. Rewritten 2026-09-05 — supersedes the
> earlier single-page version of this document.

---

## 1. What this is

*"Sanction to cash collection — one operating view."*

Six tabs, global filters, and **every segment clickable to open the exact
students behind it.** That last part is the point: a number nobody can
open is a number nobody trusts.

### Vocabulary — use these exact words on screen

| Term | Means |
|---|---|
| **Sanctioned** | What lenders approved |
| **Confirmed** | Student paid the PF — proof the loan is real and which lender won it |
| **Disbursed** | What the bank actually released, in tranches |
| **Earned** | Commission + GST on what was disbursed. Entitlement, not cash |
| **Collected** | Cash received + TDS withheld. Both discharge the debt |
| **Outstanding** | Earned − collected |
| **Undrawn** | Confirmed but not yet released — future commission |

**Sanctioned is not Disbursed.** A ₹10 L sanction comes out semester by
semester and FMC earns per release. Confusing the two produced ₹1.36
crore of wrong figures before it was caught.

> **Every amount in every response is RUPEES.** Format with Indian
> grouping — ₹15,46,98,551, never ₹154,698,551.

---

## 2. Global filters — one set, every tab

Every endpoint below takes the same four, as repeatable query params:

| Param | |
|---|---|
| `bank_name` | repeatable — matches any of the supplied lenders |
| `source_id` | repeatable — matches any of the supplied lead sources |
| `disbursed_from` / `disbursed_to` | date range on the disbursement date |

Hold the filter state in ONE object and spread it into every call. Passing
none means the whole book.

**Two controls in the mockup cannot be built yet:**

- **"All closure months"** — the CRM has no closure month. It exists only
  in the spreadsheet; no column, and zero leads carry it. A real
  `expected_closure_month` field is planned. **Until then, label the month
  control "Disbursement month"** and drive it off `disbursed_from` /
  `disbursed_to`. Do not label it "closure" — that is the exact class of
  mislabelling that cost two days of reconciliation.
- **"invoiced" in Cash Control** — there is no invoiced figure. FMC bills
  outside the CRM; `invoice_service` never touches disbursements, so
  `invoice_id` is only ever set by a direct database write. Show earned,
  collected and outstanding. **Do not add an invoiced column.**

---

## 3. Drill-down — build this first

```http
GET /api/v1/reconciliation/drilldown?segment=<kind>&value=<v>&<filters>
```

`segment` ∈ `stage` · `lender` · `ageing_bucket` · `source` ·
`funnel_step`. For a source, `value` is the source id or the literal
`unattributed`.

```jsonc
{
  "segment": "ageing_bucket", "value": "over_90",
  "total": 41,            // STUDENTS
  "tranche_total": 46,    // TRANCHES in this segment
  "page": 1, "page_size": 50,
  "items": [ { "lead_id": "...", "serial_no": 3934, "full_name": "Esha",
               "stage": "disbursed", "bank_name": "UC Axis",
               "sanctioned": 20000000.00, "disbursed": 4730000.00,
               "earned": 47300.00, "collected": 0.00,
               "outstanding": 47300.00 } ]
}
```

> **Two counts, and you need both.** The panels do not all count the same
> thing — ageing and by-lender count TRANCHES, the stage funnel counts
> STUDENTS. Header the drawer **"41 students · 46 tranches"**. Show only
> one and the drill-down will look like it contradicts the number the user
> just clicked.

Verified: every segment returns exactly what its panel claimed. `lead_id`
links to the lead page.

---

## 4. Overview tab

`GET /api/v1/reconciliation/dashboard?months=12&<filters>`

Six panels in one call: `funnel`, `pipeline_ahead`, `monthly`,
`by_lender`, `ageing`, `data_quality`. Cached 60s.

### Flow of money

```
Sanctioned  →  Confirmed  →  Disbursed  →  Earned  →  Collected
 ₹48.53cr      ₹39.32cr      ₹15.47cr     ₹20.1L     ₹13.1L
              81% of sanc.  38% of conf.            66% earned
```

**Each percentage is against the step before it, not against the top.**
That is what makes a weak step visible — 38% drawdown is the headline
finding on this book.

Click any step → `drilldown?segment=funnel_step&value=sanctioned|confirmed|disbursed`.

### Lender pulse

`by_lender` is pre-sorted by what is owed. Each row carries
`share_of_disbursed_pct` — that single field drives **Portfolio Mix** and
**Concentration Risk** with no extra call.

---

## 5. Pipeline & Forecast tab

```http
GET /api/v1/reconciliation/pipeline?limit=20&<filters>
```

### Stage funnel — volume AND value

```jsonc
"stage_funnel": [
  { "stage": "logged_in",  "leads": 41,  "sanctioned": 5000000,   "disbursed": 0 },
  { "stage": "sanctioned", "leads": 14,  "sanctioned": 57318176,  "disbursed": 0 },
  { "stage": "pf_paid",    "leads": 11,  "sanctioned": 73696344,  "disbursed": 0 },
  { "stage": "disbursed",  "leads": 101, "sanctioned": 297589691, "disbursed": 139628850 }
]
```

Counts STUDENTS by lead stage. Show value, not just headcount — a stalled
stage matters as money. Each bar → `drilldown?segment=stage&value=pf_paid`.

### Revenue bridge — booked vs unlockable

```jsonc
"revenue_bridge": {
  "booked": 2014080.10, "unlockable": 2340378.00,
  "undrawn_total": 241245191.00, "drawn_pct": 38.7,
  "files_missing_rate": 2
}
```

**`unlockable` is a FLOOR.** Files whose lender has no rate are excluded,
not zeroed. When `files_missing_rate > 0`, write "at least" before it.

### Biggest opportunities

| Student | Stage | Lender | Sanction | Disbursed | Pending | Potential net revenue |
|---|---|---|---|---|---|---|
| Bhavya Singhal | pf_paid | UC Axis | ₹2,00,00,000 | ₹0 | ₹2,00,00,000 | ₹1,60,000 |
| Esha | disbursed | UC Axis | ₹2,00,00,000 | ₹47,30,000 | ₹1,52,70,000 | ₹1,22,160 |

`potential_net_revenue` **already has the 80% net haircut applied** — do
not multiply again. Rows carry `lead_id`; click through to the student.

---

## 6. Revenue & Collections tab

Uses `dashboard`'s `monthly` and `ageing`, plus the existing
`GET /reconciliation` for the workbench table.

**Trend:** `earned` is by disbursement month, `collected` by receipt
month. **Different dates on purpose** — a tranche earned in June and paid
in August appears in both, in different columns. Do not try to reconcile a
single month's two bars.

Tranches with no date appear in **no month**. Footnote the chart with
`data_quality.tranches_without_date`.

> **A date filter does not clip the month axis.** Filter to "disbursed on
> or before 30 June" and July and August still appear, with **zero earned
> and real collected** — June money that arrived later. The filter chooses
> which tranches; the collected series then plots when their cash actually
> landed. It is correct, and it answers a useful question (how long a
> lender takes to pay), but a June filter showing an August bar looks like
> a bug unless you label it. Suggested caption when a date filter is
> active: *"Collections shown in the month the money arrived, which may
> fall outside the filtered range."*

**Ageing:** buckets `0_30` `31_60` `61_90` `over_90` `no_date`. Only rows
still owing appear.

> **`no_date` is a real bucket holding the MAJORITY of what is
> outstanding** — 56% on this book. Render it, visually distinct, labelled
> "Cannot be aged — no disbursement date". A panel that hides it
> understates the debt by more than half.

`ageing.total_outstanding` can exceed `funnel.outstanding_total` by a few
rupees. Correct, not a bug: ageing sums per-row shortfall which floors at
zero, so an overpaid tranche cannot cancel out what is owed elsewhere.

---

## 7. Lender Performance tab

`GET /api/v1/reconciliation/summary` (existing) for the full matrix, or
`dashboard`'s `by_lender` for the lighter view.

Rows are pre-sorted by outstanding, then size. **Render in the order
given.** Lenders owing nothing still appear — knowing UC Axis is your
biggest route matters in a month it owes nothing.

`share_of_disbursed_pct` gives you Portfolio Mix and Concentration Risk
directly. For "Health", combine `collected_pct` with the lender's share of
ageing — no backend field, it is your call to define.

---

## 8. Source Performance tab

```http
GET /api/v1/reconciliation/sources?<filters>
```

```jsonc
{
  "sources": [
    { "source_id": "...", "source_name": "Ankit DM", "students": 9,
      "tranches": 13, "disbursed_total": 13349780.00,
      "commission_total": 143020.00, "collected_total": 131428.00,
      "revenue_per_student": 15891.11, "collected_pct": 91.9,
      "share_of_disbursed_pct": 8.6 }
  ],
  "unattributed": { "source_name": "Unattributed", "students": 35,
                    "disbursed_total": 60751630.00, ... }
}
```

Two things about this tab:

**`students` counts only students who have actually disbursed.** Not every
lead carrying the source. Counting leads made unattributed read as 8,651
students earning ₹83 each — useless.

**`unattributed` comes back in its own field, deliberately not ranked.**
It is ~40% of disbursement — the single biggest bucket. Putting it top of
a league table of marketing channels would be actively misleading. **Render
it as a footer row, visually separated, never as the winner.** It is also
the single best argument for recording lead sources properly.

Quality Matrix (scale vs drawdown) plots `disbursed_total` against
`collected_pct` from these rows.

---

## 9. Data Control Centre tab

```http
GET /api/v1/reconciliation/exceptions?limit=200&<filters>
```

```jsonc
{
  "total": 156,
  "by_code": { "on_aggregator": 14, "no_sanctioned_amount": 40,
               "no_disbursement_date": 37, "no_receipt_date": 61,
               "materially_short": 4 },
  "items": [ { "severity": "high", "code": "on_aggregator",
               "issue": "File sits on an aggregator",
               "why": "UniCred, Nomad and Axis front several lenders at "
                      "different rates, so they carry no rate of their own...",
               "lead_id": "...", "serial_no": 9465,
               "full_name": "Muskan Sehgal", "bank_name": "Axis",
               "amount": 5000000.00 } ]
}
```

Already sorted: severity, then money at stake. `why` is written to be
shown to a person — put it in the row, not a tooltip. `lead_id` opens the
record.

A record tripping two rules yields two rows, because they are two separate
fixes. That is why `by_code` reconciles exactly with `data_quality`.

### The three "short" counters are not the same thing

From `dashboard.data_quality`:

- `tranches_awaiting_payment` — nothing received (53)
- `tranches_short` — paid, but light (71)
- `tranches_materially_short` — over ₹100 **and** over 2% (4)

**Headline `tranches_materially_short`.** The gap is rounding between the
lender's arithmetic and ours — 67 of the 71 are trivial. A tile reading
"71 lenders underpaid us" is wrong and will be ignored in a week.

---

## 10. Errors and gating

| Situation | Response |
|---|---|
| Not an admin | 403 — hide the whole section from the nav |
| Admitverse tenant | 400 — gate on brand, do not show and fail |
| Bad `segment` or bucket value | 400 with the valid list in `detail` |
| `months` outside 1-24 | 422 |

---

## Acceptance

- [ ] One filter object drives every tab; no filter = whole book
- [ ] Ageing is always measured from today — there is no as-of control
- [ ] Month control is labelled **"Disbursement month"**, not closure
- [ ] No invoiced column anywhere
- [ ] Every segment is clickable and opens the drill-down drawer
- [ ] Drawer header shows **both** counts — "41 students · 46 tranches"
- [ ] Funnel percentages are step-to-step
- [ ] "At least" prefixes `unlockable` when `files_missing_rate > 0`
- [ ] `potential_net_revenue` rendered as given, not re-multiplied
- [ ] Trend plots two series on different dates; undateable count footnoted
- [ ] Ageing shows `no_date`, visually distinct
- [ ] Sources: unattributed is a separated footer row, never ranked
- [ ] Exceptions show `why` in the row; `lead_id` opens the record
- [ ] `tranches_materially_short` is the headline, not `tranches_short`
- [ ] Indian digit grouping throughout
