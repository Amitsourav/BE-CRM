# Commission & Reconciliation — complete frontend brief

> Supersedes the earlier version of this file. The backend changed
> substantially: lender **routes**, the **PF rule**, an **earns-commission**
> flag, and real data imported from FMC's revenue tracker.

---

## 1. What this is

FundMyCampus earns a percentage of what a lender **disburses**. That money
was tracked on a spreadsheet outside the CRM, so nobody could answer the
three questions that find money:

1. Which disbursements were **never billed**?
2. Which bills were **never paid**?
3. Where did the lender **pay less than it owed**?

The backend now records all of it. This is the UI on top.

### FMC's vocabulary — use these exact words on screen

| Term | Means | Formula |
|---|---|---|
| **Gross theoretical revenue** | what we'd earn if every approved loan drew down in full | sanctioned × lender rate |
| **Net theoretical revenue** | the realistic version | gross × 80% |
| **Revenue** | what we've actually earned | disbursed × lender rate |
| **Drawdown gap** | approved money that hasn't converted | gross theoretical − revenue |

**The PF rule:** revenue counts only once a file reaches **PF Paid** or
**Disbursed**. A file at *Sanctioned* contributes **nothing**. The student
paying the processing fee is FMC's confirmation that the loan is real and
which lender won it — before that, a sanction is only an offer.

### Two amounts that must never be confused

| | What it is | Where |
|---|---|---|
| **Sanctioned** | what the lender approved | `lead_banks.loan_amount` |
| **Disbursed** | what actually left the bank | `bank_disbursements.disbursed_amount` |

A ₹30 L sanction can be released as ₹15 L now and ₹15 L next year — two
disbursements, two bills.

> **Every rupee figure you SEND is in lakhs. Every rupee figure you
> RECEIVE is in rupees.** The backend converts. Never multiply.

---

## 2. Screens to build

1. **Reconciliation report** — the main screen (already built; needs the new fields)
2. **Lender rates** — a rate on each lender in the lender admin screen
3. **Stage popovers** — Sanctioned / PF Paid / Disbursed now demand figures
4. **Record payment** — on each reconciliation row
5. **Theoretical revenue** — a small summary panel or tab

---

## 3. Lenders are ROUTES, not banks

This is the thing most likely to confuse. The lender list now contains
entries like:

```
UC Axis                 1.00%     ← Axis, via UniCred
Axis Direct (UC Code)   1.35%     ← Axis, direct
UC PNB                  0.70%
PNB Direct              0.70%
Nomad Normal            1.60%
Nomad US                3.00%     ← same lender, different product
```

Same bank, different route, different rate. **Show the full name.** Do not
"tidy" `Axis Direct (UC Code)` into `Axis` — they are priced differently
and the whole revenue figure depends on the distinction.

**`GET /api/v1/leads/banks/manage`** returns every lender with
`commission_rate` (`null` when unset) and `usage_count`.
**`PATCH /api/v1/leads/banks/{bank_id}`** with `{"commission_rate": 1.35}`
sets it.

> **23 of 27 lenders are priced.** `Axis`, `Nomad`, `SBI` and `UniCred`
> deliberately have **no rate** — plain "Axis" could be UC Axis (1%) or
> Axis Direct (1.35%) and the backend refuses to guess. Show those clearly
> as "route not specified"; ~588 files sit on them and each needs a route
> chosen before it can earn anything.

---

## 4. Stage changes now demand figures

Three stages became gated. All are enforced in the backend on **every**
route in — the Kanban drag, the lead page, and the bank-share grid — so
wire the popover in all of them or those flows will 400.

| Target | Also required |
|---|---|
| **Sanctioned** | `sanctioned_amount_lakh` + `sanction_date` |
| **PF Paid** | `bank_name` + `bank_loan_amount_lakh` |
| **Disbursed** | `disbursed_amount_lakh` + `disbursed_on` |
| `lost` | `lost_reason` (unchanged) |
| everything else | `due_date` (unchanged) |

`POST /api/v1/leads/{lead_id}/stage`

```jsonc
{
  "to_stage": "disbursed",
  "disbursed_amount_lakh": 30,        // LAKHS
  "disbursed_on": "2026-08-14",
  "bank_name": "Axis Direct (UC Code)" // optional — defaults to the lead's lender
}
```

Same fields work on the bank cell:
`PATCH /api/v1/leads/{lead_id}/banks/{entry_id}` with `bank_status` plus
the matching figures.

