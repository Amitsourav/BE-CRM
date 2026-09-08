# Invoicing — frontend build brief

> For the dev/agent working in the **CRM-UI** repo.
> Written 2026-09-08. **Every endpoint below is live in production** and
> verified against real FMC data.
>
> Background, and why the design is the way it is:
> `INVOICE_COMMISSION_LINK_RESEARCH.md`.

---

## 1. What this is

FMC bills lenders for commission. Until now the CRM could store an
invoice and nothing else — so **every invoice read "unpaid" while ₹13.1
lakh had demonstrably arrived**, and ₹17.2 lakh of earned commission sat
un-billed with no screen that showed it.

The backend now does all of it. **None of it is reachable by a human.**
That is this job.

Three screens:

| | Screen | The number it exposes |
|---|---|---|
| **A** | Ready to bill | **₹17,22,772** never invoiced |
| **B** | Invoice list | what each bill is owed |
| **C** | Record payment | closes the loop |

Build **A first** — it is the one with money in it.

---

## 2. Vocabulary

| Term | Means |
|---|---|
| **Release** | One drawdown of a loan. FMC earns commission per release |
| **Invoice** | One bill to a lender, covering **one or more** releases |
| **Billed** | Commission + GST on the releases this invoice covers |
| **Received** | Cash + TDS. **Both** discharge the debt |
| **Outstanding** | Billed − received, floored at zero, per release |

> **All money is a JSON STRING** (Decimal). Parse with a decimal library,
> never `parseFloat` — it loses paise on the larger figures. Render with
> Indian grouping: ₹17,22,772, never ₹1,722,772.

> ⚠️ **`grand_total` is the invoice's own face value. `billed_total` is
> what its releases add up to.** They are the same for anything raised
> from the CRM. On FMC's 27 imported invoices `billed_total` is **0**,
> because no releases are attached. Show `grand_total` as the invoice
> amount; use `billed_total`/`received_total` only for settlement.

---

## 3. Screen A — Ready to bill

**The point of this screen: FMC has earned ₹17,22,772 of commission and
never asked for it.** It is invisible everywhere else, because every
other panel measures what lenders *owe* — and an unbilled release is owed
just the same.

### 3a. `GET /api/v1/reconciliation/ready-to-bill`

```jsonc
{
  "lenders": [
    { "bank_name": "Axis Direct (UC Code)",
      "releases": 30,
      "disbursed_total": "26872314.00",
      "commission_total": "362776.24",   // ex-GST; GST is added at billing
      "oldest_release": "2026-04-07",
      "can_invoice": true },
    { "bank_name": "Nomad Normal", "releases": 4,
      "commission_total": "304259.12", "can_invoice": true }
  ],
  "releases": 127,
  "commission_total": "1722771.63",
  "blocked_lenders": 5
}
```

Accepts the same global filters as the dashboard (`bank_name`,
`source_id`, `disbursed_from`, `disbursed_to`).

### 3b. The layout

```
┌──────────────────────────────────────────────────────────────┐
│  NEVER INVOICED                              ₹17,22,772      │
│  127 releases · 5 lenders cannot be billed yet               │
└──────────────────────────────────────────────────────────────┘

   Lender                  Releases   Oldest      Commission
   Axis Direct (UC Code)      30      07-Apr-26   ₹3,62,776   ▸
   Nomad Normal                4      15-Jul-26   ₹3,04,259   ▸
   UC Axis                    23      10-Apr-26   ₹3,01,414   ▸
   Zolve                       5      23-Jan-26   ₹2,36,167   ▸
   GyanDhan                   15      01-Feb-26   ₹1,64,740   ▸
   BOI                         4      12-Mar-26      ₹11,026   ⚠ no GSTIN
```

- Sort by `commission_total` descending — it arrives that way
- **`oldest_release` is the urgency signal.** Zolve's oldest is January.
  Show the age, and colour it past ~90 days
- `can_invoice: false` → row is not selectable, and says
  **"Add this lender's GSTIN before billing"**, linking to lender admin.
  5 lenders are in this state (BOI, PNB, PNB Direct, UBI, IDFC)

### 3c. Expanding a lender

You need the individual releases to tick. Use the drill-down:

```
GET /api/v1/reconciliation/drilldown?segment=lender&value=Axis%20Direct%20(UC%20Code)
```

