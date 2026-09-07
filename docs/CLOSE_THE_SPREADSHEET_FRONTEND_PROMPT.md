# Closing the spreadsheet — frontend build brief

> For the dev/agent working in the **CRM-UI** repo.
> Written 2026-09-07. Every endpoint and field below is **live in
> production** and verified against real FMC data.
>
> This document covers only what is **new since 2026-09-05**. The six-tab
> dashboard itself is specified in
> `RECONCILIATION_DASHBOARD_FRONTEND_PROMPT.md` — build that first, then
> layer this on top. Tranche entry is in `TRANCHES_FRONTEND_PROMPT.md`.

---

## 1. Why this exists

FMC has run its loan book on a spreadsheet — `FMC_Revenue Tracker.xlsx`,
25 tabs — and is closing it. The backend now holds everything the sheet
held, and the CRM and the sheet agree **to the paisa** on every measure
both track.

**But the UI does not yet expose the last mile.** Until the screens below
exist, someone still has to open Excel. That is the entire job.

Five things the sheet does that the UI cannot yet:

| The sheet's job | What the UI needs |
|---|---|
| Track commission paid AWAY to connectors | §3 Payout sharing |
| Say what FMC actually KEEPS | §4 Net revenue |
| Forecast by expected closure month | §5 Forecast tab |
| Raise a GST invoice per release | §6 Invoicing |
| Hold lender billing details + DSA codes | §7 Lender admin |

Plus §8, which is not new but is now **enforced by the API** and will
break the existing lead form if ignored.

---

## 2. Vocabulary — use these exact words on screen

Extends the table in the dashboard brief.

| Term | Means | Never call it |
|---|---|---|
| **Earned** | Commission **+ GST** on what was disbursed | "revenue" |
| **Commission** | Commission only, **no GST** | "earned" |
| **Shared away** | Commission paid to whoever supplied the lead | "expense" |
| **Kept** | Commission − shared away. **THE revenue figure** | "profit" |
| **Closure month** | The month a deal is **expected** to close. A target | "closed date" |
| **Estimated date** | A disbursement date that was derived, not recorded | — |

> **Rule:** wherever you show a revenue number without qualification, show
> **kept** (`funnel.net_commission_total`). Every other figure is gross.
> FMC's dashboards showed gross for months and nobody realised.

> **Every amount in every response is RUPEES.** Indian grouping —
> ₹15,46,98,551, never ₹154,698,551.

---

## 3. Payout sharing — on the lender file

A lead from an outside connector (Altera, MBA Aspire, arman, Nikhil Le
Edu, a plain referral) costs a share of the commission. It lives on the
**lender file**, not the tranche, because it is agreed on the deal and
computed off the SANCTION while commission accrues per release.

### 3a. Fields — `GET/PATCH /api/v1/leads/{lead_id}/banks/{entry_id}`

```jsonc
{
  "payout_to":   "Altera",      // free text, max 100. Who gets the share
  "payout_due":  "10080.00",    // rupees, owed on the whole deal
  "payout_paid": "10080.00"     // rupees, what has actually gone out
}
```

All three are on `LeadBankOut` (read) and `LeadBankUpdate` (write).

### 3b. Where it goes on screen

On the **Sanction Details** card of the lender file, a new block:

```
┌─ Connector payout ─────────────────────────────────┐
│  Paid to   [ Altera                              ] │
│  Owed      [ ₹10,080          ]                    │
│  Paid      [ ₹10,080          ]   Outstanding ₹0   │
└────────────────────────────────────────────────────┘
```

`Outstanding` is derived client-side: `max(payout_due − payout_paid, 0)`.
Show it in red when > 0.

### 3c. Rules you must honour

1. **An amount without a name is refused** — the API returns 400
   *"Say who the payout goes to."* Make `payout_to` required as soon as
   either amount is filled.
2. **`payout_due` MAY exceed the commission earned so far.** Do NOT warn
   about it. The payout is owed on the full sanction while only part of
   the loan has been drawn — Aftar is owed ₹4,870 against ₹3,185 earned,
   and that is correct.
3. **Two amounts, not one.** The old spreadsheet kept a single column and
   it disagreed with itself on exactly the part-paid rows. Never collapse
   these into one field.

---

## 4. Net revenue — the number that changes

### 4a. `GET /api/v1/reconciliation/dashboard` → `funnel`

Six **new** fields alongside the existing ones:

