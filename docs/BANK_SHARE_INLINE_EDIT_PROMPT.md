# Bank Share page — inline edit, stage dropdown, row colours

Companion to `BANK_SHARE_GRID_FRONTEND_PROMPT.md`, which describes the
grid itself. This one covers making it **editable**. Backend is live; no
new endpoints are needed beyond what is listed here.

Three things to build:

1. Click the **Loan** cell → edit the lead's loan amount in place.
2. Click the **Stage** cell → dropdown to change the lead's stage.
3. Colour the **whole row** by stage.

---

## 1. Loan amount — inline edit

**Endpoint:** `PUT /api/v1/leads/{lead_id}` with `{"loan_amount": "45"}`

It is a partial update — send only `loan_amount`, nothing else is touched.
Response is the full updated lead.

The field is **free text on purpose**. The backend parses it into the
numeric value used for sorting, filtering and reports:

| User types | Stored | Parsed to |
|---|---|---|
| `45` | `"45"` | 45 lakh |
| `7.5 Lakh` | `"7.5 Lakh"` | 7.5 lakh |
| `1.5cr` | `"1.5cr"` | 150 lakh |
| `` (empty) | `""` | cleared |

> ⚠️ **Do not put a numeric-only mask on this input.** The current lead
> page does, and it is a live bug: a lead whose value is `"7.5 Lakh"`
> cannot be edited at all, because every keystroke — including backspace —
> is rejected. 15 FMC leads are stuck in that state. Accept any text and
> let the backend parse it.

Optimistic update is fine; on error revert and show the message.

---

## 2. Stage — dropdown

**Endpoint:** `POST /api/v1/leads/{lead_id}/stage`

**The dropdown cannot be fire-and-forget.** Every stage change has to
carry extra data, and the backend rejects the request without it. What to
collect depends on the target stage:

| Target stage | Also required | UI |
|---|---|---|
| `disbursed` | nothing | apply immediately |
| `lost` | `lost_reason` | ask for a reason |
| **`pf_paid`** | `due_date` + **`bank_name`** + **`bank_loan_amount_lakh`** | ask for all three |
| everything else | `due_date` | ask for a follow-up date |

So: **`disbursed` applies on click. Everything else opens a small popover
first.** Cancel closes it and leaves the stage unchanged.

### Request body

```jsonc
POST /api/v1/leads/{lead_id}/stage
{
  "to_stage": "pf_paid",
  "due_date": "2026-08-28T00:00:00Z",   // required unless terminal
  "lost_reason": "Not eligible",         // required only for lost
  "bank_name": "PNB",                    // required only for pf_paid
  "bank_loan_amount_lakh": 60            // required only for pf_paid
}
```

Returns the updated lead. `409`/`400` on a rejected transition.

### Where the dropdown options come from

- **Stage list:** the `stages` array already returned by
  `GET /leads/by-stage?pipeline=normal`. Do not hardcode it.
- **Lost reasons:** `GET /leads/lost-reasons` — a locked list, free text
  is rejected. Render as a select, never a text box.
- **Banks:** `GET /leads/banks` — admin-managed, currently ~20. Do not
  hardcode.

### PF Paid — the important one

`pf_paid` means *one specific lender's processing fee was paid, for that
lender's sanctioned amount*. The bank and the amount are therefore
mandatory, and the popover must collect both:

```
Move to PF Paid
  Bank          [ v  PNB              ]   <- GET /leads/banks
  Amount (lakh) [    60               ]   <- in LAKHS, not rupees
  Follow-up     [    28-Aug-2026      ]
                        [Cancel] [Save]
```

**The amount is in lakhs.** The user types `60` for 60 lakh. The backend
converts to rupees for storage — do not send `6000000`, and do not add
your own multiplier.

Prefer to pre-select the bank the lead is furthest along with — that is
`shares` in the grid row, highest `bank_status` wins. Still let it be
changed.

