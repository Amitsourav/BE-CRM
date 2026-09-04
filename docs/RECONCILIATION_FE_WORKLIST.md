# Reconciliation — everything still to build

> For the dev/agent working in the **CRM-UI** repo.
> Consolidates your 2026-09-04 coverage audit, the bugs found since, and
> the new analytics dashboard. Everything here is live on the backend.
> Written 2026-09-04.

Work top-down. §1 is a bug that makes a stage unreachable, §2 and §3 are
guard rails against the data errors that cost two days of reconciliation,
and §4 is the new page.

---

## 1. 🔴 BUG — the PF Paid stage move is impossible from the UI

Amit hit this today. Moving a lead to PF Paid fails every time and the
form gives no way to fix it.

`POST /api/v1/leads/{lead_id}/stage` requires **`due_date`** for every
NON-TERMINAL stage. The PF Paid dialog collects `bank_name` and
`bank_loan_amount_lakh` but has no follow-up date field, so:

```
400 — "Follow-up date is required when moving a lead to 'pf_paid'."
```

**Fix:** add the follow-up date picker to the PF Paid dialog.

Only **`disbursed`** and **`lost`** are terminal. Every other FMC stage
needs the date — audit the whole set while you are in there:

| Needs `due_date` | No date |
|---|---|
| created · contacted · dnp · qualified · processing · logged_in · sanctioned · **pf_paid** · opportunity | disbursed · lost |

The rule exists because a lead sitting at PF with no scheduled follow-up
never reappears on anyone's task list — and PF is exactly where a loan
becomes real and needs chasing to disbursement.

---

## 2. 🔴 Stage form guard rails — where the bad data came from

Five students had a **sanction typed in as a disbursement**. That single
mistake put ₹1.36 crore of wrong figures into the CRM. Every one came
through the stage form. Your tranche dialog already guards this properly;
the stage form does not, and it is the one people hit first.

**F4 — pre-fill the lender (MISSING).** Sanctioned and Disbursed open an
empty lender select. Picking the wrong one attributes the commission at
the wrong rate.

> **Check before you build anything.** The backend already defaults it —
> `stage_machine.py:226` and `:259` do `bank_name = bank_name or
> lead.bank_name`. If the form simply **omits** `bank_name` instead of
> sending an empty value, the backend fills in the lead's primary lender.
> This may be zero code.

**F5 — say "first release" (MISSING).** Nothing on the Disbursed form
says the amount is tranche 1, not the whole loan. Add helper text:
*"Amount the bank actually released — not the sanctioned amount. Later
instalments are added from the lender file."*

**F3 — label the unit (PARTIAL).** The input shows a "lakh" suffix but the
labels read "Sanctioned amount" / "Amount released". Only the tranche
dialog says "(in lakhs)". Make both stage forms say it too.

### New: the backend now refuses the impossible

Two guards shipped today. Surface the `detail` string directly — both are
written to be read by a person:

| Situation | Response |
|---|---|
| Disbursement exceeds the file's sanctioned amount | 400 — *"That would make Kuhoo disburse ₹20,00,001 against a sanction of ₹20,00,000. A lender cannot release more than it approved…"* |
| Marking a lender file `lost` when it holds tranches | 400 — *"'Axis Direct (UC Code)' has disbursements recorded against it, so it cannot be marked lost…"* |

The second one matters for the bank-status dropdown: **`lost` can now
fail.** Do not assume a status change always succeeds.

---

## 3. 🟠 Reconciliation report gaps (from your audit)

**B3 + A3 — the GST column (MISSING / PARTIAL).** `gst_total` is now on
`/reconciliation/summary` rows and in `/reconciliation` totals. Add the
column to both.

> Correcting the note in your audit: GST is **not** null until invoicing.
> All 125 tranches carry it, ₹3,00,508 in total, and none is invoiced. It
> is **41% of the outstanding figure** — without the column, outstanding
> cannot be explained from what is on screen.

**E4 + E1 — no way to correct a tranche (PARTIAL).** `commission_rate`
and `gst_amount` have no editor anywhere, and the report row opens the
payment dialog rather than the tranche. A wrong rate currently needs a
database write. `PATCH /reconciliation/disbursements/{id}` accepts both.

**G4 — drop the naming heuristic (BUILT, now improvable).**
`is_aggregator` is a real boolean on `/leads/banks/manage`, true for
`Axis`, `Nomad`, `UniCred`. Replace the name-overlap guess with the flag.