⚠️ **That returns ALL of a lender's releases, billed or not.** There is no
"unbilled only" filter on it yet. Either filter client-side on the ones
present in `ready-to-bill`, or ask for the endpoint — say so and it will
be added rather than worked around.

```
   Axis Direct (UC Code) — 30 releases, ₹3,62,776

   ☑  #8341  Aditya Konar        ₹15,00,000   1.35%   ₹20,250
   ☑  #8345  Nishant Ranjan      ₹14,60,000   1.35%   ₹19,710
   ☑  #8342  Anushka Purwar      ₹14,50,000   1.35%   ₹19,575
   ☐  #5627  Soumandip           ₹13,00,000   1.35%   ₹17,550
                                             ──────────────────
      3 selected                              ₹59,535 + GST

                                    [ Raise invoice ]
```

- **Select-all per lender** — the common case is billing a whole month
- Running total of the selection, ex-GST, labelled as such

### 3d. Raising the invoice

```
POST /api/v1/reconciliation/invoices/bulk
     { "disbursement_ids": ["...", "..."],
       "invoice_date": "2026-09-08" }   // optional, defaults today
```

Returns the created invoice: `invoice_number`, `subtotal`, `total_tax`,
`grand_total`, `pdf_url`, `releases`.

**On success:** show the invoice number, offer the PDF, and take the user
to it. **The invoice number is permanent** — GST numbering is sequential
and gapless — so never offer a "retry" that would raise a second one.

**Refusals, all 400, all with a message worth showing verbatim:**

| Cause | Message shape |
|---|---|
| Two lenders selected | *"An invoice has one customer. These releases span 2 lenders (Kuhoo, Zolve) — raise one invoice per lender."* |
| Already invoiced | *"One of these releases is already on an invoice…"* |
| Written off / earns nothing | *"…so there is nothing to bill."* |
| **Lender has no GSTIN** | *"'BOI' cannot be invoiced yet — no GSTIN on file…"* |

The GSTIN one should not be reachable if 3b greys those rows out — but
handle it, because the lender list can change under an open page.

---

## 4. Screen B — Invoice list

`GET /api/v1/invoices?page=1&page_size=25` — **paginated, and FMC has 27
invoices, so page 1 shows 25.** Build the pager or pass `page_size=100`
(max). Two invoices are currently invisible for this reason.

Each row now carries settlement:

```jsonc
{
  "invoice_number": "FMC/2026-27/016",
  "invoice_date": "2026-07-10",
  "customer_name": "UniCreds Private Limited",
  "grand_total": "91009.00",     // the invoice's own face value
  "status": "issued",            // STORED — do not display this
  "effective_status": "issued",  // DISPLAY THIS
  "linked_releases": 0,
  "billed_total": "0.00",
  "received_total": "0.00",
  "outstanding_total": "0.00",
  "pdf_url": "https://…"
}
```

### 4a. Render `effective_status`, never `status`

| `effective_status` | Meaning | Suggested |
|---|---|---|
| `issued` | nothing received yet | grey |
| `part_paid` | some settled | amber |
| `paid` | fully settled | green |
| `void` | cancelled | strikethrough |

It is derived from the releases, so it moves on its own when a payment is
recorded. Nobody sets it.

### 4b. Invoices with `linked_releases: 0`

**All 27 of FMC's current invoices are like this** — they were imported
from PDFs that predate CRM billing, so no releases are attached.

For those rows:
- Show the amount from **`grand_total`**, not `billed_total` (which is 0)
- Show `effective_status` (their stored status, `issued`)
- **Hide** received/outstanding — there is nothing to derive them from
- **Disable "Record payment"** with a tooltip:
  *"This invoice predates CRM billing and has no releases attached.
  Record the payment on the release instead."*

This is deliberate, not a bug. Deriving "paid" from an empty set of
releases would have marked all 27 paid and invented ₹12.5 lakh of
collections.

### 4c. Row actions

| Action | Enabled when | Endpoint |
|---|---|---|
| **Record payment** | `linked_releases > 0`, not void, not paid | §5 |
| **Download** | `pdf_url` present | it is, on all 27 |
| **View releases** | `linked_releases > 0` | drill-down by invoice — **ask for this endpoint, it does not exist** |
| **Void** | not paid | `PATCH /invoices/{id}/status {status:"void", void_reason}` |