**This rule applies on every screen, not just this one** — it lives in
the stage engine, so the Kanban drag and the lead detail page must ask
for the same three fields when moving to `pf_paid`. If you only wire it
here, PF Paid will break everywhere else.

---

## 3. Row colours

Colour the **entire row** by `current_stage`:

| Stage | Colour |
|---|---|
| `lost` | red |
| `disbursed` | green |
| `pf_paid` | orange |
| `sanctioned` | blue |
| anything else | default / no tint |

Use a **soft tint** for the row background, not a saturated fill — the
row still has to be readable, and the coloured bank cells sit on top of
it. Keep the row's own text at normal contrast and let the tint do the
work. Verify in both light and dark mode; a tint that works on white
usually fails on a dark background, so define both.

Colour is decoration, never information on its own — a colour-blind user
must still see the stage name in the Stage cell.

---

## 4. Which loan amount to display

The row now returns two things:

```jsonc
{
  "loan_amount": "60.00",                 // what the STUDENT asked for
  "pf_paid_banks": [                      // what a LENDER committed
    { "bank_name": "Axis", "loan_amount_lakh": 60.00 }
  ]
}
```

Rule:

- `pf_paid_banks` is **empty** → show `loan_amount` (the normal case).
- `pf_paid_banks` is **non-empty** → show those figures instead, with the
  bank name, e.g. `60 L (Axis)`. Once a lender has actually committed
  money, that is the real number.
- **Two banks** → show both: `60 L (Axis), 28 L (PNB)`.

`loan_amount_lakh` is already converted to lakhs — display as-is.

> **Expect blanks.** 8 of the 11 leads currently at `pf_paid` were moved
> there before the bank became mandatory, so they have no bank recorded
> and `pf_paid_banks` is `[]`. Fall back to `loan_amount` for those — do
> not render an error or a dash. New PF Paid moves cannot be blank.

Each cell also now carries `loan_amount_lakh` — that lender's own figure,
`null` until it reaches `sanctioned`. Useful in the hover tooltip.

---

## 5. After any change

A stage change touches more than this page. On success:

- Refresh the row from the response (the endpoint returns the full lead).
- Invalidate the Kanban / pipeline query — the lead has moved column.
- A `pf_paid` change also updates that lead's **bank cell** in this same
  grid (it becomes `pf_paid` with the amount) and the lead's primary
  bank. Simplest correct thing is to refetch the current grid page.

The backend already keeps everything consistent; this is only about not
showing the user a stale screen.

---

## 6. Errors to handle

All arrive as `400` with a readable `detail` string. Show it directly —
they are written for the user, not for logs.

| Situation | `detail` |
|---|---|
| PF Paid missing bank or amount | `bank_name and bank_loan_amount_lakh (in lakhs) are both required when moving a lead to 'pf_paid'.` |
| Bank not in the list | `Unknown bank 'X'. See GET /leads/banks.` |
| Amount ≤ 0 | `bank_loan_amount_lakh must be greater than 0.` |
| Missing follow-up date | `Follow-up date is required when moving a lead to 'X'.` |
| Lost with no reason | `lost_reason is required when moving to 'lost'` |
| Lost reason not in the list | `lost_reason must be one of the canonical FMC values…` |

---

## 7. Edge cases

- **`disbursed` is terminal — nothing moves out of it.** The dropdown
  must be disabled (or empty) for a disbursed lead. The backend rejects
  every target.
- **`lost` can only go back to `created`.** No other target is valid.
- **Restricted roles** (manager, pre_counsellor) can only edit leads
  assigned to them; the backend returns `403` otherwise. Either hide the
  controls on rows they don't own, or handle the 403 cleanly.
- **Cells stay per-bank.** Changing the row's stage does not overwrite the
  other bank cells — a lead can be at `pf_paid` with Axis while still
  `applied` with PNB, and that is correct, not a bug to reconcile.
- The grid is read-only for **shares themselves** — there is still no UI
  flow for creating or deleting a share. The WhatsApp bot writes those.