A file on an aggregator **can never earn** — an aggregator fronts several
lenders at different rates, so it has none of its own. 14 live files are
parked this way. Flag them for someone to move.

**G2 — add-lender (PARTIAL).** `POST /leads/banks` is live and takes
`name`, `commission_rate`, `sort_order`, `is_aggregator`. 35 lenders now.

**A2 — page size (PARTIAL).** `page_size` is capped at 200. Low priority.

---

## 4. 🟢 NEW — the Loan Intelligence dashboard (six tabs)

Full brief: **`docs/RECONCILIATION_DASHBOARD_FRONTEND_PROMPT.md`**.
Built against Amit's design at
`fundmycampus-loan-analytics.nishuash.chatgpt.site`. This is the summary;
read that one before starting.

Six tabs — Overview · Pipeline & Forecast · Revenue & Collections ·
Lender Performance · Source Performance · Data Control Centre — with
global filters and **every segment clickable to open the students behind
it.**

| Endpoint | Tab |
|---|---|
| `GET /reconciliation/dashboard?months=12` | Overview |
| `GET /reconciliation/pipeline` | Pipeline & Forecast |
| `GET /reconciliation/summary` *(existing)* | Lender Performance |
| `GET /reconciliation/sources` | Source Performance |
| `GET /reconciliation/exceptions` | Data Control Centre |
| `GET /reconciliation/drilldown?segment=&value=` | every clickable segment |

All admin-only, FMC-only, all taking the same five filters:
`bank_name` · `source_id` · `disbursed_from` · `disbursed_to` · `as_of`
(the first two repeatable). Hold filter state in ONE object.

**Build drill-down first.** It is the idea the whole design rests on, and
it returns TWO counts — `total` (students) and `tranche_total` (tranches
in that segment) — because ageing and by-lender count tranches while the
stage funnel counts students. Header the drawer "41 students · 46
tranches" or it will look like it contradicts what was clicked.

### Six traps, all covered in the full brief

1. Funnel percentages are **step-to-step**, not against the top.
2. The trend's two series use **different dates** — earned by
   disbursement month, collected by receipt month. Do not reconcile a
   single month's two bars.
3. **`no_date` is a real ageing bucket** holding 56% of everything
   outstanding. Render it or the panel lies.
4. Show **`tranches_materially_short` (4)**, not `tranches_short` (71).
   The rest is rounding.
5. **Sources: unattributed is a separated footer row, never ranked.** It
   is ~40% of disbursement; heading a marketing league table with it
   would be misleading.
6. `potential_net_revenue` already has the 80% haircut applied. Do not
   multiply again.

### Two controls in the mockup that cannot be built yet

- **"All closure months"** — no such field outside the spreadsheet. Label
  the control **"Disbursement month"** and drive it off
  `disbursed_from`/`disbursed_to`. A real `expected_closure_month` field
  is planned.
- **"invoiced" in Cash Control** — no invoiced figure exists. FMC bills
  outside the CRM. Show earned, collected, outstanding. No invoiced
  column, no "unbilled" tile.

---

## Suggested order

| | | Why |
|---|---|---|
| 1 | §1 PF Paid date field | A stage is unreachable today |
| 2 | §2 F4 · F5 · F3 | Stops the ₹1.36 cr class of error at source |
| 3 | §4 the dashboard | Six tabs; build drill-down first |
| 4 | §3 B3/A3 GST column | Explains the outstanding figure |
| 5 | §3 E4/E1 tranche editing | A wrong rate needs a DB write today |
| 6 | §3 G4 · G2 · A2 | Cleanups |

Push back if you disagree — you can see the UI and we can't. Your last
audit corrected two of our assumptions, which is exactly what it was for.

---

## Acceptance

- [ ] A lead can be moved to PF Paid from the UI, with a follow-up date
- [ ] Every non-terminal stage form asks for a follow-up date; disbursed and lost do not
- [ ] Sanctioned and Disbursed forms pre-fill the lender (or omit the field and let the backend default it)
- [ ] Disbursed form says the amount is the first release, in lakhs
- [ ] A 400 from either new guard shows its `detail` text to the user
- [ ] `gst_total` appears in report totals and in the By-lender table
- [ ] A tranche's rate and GST can be corrected from the report row
- [ ] Aggregator files are flagged from `is_aggregator`, not from name matching
- [ ] Dashboard renders all five rows; `no_date` bucket visible; materially-short is the headline
- [ ] Indian digit grouping everywhere (₹15,15,50,551)
