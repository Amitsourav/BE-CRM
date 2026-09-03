# Tranches — the panel that has to exist

> For the dev/agent working in the **CRM-UI** repo.
> Supersedes §8 of `COMMISSION_RECONCILIATION_FRONTEND_PROMPT.md`.
> Backend is live; every endpoint below works today.
> Written 2026-09-03 against production data.

---

## Read this first — the earlier brief was wrong

§8 of the commission brief said:

> "Surface it from the bank cell, not as a top-level action — only 2 of
> 2,410 files have more than one tranche."

**That guidance is withdrawn.** It was written before the revenue tracker
was imported, when the CRM had almost no disbursement history. The real
numbers, today:

| Tranches on a file | Files |
|---|---|
| 1 | 83 |
| 2 | **16** |
| 3 | **3** |

Nineteen files are already multi-tranche, and that is the *floor*, not the
ceiling — see below.

---

## Why this is the most valuable screen left to build

An education loan is **sanctioned once and released in instalments**,
typically semester by semester as college fees fall due. A ₹10 L sanction
might come out as ₹2.5 L now, ₹3 L in January, ₹2.5 L in July.

FMC earns commission **per release**, not on the sanction. So every
instalment that is not recorded is commission that is never invoiced.

Across the book right now:

| | |
|---|---|
| Sanctioned (130 live files) | **₹45.88 cr** |
| Drawn so far | ₹14.89 cr |
| **Still to be drawn** | **₹30.99 cr** |
| **Commission riding on it** | **₹31,38,986** |

**Only 32% of approved money has actually been released.** ₹31 lakh of
commission is already earned in principle and will land over the coming
semesters. It needs recording as it arrives.

If the team cannot add an instalment from the CRM, they will keep doing it
in the spreadsheet — and the two records drift apart again. That drift is
what cost a full day on 2026-09-03 reconciling a ₹1.62 crore gap.

---

## 1. Tranche list — on the lender file

Every (lead, lender) file needs its instalment history visible. Not buried
in a cell hover; a panel on the lead page, under the lender.

```http
GET /api/v1/leads/{lead_id}/banks/{entry_id}/disbursements
```

`entry_id` is the **lender file** (the `lead_banks` row), not the lead.
Returns every tranche in `tranche_no` order.

Real example — Sayan Nandy / GyanDhan, sanctioned ₹31,00,000:

| # | Amount | Date | Commission | Received |
|---|---|---|---|---|
| 1 | ₹13,36,183 | 01 Feb 2026 | ₹13,362 | ₹15,499 |
| 2 | ₹6,50,000 | 01 May 2026 | ₹6,500 | ₹7,540 |
| 3 | ₹7,50,000 | 01 May 2026 | ₹7,500 | ₹8,700 |

Each row carries: `tranche_no`, `disbursed_amount`, `disbursed_on`,
`commission_rate`, `commission_amount`, `gst_amount`, `amount_received`,
`tds_deducted`, `received_on`, `utr_reference`, `earns_commission`,
`status`, `shortfall`.

`disbursed_on` can be **null** on historical rows — render "—", never
today's date. Those rows still count for money; they are only excluded
from ageing.

---

## 2. Drawdown progress — the number people actually want

Show it above the list, per lender file:

```
GyanDhan — sanctioned ₹31,00,000
████████████████████░░░  88% drawn
drawn ₹27,36,183   ·   still to come ₹3,63,817
```

Compute from what you already have: `sanctioned` is `loan_amount` on the
lender file, `drawn` is the sum of the tranche list, remainder is the
difference. Do not let it go below zero.

This is the single most useful thing on the page — it tells a counsellor
whether there is more money coming and how much commission is still to be
earned on that student.

---

## 3. Add-tranche button

Sits on the lender file, next to the progress bar. Enabled once the file
is at `disbursed`.