```jsonc
{
  "earned_total":              "2022014.50",  // commission + GST (existing)
  "commission_total":          "1713571.63",  // NEW — commission only
  "payout_due_total":            "55062.00",  // NEW — shared away
  "payout_paid_total":           "50845.00",  // NEW
  "payout_outstanding_total":     "4217.00",  // NEW — still owed to partners
  "net_commission_total":      "1658509.63",  // NEW — WHAT FMC KEEPS
  "sanctioned_files_unpriced":          15,   // NEW — see 4c
  "confirmed_files_unpriced":            3    // NEW
}
```

### 4b. Overview tab — replace the headline

The Overview currently leads with earned. **Lead with kept instead:**

```
┌────────────────────────────────────────────────────────────┐
│  YOU KEEP                                    ₹16,58,510    │
│  ─────────────────────────────────────────────────────────  │
│  Commission billed          ₹17,13,572                     │
│  Shared with partners     − ₹55,062   (₹4,217 still to pay)│
│  GST (not yours)            ₹3,08,443                      │
└────────────────────────────────────────────────────────────┘
```

GST is shown but visually de-emphasised — it is collected for the
government and was never FMC's.

### 4c. `sanctioned_files_unpriced` — show it or the average lies

`sanctioned_files` counts every live file; `sanctioned_total` can only
sum the ones carrying an amount. Render as:

> **149 files · ₹48,53,16,918**  ·  *15 carry no amount*

Without the second line, "average sanction per file" reads low and nobody
can tell why.

### 4d. `GET /api/v1/reconciliation/pipeline` → `revenue_bridge`

```jsonc
{
  "booked":      "1713571.63",  // commission, EX-GST
  "booked_gst":   "308442.87",  // booked + booked_gst = funnel.earned_total
  "shared_away":   "55062.00",  // NEW
  "kept":        "1658509.63",  // NEW
  "unlockable":  "2340377.88",  // future commission, gross
  "unlockable_net": "1872302.30", // × 0.80 — same basis as opportunity rows
  "files_missing_rate":      17,
  "files_missing_sanction":   3   // NEW
}
```

Render the bridge as: **Kept ₹16.6 L → at least ₹18.7 L more to come**,
with a footnote naming `files_missing_rate + files_missing_sanction` as
"excluded, not zero".

---

## 5. Forecast tab — `GET /api/v1/reconciliation/expected-months`

**This is a new tab.** It is the only forward-looking screen in the CRM;
everything else reports history.

```jsonc
{
  "months": [
    { "month": "2026-06-01", "students": 33,
      "sanctioned": "136476793.00", "disbursed": "37713589.00",
      "commission": "434290.15",  "pending": "98763204.00",
      "is_past": true }
  ],
  "students_without_month": 37,
  "slipped_months": 7,
  "slipped_pending": "277560677.00"
}
```

### 5a. What it must communicate

Every FMC month has already passed with **₹27.76 crore approved and not
drawn**. That is the headline, and it should be uncomfortable.

```
   SLIPPED — 7 months gone, ₹27.76 cr still not drawn

   Month      Students   Sanctioned    Drawn      Still pending
   Jun 2026      33       ₹13.65 cr   ₹3.77 cr    ₹9.88 cr  ▲
   May 2026      22        ₹8.68 cr   ₹2.17 cr    ₹6.51 cr
   Apr 2026      17        ₹5.32 cr   ₹1.78 cr    ₹3.54 cr
   ...
   37 students have no closure month — not in this forecast
```

### 5b. Rules

- `month` is a **date pinned to the 1st**. Render as `Jun 2026`, **never
  as a date**. It is a month, not a day.
- `is_past: true` **and** `pending > 0` → highlight the row. That is a
  target that slipped, and it is the only actionable thing here.
- `students_without_month` must be visible on the panel. A forecast that
  silently omits 37 live deals reads low for no reason.
- Rows count **students, not files** — one student with three lender
  files is one deal.

### 5c. Capturing the field

`expected_closure_month` is on `LeadCreate`, `LeadUpdate` and `LeadOut`.

- A **month picker**, not a date picker.
- Send any date within the month; the backend pins it to the 1st.
- Editable at **any stage** — it is a revisable target.
- Send `null` to clear.

> ⚠️ **Never use this field to date anything.** It is what someone
> *expected*, not what happened. Using it as a disbursement date once put
> a tranche in March that had not happened yet.

---

## 6. Invoicing — one bill per release

`status = "billed"` has never been reachable from the UI. All 126 of
FMC's tranches read `to_bill` regardless of what has actually been sent,
which is why the whole to_bill/billed distinction currently means nothing.

### 6a. Raise

```
POST /api/v1/reconciliation/disbursements/{disbursement_id}/invoice
     ?invoice_date=2026-09-07        // optional, defaults today
```

