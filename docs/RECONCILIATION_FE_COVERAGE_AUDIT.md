# Reconciliation — frontend coverage audit

> For the dev/agent working in the **CRM-UI** repo.
> Not a build brief. A checklist: **tick what exists, name what doesn't.**
> Every endpoint below is live in production today (2026-09-03).
>
> Reply with this list, each row marked **BUILT / PARTIAL / MISSING**, plus
> where it lives (route or component). Where PARTIAL, say what's absent.

---

## Why this audit

The backend commission subsystem was built across Aug–Sep 2026. We do not
have a reliable picture of how much of it reached the UI, and on
2026-09-03 a reconciliation of ₹16 crore had to be done by hand against a
spreadsheet. Anything MISSING here is work the team is still doing in
Excel — which is exactly how the CRM and the tracker drifted ₹1.62 crore
apart.

All routes are **admin-only** and **FMC-only** (they 403 on Admitverse).

---

## A. The reconciliation report

`GET /api/v1/reconciliation`

- [ ] **A1** Report screen exists, one row per tranche
- [ ] **A2** Pagination (`page`, `page_size` — default 50, max 200)
- [ ] **A3** Headline totals from `totals.*` — `disbursed_total`,
      `commission_total`, `gst_total`, `received_total`, `tds_total`,
      `outstanding_total`
- [ ] **A4** Headline states its scope when filtered *(fixed 2026-09-03 — confirm it stayed fixed)*
- [ ] **A5** Filter: `bank_name` — **repeatable**, multi-select
- [ ] **A6** Filter: `status` — repeatable: `to_bill` / `billed` / `short_paid` / `paid` / `written_off`
- [ ] **A7** Filter: `disbursed_from` / `disbursed_to` date range
- [ ] **A8** Filter: `q` — search by student name
- [ ] **A9** Row shows derived `status` and `shortfall`
- [ ] **A10** `days_outstanding` is **null** on dateless rows — rendered "—", not 0

## B. Per-lender view

`GET /api/v1/reconciliation/summary`

- [ ] **B1** "By lender" tab — who owes what
- [ ] **B2** Columns: `files`, `disbursed_total`, `commission_total`,
      `received_total`, `tds_total`, `outstanding_total`, `unbilled_count`
- [ ] **B3** **`gst_total`** — *newly added; may not be in the UI yet*
- [ ] **B4** Theoretical columns: `sanctioned_files`, `sanctioned_total`,
      `gross_theoretical_revenue`, `files_missing_amount`
- [ ] **B5** Lenders with sanctioned files but **zero** disbursements still
      appear (approved money that never converted — the row most worth seeing)

## C. Theoretical revenue

`GET /api/v1/reconciliation/theoretical`

- [ ] **C1** Panel exists
- [ ] **C2** `sanctioned_total`, `gross_theoretical_revenue`,
      `net_theoretical_revenue`, `disbursed_total`, `revenue`, `drawdown_gap`
- [ ] **C3** Honesty counters shown: `files_missing_amount`,
      `files_missing_rate`, `files_counted` of `files`. **A file with no
      amount or no lender rate is excluded from the total, not counted as
      zero** — without these the number reads complete when it isn't
- [ ] **C4** `drawdown_gap` surfaced — sanctioned money not yet released

`GET|PATCH /api/v1/reconciliation/settings`

- [ ] **C5** `net_theoretical_factor` is editable by an admin (defaults 0.8)

## D. Tranches — see `TRANCHES_FRONTEND_PROMPT.md`

`GET|POST /api/v1/leads/{lead_id}/banks/{entry_id}/disbursements`

- [ ] **D1** Tranche list on the lender file
- [ ] **D2** Add-tranche button (2nd and later instalments)
- [ ] **D3** Drawdown progress — sanctioned / drawn / still to come
- [ ] **D4** Amount field labelled **in lakhs**, helper "what the bank released, not the sanction"

**Highest priority if MISSING.** ₹30.99 cr is sanctioned but not yet
drawn — **₹31,38,986 of commission** that must be recorded as each
semester's instalment lands, or it is never invoiced.

## E. Editing a tranche / recording payment

`GET|PATCH|DELETE /api/v1/reconciliation/disbursements/{id}`

- [ ] **E1** Open a tranche from the report row
- [ ] **E2** Record a payment: `amount_received`, `tds_deducted`,
      `received_on`, `payment_reference`
- [ ] **E3** **TDS field present.** Without it every receipt looks 2–5%
      short and the shortfall column becomes noise
- [ ] **E4** Correct a tranche: `disbursed_amount_lakh`, `disbursed_on`,
      `utr_reference`, `commission_rate`, `commission_amount`, `gst_amount`
- [ ] **E5** **`earns_commission`** tick box — off means the tranche earns
      nothing whatever the rate says
- [ ] **E6** `write_off_reason` — stops chasing, drops out of outstanding
- [ ] **E7** Delete a tranche
- [ ] **E8** PATCH is `extra: forbid` — send only changed fields, not the whole row

## F. Stage moves that demand figures

`POST /api/v1/leads/{lead_id}/stage`

- [ ] **F1** Moving to **Sanctioned** asks for `bank_name`,
      `sanctioned_amount_lakh`, `sanction_date` — all required, rejected otherwise
- [ ] **F2** Moving to **Disbursed** asks for `bank_name`,
      `disbursed_amount_lakh`, `disbursed_on` — all required
- [ ] **F3** Both amounts labelled **in lakhs**
- [ ] **F4** `bank_name` pre-filled with the lead's primary lender
- [ ] **F5** The Disbursed form makes clear this is **tranche 1**, not the whole loan

## G. Lenders

- [ ] **G1** Lender dropdowns fetch `GET /api/v1/leads/banks` — **never hard-coded**
- [ ] **G2** Admin lender management: `GET /leads/banks/manage`,
      `POST /leads/banks`, `PATCH /leads/banks/{id}` — add a lender and set its rate without a deploy
- [ ] **G3** **35 lenders now**, including 8 added 2026-09-03: `UniCred Normal` 1.00,
      `UniCred Credila Domestic` 0.75, `UniCred Propelld Connector` 1.10,
      `UniCred Edgro Connector` 0.75, `Nomad Axis Domestic` 1.00, `Nomad Govt` 0.70,
      `Nomad Prodigy` 0.75, `IDFC Domestic` 0.50
- [ ] **G4** **Aggregators**: bare `UniCred`, `Nomad`, `Axis` carry **no rate** —
      they are routes with sub-products, not banks. A file left on a bare
      name can never earn commission. Flag it; do not render ₹0

## H. Formatting and safety

- [ ] **H1** Indian grouping everywhere — ₹15,94,01,706, not ₹159,401,706
- [ ] **H2** Every loan amount typed **in lakhs**, converted server-side
- [ ] **H3** Null `disbursed_on` / `received_on` render "—", never today's date
- [ ] **H4** Non-admin and Admitverse users don't see these screens at all
- [ ] **H5** No headline figure is computed client-side from a row list

---

## How to reply

For each ID: **BUILT / PARTIAL / MISSING**, plus the route or component.
Then your own ranking of what to build first — you can see the UI, we
can't. Section **D** is our priority; tell us if you disagree and why.
