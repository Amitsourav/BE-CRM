# Wiring invoices to commission — research

> Research, not a build. Written 2026-09-08 against live FMC data.
> Amit asked how to use the invoice section to its full potential and link
> it to the commission page, with actions like "payment received".
>
> **Read section 2 before deciding anything.** The central question is
> where a payment is recorded, and getting it wrong would break the
> reconciliation that took two days to achieve.

---

## 1. What is actually there today

FMC has **two money ledgers that have never spoken to each other**.

| | Invoices | Tranches |
|---|---|---|
| Rows | 27 | 127 |
| Money | ₹12,53,359 **billed** | ₹20,31,215 **due** |
| Payment recorded | 0 marked paid | ₹13,09,816 received |
| Link between them | **0 tranches point at an invoice** | |

Three facts fall out of that, and each is worth acting on:

**a. ₹7,77,856 of commission has never been invoiced** — 38% of what
lenders owe. Biggest: Axis Direct ₹4.28 L across 30 releases, Nomad
₹3.59 L, UC Axis ₹3.54 L, Zolve ₹2.79 L.

**b. Every invoice reads unpaid** while ₹13.1 L has demonstrably arrived.
The invoice list is telling a story the commission page contradicts.

**c. An invoice cannot hold a payment at all.** It has `due_date` and a
`status` that can be set to `paid` — and nothing else. No amount, no
date, no reference, no TDS.

```
INVOICE   payment fields:  due_date            (status = paid|void)
TRANCHE   payment fields:  amount_received, received_on,
                           payment_reference, tds_deducted
```

---

## 2. The decision: where does a payment live?

This is the whole design, and it has exactly one right answer.

### Keep money on the TRANCHE. Derive the invoice's status from it.

**Not** the other way round. Reasons, in order of weight:

1. **Every reconciled figure is built on the tranche.** Outstanding,
   ageing, collection %, the lender scorecard, "who owes what" — all of
   it reads `amount_received` and `tds_deducted` per release. Moving
   payment to the invoice would orphan the lot.

2. **One invoice covers many releases.** Invoice 010 bills five students
   at once; invoice 012 bills six. A lender paying ₹31,691 against
   invoice 010 is paying five different tranches, and the ageing report
   needs to know which. A single payment field on the invoice cannot say.

3. **Lenders part-pay.** 4 tranches are already materially short. A
   payment has to be allocable, not a flag.

4. **TDS is per release.** Section 194H is deducted per payment, and 0 of
   127 tranches currently record any — which is itself a finding (see §6).

### So the model is

```
        Invoice  ──covers──▶  Tranche(s)
           │                     │
   status DERIVED           money RECORDED
   from its tranches         here (already is)
```

An invoice's status becomes a **computed** thing:

| Derived status | When |
|---|---|
| `issued` | no tranche on it has been paid |
| `part_paid` | some settled, some not |
| `paid` | every tranche settled within tolerance |
| `void` | explicitly voided — the one status that stays manual |

**This deletes a question rather than answering it.** Nobody has to keep
the invoice and the releases in step, because there is only one truth.

---

## 3. What has to be built

### 3a. The link — `bank_disbursements.invoice_id` (exists, unused)

The column is there and 0 rows use it. `POST /reconciliation/
disbursements/{id}/invoice` already sets it for *new* invoices raised
from the CRM.

**Missing: attaching the 27 historical invoices to the tranches they
cover.** That is the parked ~3-hour job, and everything below is worth
much less until it is done — a payment button that cannot find its
releases is a button that does nothing.

### 3b. Multi-tranche billing

Today's endpoint bills **one** tranche. Real invoices bill five or six.

```
POST /reconciliation/invoices/bulk
     { "bank_name": "UC Axis", "disbursement_ids": [...] }
```

Same `InvoiceService.create`, one line item per tranche, `invoice_id`
stamped on each. This is how the ₹7.78 L of unbilled commission gets
cleared without raising 60 separate bills.

### 3c. Recording a payment against an invoice