```jsonc
{
  "invoice_id": "…", "invoice_number": "FMC/2026-27/024",
  "invoice_date": "2026-09-07",
  "customer_name": "Senbonzakura Consultancy Private Limited",
  "subtotal": "36727.95", "total_tax": "6611.03",
  "grand_total": "43338.98",
  "pdf_url": "https://…"          // may be null if the PDF failed
}
```

### 6b. Unlink

```
DELETE /api/v1/reconciliation/disbursements/{disbursement_id}/invoice
```

**Draft invoices only.** An issued GST number is burned — sequences must
be gapless — so an issued bill is voided, never unpicked. The API returns
400 naming the status.

### 6c. UI

An **Invoice** button on each tranche row in the reconciliation table.

- `status = to_bill` → button enabled
- `status = billed` → show the invoice number as a link to its PDF, plus
  an **Unlink** action (draft only)
- written off, or commission 0 → button hidden

### 6d. Refusals — surface the message verbatim

| Cause | Message shape |
|---|---|
| Already invoiced | *"This tranche is already on an invoice…"* |
| Written off | *"…written off, so there is nothing to bill."* |
| Earns nothing | *"…earns no commission…"* |
| **Lender has no GSTIN** | *"'BOI' cannot be invoiced yet — no GSTIN on file…"* |

The last one is the common case: **5 lenders still have no GSTIN** (BOI,
PNB, PNB Direct, UBI, IDFC — 6% of the book). Link the message straight
to that lender's admin row (§7). Do not paraphrase — the message names
the missing field.

> The backend **refuses rather than guesses**. A lender's state code
> decides CGST+SGST vs IGST; inventing it would put the wrong tax on a
> legal document.

---

## 7. Lender admin — `GET/POST/PATCH /api/v1/leads/banks`

`BankOut` gained six fields:

```jsonc
{
  "partner_code":   "PCSLDSA9229",   // DSA code the lender issued FMC
  "gstin":          "27AAJCK4102D1ZY",
  "state_code":     "27",            // usually derived from the GSTIN
  "billing_name":   "Kuhoo Finance Private Limited",  // LEGAL name
  "billing_address":"7th Floor, Gufic Building, …",
  "billing_email":  "…"
}
```

### 7a. Why `billing_name` is separate from `name`

The legal entity is often not the name FMC uses:

| FMC calls it | Bills as |
|---|---|
| GyanDhan | **Senbonzakura Consultancy Private Limited** |
| Propelld | **Bluebear Technology Private Limited** |
| UC Axis / UC PNB / Axis Direct | **UniCreds Private Limited** |
| Nomad Normal / Nomad Axis | **Nomad Study Abroad Consultant Pvt Ltd** |

Show `name` in dropdowns, `billing_name` on invoices.

### 7b. The lender table needs a "can invoice" column

```
Lender              Rate    DSA code       GSTIN               Invoiceable
Kuhoo               0.80%   —              27AAJCK4102D1ZY     ✓
BOI                 0.30%   BOISL/DEL/…    —                   ✗ add GSTIN
UniCred             —       —              27AACCU6987B1Z2     ✗ aggregator
```

- No GSTIN → ✗ with a direct edit affordance
- `is_aggregator: true` → ✗ *"aggregator — move files to a real route"*

### 7c. `POST /leads/banks` was broken until 2026-09-07

The route read `is_aggregator` from a body that never carried it, so
adding **any** lender was a 500. Fixed — but confirm your create form
sends `is_aggregator` (bool) and, optionally, the six fields above.

---

## 8. ⚠️ Lead source is now MANDATORY — this will break your form

`POST /api/v1/leads` **rejects a lead with no `lead_source_id`**:

> *"A lead source is required. Pick where this lead came from — without
> it the lead is invisible to every channel report."*