**Label the disbursed field "Amount released", not "loan amount".** If
someone types the sanctioned figure when only part came out, every
commission downstream is wrong and nothing will catch it.

All of these are **idempotent** — re-saving a file that's already at that
stage doesn't re-ask or duplicate anything.

---

## 5. The reconciliation report

`GET /api/v1/reconciliation` — admin only.

Filters. `bank_name` and `status` are **repeatable** (repeat the param;
brackets are ignored):

| Param | |
|---|---|
| `page`, `page_size` | max 200 |
| `bank_name` | repeatable |
| `status` | repeatable |
| `disbursed_from` / `disbursed_to` | |
| `q` | student name |

```jsonc
{
  "items": [{
    "id": "…", "lead_id": "…", "lead_name": "Ananya Sharma", "serial_no": 1042,
    "bank_name": "Axis Direct (UC Code)", "tranche_no": 1,
    "disbursed_amount": "3000000.00",        // RUPEES
    "disbursed_on": "2026-06-12",
    "commission_rate": "1.35",
    "earns_commission": true,
    "commission_amount": "40500.00",
    "gst_amount": null,
    "invoice_id": null,
    "amount_received": "30000.00",
    "tds_deducted": "3000.00",
    "received_on": "2026-08-20",
    "shortfall": "7500.00",
    "status": "short_paid",
    "days_outstanding": 75,
    "utr_reference": "AXISN123",
    "source": "backfill"
  }],
  "totals": {
    "count": 60, "disbursed_total": "68920820.00",
    "commission_total": "768477.16", "gst_total": "0",
    "received_total": "703212.07", "tds_total": "0",
    "outstanding_total": "65265.09"
  },
  "total": 60, "page": 1, "page_size": 50, "total_pages": 2
}
```

> **`totals` covers the whole filtered set, not the page.** Render it in a
> summary bar and never re-sum the visible rows — with 50 per page that
> would show a fraction of what's owed and make the problem look smaller
> than it is.

### The five statuses

| `status` | Meaning | Colour |
|---|---|---|
| `to_bill` | disbursed, **never invoiced** — question 1 | amber |
| `billed` | invoiced, nothing received | blue |
| `short_paid` | they paid **less than owed** — question 3 | red |
| `paid` | settled | green |
| `written_off` | given up on, out of the outstanding total | grey |

`to_bill` and `short_paid` are the money. Default the view to them.

### Suggested table

```
Student            Lender                 Disbursed   Date      Rate   Commission  Received  Short    Age  Status
Ananya Sharma #1042 Axis Direct (UC Code) ₹30,00,000  12-Jun-26 1.35%  ₹40,500     ₹33,000   ₹7,500   75d  ● short paid
Rahul Verma   #1108 UC PNB                ₹20,00,000  06-Aug-26 0.70%  ₹14,000     —         ₹14,000  20d  ● to bill
```

Indian formatting (₹30,00,000). Emphasise age over 60 days.

### Per-lender view

`GET /api/v1/reconciliation/summary` → one row per lender with both sides:

```jsonc
{
  "bank_name": "UC Axis",
  "files": 13, "disbursed_total": "…", "commission_total": "165147.00",
  "received_total": "…", "tds_total": "…", "outstanding_total": "…",
  "unbilled_count": 13,
  "sanctioned_files": 21, "sanctioned_total": "…",
  "gross_theoretical_revenue": "1331763.00",
  "files_missing_amount": 2
}
```

A lender can appear with sanctioned files and **zero** disbursements —
that's approved money that hasn't converted, and it's the case worth
looking at. Good as a second tab.

---

## 6. Theoretical revenue panel

`GET /api/v1/reconciliation/theoretical`

```jsonc
{
  "files": 110, "files_counted": 74,
  "files_missing_amount": 17, "files_missing_rate": 19,
  "sanctioned_total": "363678104.00",
  "gross_theoretical_revenue": "2969562.00",
  "net_theoretical_factor": "80.00",
  "net_theoretical_revenue": "2375649.00",
  "disbursed_total": "68920820.00",
  "revenue": "768477.16",
  "drawdown_gap": "2201085.00"
}
```

Suggested panel:

```
Gross theoretical    ₹29,69,562     if every sanction drew down in full
Net theoretical      ₹23,75,649     at 80%
Revenue earned        ₹7,68,477     on what actually disbursed
─────────────────────────────────
Drawdown gap         ₹22,01,085     approved, not yet drawn

⚠ 17 files have no sanctioned amount · 19 have no lender rate
   — these are excluded, so the figures above are a floor
```