```http
POST /api/v1/leads/{lead_id}/banks/{entry_id}/disbursements
{
  "disbursed_amount_lakh": 3,
  "disbursed_on": "2026-12-15",
  "utr_reference": "optional — the lender's payout reference",
  "notes": "optional"
}
```

- **`disbursed_amount_lakh` is in LAKHS**, like every loan figure in this
  CRM. 3 means ₹3,00,000. Label the field "in lakhs" — this is exactly
  the confusion that produced the wrong figures we spent a day fixing.
- `tranche_no` is assigned by the server. Never send it.
- Returns the created tranche (201). Append it and refresh the progress bar.
- **Admin only.** Hide the button for non-admins rather than letting it 403.

### Warn before submitting

The backend accepts what it is given. The UI is the only place these get
caught, and both happened for real:

- **amount > remaining to draw** — a lender cannot release more than it
  approved. Warn plainly: *"₹X exceeds the ₹Y still to be drawn on this
  sanction. Is this the amount the bank actually released?"*
- **amount == the full sanction on tranche 1** — legitimate for a single
  full drawdown, but it is also exactly what someone typing the loan size
  by mistake produces. Ask for confirmation.

**The field means "what the bank actually paid out", never the sanction.**
Put that in the helper text.

---

## 4. Editing and deleting a tranche

```http
GET    /api/v1/reconciliation/disbursements/{id}
PATCH  /api/v1/reconciliation/disbursements/{id}
DELETE /api/v1/reconciliation/disbursements/{id}
```

`PATCH` accepts `disbursed_amount_lakh`, `disbursed_on`, `utr_reference`,
`commission_rate`, `commission_amount`, `gst_amount`, `earns_commission`,
`amount_received`, `tds_deducted`, `received_on`, `payment_reference`,
`write_off_reason`, `notes`. Changing the amount or rate recomputes
commission server-side; sending `commission_amount` sets it directly, for
when a lender settles at an agreed figure.

`extra: forbid` — an unknown field is a 422, so don't send the whole row back.

---

## 5. What changed on the backend since the last brief

- **`/reconciliation/summary` rows now include `gst_total`**, and their
  `outstanding_total` now uses `(commission + gst) − (received + tds)` —
  the same formula as `/reconciliation`. Previously it omitted GST and the
  two endpoints disagreed by ₹3,03,763.
- **New lenders**: UniCred and Nomad are *aggregators* with sub-products at
  different rates. Added: `UniCred Normal` 1.00%, `UniCred Credila Domestic`
  0.75%, `UniCred Propelld Connector` 1.10%, `UniCred Edgro Connector`
  0.75%, `Nomad Axis Domestic` 1.00%, `Nomad Govt` 0.70%, `Nomad Prodigy`
  0.75%, `IDFC Domestic` 0.50%. **Always fetch the list from
  `GET /leads/banks`; never hard-code.**
- Bare `UniCred`, `Nomad` and `Axis` still carry **no rate** — an
  aggregator cannot have one. A file left on a bare name can never earn
  commission, so flag it rather than showing ₹0.

---

## 6. One rule from the last bug

A headline figure must state what it covers. The reconciliation page
previously opened with a hidden status filter and showed a total for a
subset with nothing on screen saying so; it read as a wrong number for a
day. Same rule here: **if the tranche list or progress bar is ever
filtered or partial, say so on screen.**

---

## Acceptance

- [ ] Lender file on the lead page shows its tranche list, in order
- [ ] Progress bar: sanctioned, drawn, still-to-come, % drawn
- [ ] Add-tranche button on a `disbursed` file; admin only, hidden otherwise
- [ ] Amount field labelled "in lakhs", helper text says "what the bank released, not the sanction"
- [ ] Warn on amount > remaining; confirm on amount == full sanction
- [ ] New tranche appears in the list and the progress bar updates without a reload
- [ ] Null `disbursed_on` renders "—", not today's date
- [ ] Edit and delete work on an existing tranche
- [ ] Indian number format throughout (₹27,36,183)
