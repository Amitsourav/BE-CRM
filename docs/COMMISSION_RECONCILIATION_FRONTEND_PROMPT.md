# Commission reconciliation — frontend

## What this is for

FundMyCampus earns a percentage of what a lender **disburses**. Until now
that money was tracked on a spreadsheet outside the CRM, so nobody could
answer the three questions that actually find money:

1. Which disbursements were **never billed**?
2. Which bills were **never paid**?
3. Where did the lender **pay less than it owed**?

The backend now records every disbursement and works out the commission.
This document covers the screens that sit on top.

**Four things to build:**

1. A **commission rate** field on each lender in the lender admin screen.
2. An **amount + date** prompt when a file is marked *Disbursed*.
3. The **reconciliation report** — the main screen.
4. A **record payment** action on each row.

---

## The one concept to get right

There are two different amounts on a lead's lender file, and confusing
them makes every number wrong:

| | What it is | Where it lives |
|---|---|---|
| **Sanctioned** | what the lender approved | `lead_banks.loan_amount` |
| **Disbursed** | what actually left the bank | `bank_disbursements.disbursed_amount` |

**Commission is earned on the disbursed amount, never the sanctioned
one.** A lender can sanction ₹30 L and release it as ₹15 L now and
₹15 L next year — that is two disbursements and two bills.

Every rupee figure you send is in **lakhs**. Every rupee figure you
receive is in **rupees**. The backend converts. Do not multiply.

---

## 1. Commission rate on each lender

**Endpoint:** `PATCH /api/v1/leads/banks/{bank_id}` (existing, admin-only)
**Body:** `{"commission_rate": 1.5}`

`GET /api/v1/leads/banks/manage` now returns `commission_rate` on each
lender — `null` when none is set.

Add a percentage input to the lender admin list. Show `—` for `null`, and
**flag it**: a lender with no rate cannot have commission calculated, and
marking a file disbursed against it will fail with a clear error. Getting
all ~20 rates entered is the first thing that has to happen.

> Changing a rate only affects **future** disbursements. Every existing
> one stored the rate that applied to it, so renegotiating a lender never
> rewrites what was already earned. Say so in the UI — it is the question
> an admin will ask before touching the field.

---

## 2. Marking a file Disbursed now asks for the money

This is a **breaking change** to two existing flows. Both now reject the
request until you send the new fields.

### a. Lead stage → Disbursed

`POST /api/v1/leads/{lead_id}/stage`

```jsonc
{
  "to_stage": "disbursed",
  "disbursed_amount_lakh": 30,        // required, in LAKHS
  "disbursed_on": "2026-08-14",       // required
  "bank_name": "Axis"                 // optional — see below
}
```

`bank_name` may be **omitted**: the backend uses the lead's primary
lender, which the CRM already knows by the time a file disburses. Send it
only when the user picks a different one.

Disbursed is terminal, so no follow-up date is needed.

### b. Bank cell → Disbursed (bank share grid)

`PATCH /api/v1/leads/{lead_id}/banks/{entry_id}`

```jsonc
{
  "bank_status": "disbursed",
  "disbursed_amount_lakh": 30,
  "disbursed_on": "2026-08-14",
  "utr_reference": "AXISN12345678"    // optional
}
```

### The popover

```
Mark as Disbursed
  Amount released (lakh) [ 30          ]   <- NOT the sanctioned amount
  Date released          [ 14-Aug-2026 ]
  Lender                 [ v Axis      ]   <- prefilled, editable
  Bank reference         [             ]   optional
                            [Cancel] [Save]
```

Label it **"Amount released"**, not "loan amount". If the user types the
sanctioned figure when only part came out, the commission is wrong and
nothing downstream will catch it.

**Both are idempotent** — saving a file that is already disbursed does
not create a second tranche and does not re-ask for the figures.

---

## 3. The reconciliation report

**Endpoint:** `GET /api/v1/reconciliation` (admin-only)