```
POST /reconciliation/invoices/{id}/payment
     { "amount_received": 31691, "tds_deducted": 0,
       "received_on": "2026-07-02", "payment_reference": "UTR..." }
```

The interesting part is **allocation**. The lender sends one amount
against an invoice covering five tranches. Options, and my
recommendation:

- **Pro-rata by each tranche's share of the invoice** ← recommended.
  Predictable, needs no input, and matches how lenders actually pay
  (they settle the bill, not the students).
- Oldest-first — arbitrary here, since all lines share an invoice date.
- Manual per-line — correct but nobody will do it for a five-line bill.

**Pro-rata, with a per-line override available.** The default must be the
one that requires no thought.

### 3d. Derived status

`Invoice.status` becomes a hybrid over its tranches, exactly like
`BankDisbursement.status` is today. `void` stays a stored override.

⚠️ **Backwards compatibility:** `PATCH /invoices/{id}/status` currently
lets a human set `paid`. Once status is derived, that endpoint should
refuse `paid` and say why — otherwise a manual flag silently disagrees
with the money underneath it.

---

## 4. What the invoice screen should then offer

Per invoice, once linked:

| Action | Enabled when | Notes |
|---|---|---|
| **Record payment** | not void, not fully paid | the main one — §3c |
| **Download** | always | already works, all 27 have their document |
| **View releases** | linked | the students and tranches on this bill |
| **Send to lender** | has `billing_email` | email the PDF; nothing exists yet |
| **Void** | not paid | exists |
| **Unlink a release** | draft only | exists |

And on the row: **billed · received · outstanding · age**, all derived
from the tranches rather than typed.

### The panel worth adding

> **Ready to bill — ₹7,77,856 across 92 releases**
> Axis Direct ₹4.28 L · Nomad ₹3.59 L · UC Axis ₹3.54 L · Zolve ₹2.79 L

Group by lender, tick releases, raise one invoice. That single screen
converts the biggest number in this document into invoices.

---

## 5. Order of work

**Scope decided by Amit, 2026-09-08: build it for FUTURE invoices.**
Retro-linking the 27 historical ones is welcome if it falls out, but is
explicitly not required. That removes what I had called the blocker, and
reorders everything:

| | | Why |
|---|---|---|
| **1** | Bulk billing — raise one invoice from N releases | Real invoices bill 5-6 students. The single-tranche endpoint cannot express one |
| **2** | Derived invoice status | Kills the contradiction between the two screens |
| **3** | Record-payment with pro-rata allocation | The action Amit asked for |
| **4** | "Ready to bill" panel | Turns the ₹7.78 L backlog into invoices |
| **5** | Email to lender | Convenience, no reconciliation value |
| — | Retro-link the 27 | Optional. Nice, not needed |

### ⚠️ The trap this scope creates

The 27 historical invoices have **no linked tranches**. If status is
derived as *"paid when every linked tranche is settled"*, then an invoice
with zero links satisfies that vacuously and **all 27 would flip to
paid** — inventing ₹12.5 L of collections.

So the rule is:

```
linked tranches?  ──yes──▶  status DERIVED from them
                  ──no───▶  status STORED, as it is today
```

Historical invoices keep behaving exactly as they do now, and every
invoice raised from the CRM gets the live status. No migration, no
backfill, and the two eras coexist without either lying.

---

## 6. Two findings from this research, unrelated to the build

**No TDS is recorded anywhere.** 0 of 127 tranches. Lenders deduct TDS
under s.194H on commission, so either FMC's lenders genuinely do not, or
receipts have been entered net and the TDS silently written off as a
shortfall. If it is the second, some part of the ₹7.2 L "outstanding" is
not debt at all — it is tax already paid on FMC's behalf and reclaimable.
**Worth one question to the accountant.**

**Receipt dates are missing on 61 of 72 payments.** The money is recorded
but not when it arrived, so the collection-speed figures on the dashboard
are built on 11 data points. Recording a payment through §3c would fix
this going forward by making the date part of the same action.