An unknown `lead_source_id` is also rejected (it used to accept another
tenant's id).

**Required changes:**

1. Source becomes a **required field** on the create form, validated
   client-side before submit.
2. **CSV import**: the file must carry a `source` column, or the user must
   pick one from the dropdown. A row with neither **fails with its row
   number** rather than importing unattributed.
3. **CSV import now REFUSES files over 5,000 rows** rather than silently
   truncating. Surface the message — it tells the user to split the file.
   (A 19,688-row upload once imported 5,000 and reported success.)

Webhook paths — WhatsApp, Meta, website forms — are unaffected; they name
their own channel server-side.

---

## 9. Estimated dates — say so on screen

`DisbursementOut` gained `disbursed_on_estimated: bool`.

**36 of FMC's 126 tranches carry a derived date**, recovered from invoice
dates, receipt dates or a stated month. They age and appear in monthly
totals exactly like real ones.

- Render an estimated date with a marker: `30-Jun-2026 ~`
- Tooltip: *"Estimated — the exact date was never recorded. Replace it
  when the lender confirms."*
- `data_quality.tranches_with_estimated_date` gives the count; show it in
  the Data Control Centre.

> A figure that looks exact and is not is worse than a blank. This flag is
> the only thing keeping the recovery honest.

---

## 10. Data Control Centre — new counters and codes

`data_quality` gained `tranches_with_estimated_date`.

The exception register (`GET /reconciliation/exceptions`) gained two
codes, and **both are drillable** via
`GET /reconciliation/drilldown?segment=exception&value=<code>`:

| Code | Severity | Means |
|---|---|---|
| `estimated_disbursement_date` | low | Date derived, not recorded |
| `money_on_prestage` | high | Money disbursed, lead still at an early stage |

The drill-down also now accepts `segment=exception` (it did not before)
and `segment=stage&value=other` (which used to return a raw 500).

> **Counts differ by design.** `on_aggregator` and `no_sanctioned_amount`
> count **files**; the rest count **tranches**; drill-down returns
> **students** plus `tranche_total`. Label each so they do not look like a
> contradiction.

---

## 11. Lender table — one field was never rendered

`LenderDebtRow.share_of_disbursed_pct` has been computed since day one and
was missing from the response schema, so it never reached the UI. It is
there now — build the **concentration view** on it.

FMC's top three lenders are ~48% of the book. That is worth showing.

---

---

## 11b. With GST and without — every money panel now carries both

Added 2026-09-07. `funnel` and `sources` already split GST; **`by_lender`
and `monthly` did not**, so the Lender Performance and Revenue tabs could
only ever draw one basis and could not be compared against the others.

Both now return the split:

```jsonc
// LenderDebtRow
{ "earned_total":     "344812.00",   // commission + GST — what the lender is BILLED
  "commission_total": "292214.00",   // NEW — what FMC earns
  "gst_total":         "52598.00" }  // NEW — collected for the government

// MonthPoint
{ "earned":     "455868.00",
  "commission": "386329.00",   // NEW
  "gst":         "69539.00" }  // NEW
```

`commission + gst == earned` on every row, and the column totals tie to
`funnel.commission_total`.

### What to build

A **single toggle at the top of the page**, not per-widget:

```
   Show amounts:  ( ) Including GST     (•) Excluding GST
```

- **Excluding GST is the default.** GST is not FMC's money.
- The toggle switches `earned_total`/`earned` for
  `commission_total`/`commission` everywhere on the page at once.
- **`collected` and `outstanding` never change** — cash arrives with GST
  in it and the debt is the GST-inclusive figure. Label those columns
  "incl. GST" so the difference is deliberate, not confusing.
- Show the GST column itself when "Including GST" is selected, so the
  three numbers visibly add up.

**Do not put a toggle on each card.** Two cards on the same screen showing
different bases is exactly how "revenue" came to mean two different things
in this system.

## 12. Errors and gating

| Situation | Response |
|---|---|
| Non-admin | 403 |
| Admitverse tenant | 400 — these routes are FMC-only |
| Unknown drill-down segment/value | 400 naming the valid ones |
| Lender not invoiceable | 400 naming the missing field |
| Payout amount with no `payout_to` | 400 |
| Lead with no source | 400 |
| CSV over 5,000 rows | 400 — nothing imported |

All money is a **string** in JSON (Decimal serialisation). Parse with a
decimal library, never `parseFloat`, before summing.

---

## Acceptance

- [ ] Overview leads with **kept**, not earned; GST de-emphasised
- [ ] `sanctioned_files_unpriced` shown beside the file count
- [ ] Payout block on the lender file; `payout_to` required when an amount
      is entered; no warning when `payout_due` exceeds commission
- [ ] Forecast tab renders months as **Jun 2026**, highlights slipped
      rows, shows `students_without_month`
- [ ] `expected_closure_month` is a **month picker**, editable at any stage
- [ ] Invoice button on tranche rows; refusal messages shown verbatim and
      linked to lender admin
- [ ] Lender table shows DSA code, GSTIN and an invoiceable flag
- [ ] Lead create form **requires** a source; CSV import surfaces the
      row-level and >5,000-row failures
- [ ] Estimated dates visually marked wherever a disbursement date appears
- [ ] `segment=exception` and `segment=stage&value=other` drill-downs work
- [ ] Concentration view built on `share_of_disbursed_pct`
- [ ] ONE page-level GST toggle, defaulting to **excluding**;
      collected/outstanding stay GST-inclusive and are labelled so

When every box is ticked, nothing in the tracker is unavailable in the
CRM, and the spreadsheet can be closed.