⚠️ **Do not offer "Mark as paid".** `PATCH /invoices/{id}/status` now
**refuses** `paid` on any invoice with linked releases:

> *"This invoice bills 3 release(s), so whether it is paid comes from
> them. Record the payment against the invoice instead of setting the
> status."*

---

## 5. Screen C — Record payment

```
POST /api/v1/reconciliation/invoices/{invoice_id}/payment
```

```jsonc
{
  "amount_received": "40997.92",
  "tds_deducted": "0",
  "received_on": "2026-09-08",
  "payment_reference": "UTR123456",
  "allocation": null            // optional, see 5c
}
```

### 5a. The form

```
┌─ Payment against FMC/2026-27/029 ──────────────────┐
│  Kuhoo Finance Private Limited                     │
│  Billed ₹40,997.92 · received ₹10,000 · owed ₹30,997.92
│                                                    │
│  Amount received   [ ₹30,997.92        ]           │
│  TDS deducted      [ ₹0                ]  ⓘ        │
│  Date received     [ 08/09/2026        ]           │
│  Reference / UTR   [                   ]           │
│                                    [ Record ]      │
└────────────────────────────────────────────────────┘
```

- **Prefill "amount received" with the outstanding balance.** That is the
  answer nine times out of ten
- **ADDITIVE.** A second payment adds to the first. Label it *"Add
  payment"* on an invoice that already has one, so nobody fears
  overwriting

### 5b. TDS — do not let it be skipped

Put a hint next to it:

> *Enter TDS separately, do not subtract it. It is not a shortfall — it
> is tax paid on FMC's behalf and reclaimable. A receipt entered net with
> this blank makes the release look underpaid.*

**Currently 0 of FMC's 127 releases record any TDS**, which for s.194H
commission is unlikely and may mean receipts were entered net. This field
is how that stops.

### 5c. Allocation is automatic

The payment is split across the invoice's releases **pro-rata by what
each still owes**. So paying the exact outstanding clears the invoice to
zero. You do not need to build a splitter.

`allocation` (`{disbursement_id: amount}`, must sum to
`amount_received`) exists for lenders who itemise. **Leave it out of v1.**

### 5d. The response

```jsonc
{ "invoice_number": "…", "releases": 3,
  "billed_total": "40997.92", "received_total": "40997.92",
  "outstanding_total": "0.00",
  "allocation": [ { "disbursement_id": "…", "amount_received": "…",
                    "now_settled": "…", "still_owed": "…" } ] }
```

Show the per-release split after recording — it is reassuring, and it is
how someone spots a payment that landed on the wrong bill.

### 5e. Refusals

| Cause | Message |
|---|---|
| Void invoice | *"Invoice … is void…"* |
| No linked releases | *"…has no releases attached… record the payment on the release itself."* |
| Nothing entered | *"Enter what arrived — an amount, the TDS withheld, or both."* |
| Date before invoice date | *"Payment date is before the invoice date. One of the two is wrong."* |

---

## 6. Two endpoints to ask for

Neither exists. **Ask rather than working around them** — both are small:

1. **Unbilled-only releases for a lender.** §3c currently needs
   client-side filtering of a drill-down that returns billed ones too.
2. **Releases on an invoice.** For §4c "View releases". `invoice_id` is
   on `bank_disbursements`; nothing exposes it.

---

## 7. Errors and gating

| Situation | Response |
|---|---|
| Non-admin | 403 |
| Admitverse tenant | 400 — FMC-only |
| Any refusal above | 400, message meant for the user |

**Show the backend's message.** They name the missing field, the lenders
that clashed, or the release already billed. Paraphrasing loses that.

---

## Acceptance

- [ ] **Ready to bill** page: lenders by unbilled commission, oldest-age
      signal, GSTIN-blocked rows greyed with the reason
- [ ] Expand a lender, tick releases, running ex-GST total, raise one
      invoice; refusals shown verbatim
- [ ] Invoice list **paginates** (27 invoices, 25 per page today)
- [ ] Rows render **`effective_status`**, never `status`
- [ ] `linked_releases: 0` rows show `grand_total`, hide settlement,
      disable Record payment with the tooltip
- [ ] **No "Mark as paid" anywhere**
- [ ] Record payment prefills the outstanding, is labelled additive, and
      carries the TDS hint
- [ ] Per-release allocation shown after recording
- [ ] All money parsed as decimal, rendered with Indian grouping