Query params — `bank_name` and `status` are **repeatable**, same
convention as the bank share grid (repeat the param, don't use brackets):

| Param | Notes |
|---|---|
| `page`, `page_size` | max 200 |
| `bank_name` | repeatable |
| `status` | repeatable — see below |
| `disbursed_from` / `disbursed_to` | on the disbursement date |
| `q` | student name |

### Response

```jsonc
{
  "items": [
    {
      "id": "…",
      "lead_id": "…", "lead_name": "Ananya Sharma", "serial_no": 1042,
      "bank_name": "Axis", "tranche_no": 1,
      "disbursed_amount": "3000000.00",     // RUPEES
      "disbursed_on": "2026-06-12",
      "commission_rate": "1.50",
      "commission_amount": "45000.00",
      "gst_amount": null,
      "invoice_id": null,
      "amount_received": "30000.00",
      "tds_deducted": "3000.00",
      "received_on": "2026-08-20",
      "shortfall": "12000.00",
      "status": "short_paid",
      "days_outstanding": 75,
      "utr_reference": "AXISN123", "source": "stage_machine"
    }
  ],
  "totals": {
    "count": 2,
    "disbursed_total": "5000000.00",
    "commission_total": "75000.00",
    "gst_total": "0",
    "received_total": "30000.00",
    "tds_total": "3000.00",
    "outstanding_total": "42000.00"
  },
  "total": 2, "page": 1, "page_size": 50, "total_pages": 1
}
```

> **`totals` covers the whole filtered set, not the page.** Render it in a
> summary bar above the table and never re-sum the rows yourself — with
> 50 rows per page that would show a fraction of what is owed and read as
> a much smaller problem than it is.

### The five statuses

| `status` | Meaning | Suggested colour |
|---|---|---|
| `to_bill` | Disbursed, **never invoiced** — question 1 | amber |
| `billed` | Invoiced, nothing received yet | blue |
| `short_paid` | They paid **less than owed** — question 3 | red |
| `paid` | Settled | green |
| `written_off` | Given up on, excluded from chasing | grey |

`to_bill` and `short_paid` are the money. Make them the default filter or
at minimum the first thing on screen.

### Suggested layout

A summary bar (outstanding total, biggest number on the page), status
filter chips with counts, lender filter, then the table:

```
Student            Lender  Disbursed   Date      Rate   Commission  Received  Short     Age   Status
Ananya Sharma #1042 Axis   ₹30,00,000  12-Jun-26 1.50%  ₹45,000     ₹33,000   ₹12,000   75d   ● short paid
Rahul Verma   #1108 PNB    ₹20,00,000  06-Aug-26 0.75%  ₹15,000     —         ₹15,000   20d   ● to bill
```

Format rupees Indian-style (₹30,00,000). Age above 60 days deserves
emphasis.

### Per-lender summary

`GET /api/v1/reconciliation/summary` → one row per lender with `files`,
`disbursed_total`, `commission_total`, `received_total`, `tds_total`,
`outstanding_total`, `unbilled_count`. This is the "who do we chase this
month" view — a good second tab.

---

## 4. Recording a payment

**Endpoint:** `PATCH /api/v1/reconciliation/disbursements/{id}`

```jsonc
{
  "amount_received": 40500,
  "tds_deducted": 4500,
  "received_on": "2026-08-20",
  "payment_reference": "UTR123456"
}
```

> ### TDS is not optional, and this is the important bit
>
> Indian lenders withhold TDS (section 194H) before paying commission.
> A ₹45,000 commission typically arrives as **₹40,500 cash + ₹4,500 TDS**.
>
> The backend treats **cash + TDS** as the amount settled, so that row
> closes at zero shortfall — correctly.
>
> If the user enters only the ₹40,500 and leaves TDS blank, the row shows
> a **₹4,500 shortfall that does not exist**. Do that across a few hundred
> rows and the shortfall column becomes noise nobody trusts, which defeats
> the entire purpose of the report.
>
> Put both fields in the payment form, side by side, with TDS explained.
> Consider defaulting TDS to `commission − cash` once cash is entered.

```
Record payment — Ananya Sharma / Axis
  Commission due            ₹45,000
  Amount received (cash)  [ 40,500  ]
  TDS deducted            [  4,500  ]   ← tax the lender withheld
  ------------------------------------
  Settles                   ₹45,000  ✓ fully paid
  Date received           [ 20-Aug-2026 ]
  Reference               [ UTR123456   ]
                              [Cancel] [Save]
```

Show the running "settles / still short" line live as they type. That one
line is what stops wrong entries.

Other fields on the same endpoint: `disbursed_amount_lakh`,
`disbursed_on`, `commission_rate`, `commission_amount` (overrides the
percentage for a negotiated settlement), `utr_reference`,
`write_off_reason`, `notes`.

**Write-off** = stop chasing. Sets status to `written_off` and drops the
row out of the outstanding total. Needs a reason.

---

## 5. Extra tranches

The **first** disbursement is captured automatically when the file is
marked disbursed. When a lender releases a second instalment later:

`POST /api/v1/leads/{lead_id}/banks/{entry_id}/disbursements`

```jsonc
{ "disbursed_amount_lakh": 15, "disbursed_on": "2027-07-01" }
```

`tranche_no` is assigned automatically. `GET` the same path lists every
tranche on that file. Surface this from the bank cell — "add another
disbursement" — rather than as a top-level action.

Most loans come out in one go (only 2 of 2,410 files have more than one
tranche), so keep this out of the main flow.

---

## 6. Errors

All `400` with a readable `detail` — show it directly.

| Situation | `detail` |
|---|---|
| Disbursed without amount or date | `bank_name, disbursed_amount_lakh (in lakhs) and disbursed_on are all required when moving a lead to 'disbursed' …` |
| Lender has no rate configured | `No commission rate is set for 'X', so the commission cannot be worked out…` |
| Future date | `Disbursement date cannot be in the future.` |
| Payment dated before disbursement | `Payment date cannot be before the disbursement it settles.` |
| Deleting a billed row | `This disbursement is on an invoice. Void the invoice before deleting it.` |

The "no commission rate" error is the one you will hit first — it means
the lender rates from section 1 haven't been entered yet. Link straight to
the lender admin screen from it.

---

## 7. Notes

- **Admin-only.** Every endpoint here is `get_current_admin`; non-admins
  get 403. Hide the whole section for other roles.
- **FMC only.** Admitverse has no lenders; these endpoints return a 400
  saying so. Gate on brand.
- **Not built yet:** creating an invoice from a set of disbursements.
  `invoice_id` is on every row and stays `null` until that lands, so
  `to_bill` currently means "not yet linked to a bill". Build the report
  and payments first; billing follows.
- **History is thin.** 78 leads reached `disbursed` before this existed,
  and none of them has a disbursement date, so they will not appear in
  the report until they are entered by hand. Expect the report to look
  near-empty at first and fill up as new files disburse. That is correct
  behaviour, not a bug.