> **`files_missing_amount` and `files_missing_rate` are not decoration.**
> Files they count are excluded from the sums rather than counted as zero.
> Without them on screen the total reads as complete when it isn't. Make
> them visible and, ideally, clickable through to the offending files.

**The 80% is editable** — `GET`/`PATCH /api/v1/reconciliation/settings`
with `{"net_theoretical_factor": 80}`. It applies to every figure
immediately, historical included: it's an assumption about the future, not
a record of the past.

---

## 7. Recording a payment

`PATCH /api/v1/reconciliation/disbursements/{id}`

```jsonc
{
  "amount_received": 40500,
  "tds_deducted": 4500,
  "received_on": "2026-08-20",
  "payment_reference": "UTR123456"
}
```

> ### TDS is the one to get right
>
> Indian lenders withhold TDS (s.194H) before paying. A ₹45,000 commission
> arrives as **₹40,500 cash + ₹4,500 TDS**. The backend counts **cash +
> TDS** as settled, so the row closes at zero shortfall — correctly.
>
> Enter only the ₹40,500 and the row shows a **₹4,500 shortfall that
> doesn't exist**. Across a few hundred rows the shortfall column becomes
> noise nobody trusts, which defeats the report.
>
> Both fields, side by side, with a live "settles / still short" line.

```
Record payment — Ananya Sharma / Axis Direct (UC Code)
  Commission due            ₹40,500
  Amount received (cash)  [ 36,450  ]
  TDS deducted            [  4,050  ]   ← tax the lender withheld
  ─────────────────────────────────
  Settles                   ₹40,500  ✓ fully paid
  Date received           [ 20-Aug-2026 ]
  Reference               [ UTR123456   ]
```

Other fields on the same endpoint: `disbursed_amount_lakh`,
`disbursed_on`, `commission_rate`, `commission_amount` (overrides the
percentage for a negotiated settlement), `utr_reference`,
`earns_commission`, `write_off_reason`, `notes`.

### The "earns commission" tick box

Some disbursements earn nothing. Every row has `earns_commission`, on by
default. Untick → commission becomes ₹0 while the **rate stays visible**,
so the report can still show what it would have been worth. Re-tick and
the figure comes back.

```
Disbursement ₹3,22,000 @ 0.70%
  [✓] Earns commission   →  ₹2,254
  [ ] Earns commission   →  ₹0
```

Setting an explicit `commission_amount` on an unticked row is refused —
tick it first.

---

## 8. Extra tranches

The **first** disbursement is captured when the file is marked Disbursed.
For a later instalment:

`POST /api/v1/leads/{lead_id}/banks/{entry_id}/disbursements`
```jsonc
{ "disbursed_amount_lakh": 15, "disbursed_on": "2027-07-01" }
```

`GET` the same path lists every tranche. Surface it from the bank cell,
not as a top-level action — only 2 of 2,410 files have more than one
tranche.

---

## 9. Errors

All `400` with a readable `detail`. Show it directly.

| Situation | `detail` |
|---|---|
| Sanctioned without amount/date | `bank_name, sanctioned_amount_lakh (in lakhs) and sanction_date are all required…` |
| Disbursed without amount/date | `bank_name, disbursed_amount_lakh (in lakhs) and disbursed_on are all required…` |
| PF Paid without bank/amount | `bank_name and bank_loan_amount_lakh (in lakhs) are both required…` |
| Lender has no rate | `No commission rate is set for 'X'…` |
| Future date | `Disbursement date cannot be in the future.` |
| Payment before disbursement | `Payment date cannot be before the disbursement it settles.` |
| Amount on an unticked row | `This disbursement is marked as not earning commission…` |
| Deleting a billed row | `This disbursement is on an invoice. Void the invoice first.` |

The "no commission rate" error is the one you'll hit first — link straight
to the lender admin screen from it.

---

## 10. Notes

- **Admin only.** Every `/reconciliation` endpoint is admin-gated; others
  get 403. Hide the section.
- **FMC only.** Admitverse has no lenders; these return 400. Gate on brand.
- **There is real data now** — 112 lender files and 60 tranches imported
  from FMC's revenue tracker, ₹6.89 cr disbursed carrying ₹7.68 L of
  commission. The screen will not be empty.
- **Invoicing isn't built yet.** `invoice_id` is on every row and stays
  `null`, so `to_bill` currently means "not linked to a bill". Build the
  report and payments; billing follows.
- **`gst_amount` is null on every imported row.** It's filled when a bill
  is raised, so the GST total reads 0 until invoicing exists. Don't show
  it as a headline yet.
