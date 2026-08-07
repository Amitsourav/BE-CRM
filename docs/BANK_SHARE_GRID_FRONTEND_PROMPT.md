# Bank Share Grid — frontend build prompt

> Give this whole file to the agent/dev working in the **CRM-UI** repo.
> The backend is live; no CRM-side changes are needed.
>
> Written 2026-08-07 against BE-CRM `main`.

---

## What the page answers

For each lead: **which banks has this file gone to, and what has happened
with each?**

```
Student name | Number | Counsellor | Stage | Loan amount | Axis | PNB | SBI | ICICI | …
```

- A cell is **coloured** when that lead has been shared with that bank,
  **blank** otherwise.
- **Hovering a coloured cell** shows when it was shared, who shared it,
  and the conversation in that bank's WhatsApp group since.

FundMyCampus only — Admitverse has no banks (it uses university
applications). Gate the nav entry on `company_slug === "default"`.

---

## One call for the whole grid

```http
GET /api/v1/leads/bank-share-grid?page=1&page_size=25
```

```jsonc
{
  "banks": ["Axis", "PNB", "SBI", "Yes Bank", "ICICI", /* …19 total */],
  "items": [
    {
      "lead_id": "0d9e…",
      "serial_no": 8881,
      "full_name": "Ajoy Dhar",
      "phone": "+917439312141",
      "counsellor_name": "Himanshu",
      "current_stage": "created",
      "loan_amount": "17.5",
      "shares": {
        "PNB": {
          "shared_at": "2026-08-07T09:15:00Z",
          "shared_by_name": "Ankit Dubey",
          "source": "whatsapp",
          "bank_status": "applied",
          "message_count": 4,
          "last_message_at": "2026-08-07T11:02:00Z",
          "last_message_preview": "Login done, sanction expected Friday"
        }
      }
    }
  ],
  "total": 10283, "page": 1, "page_size": 25, "total_pages": 412
}
```

**`banks` is the column order — render columns from it, not a hard-coded
list.** It comes from the backend's canonical list — 19 today, and it grows
as new lender relationships start — and would silently drift if you
duplicated it. Poonawalla was added on 2026-08-07; more will follow.

**A bank missing from a row's `shares` is a blank cell.** That is the
whole rule for colouring. Don't infer anything from `bank_status`.

Query params: `page`, `page_size` (max 100), `q` (name/phone/email),
`current_stage`, `agent_id`, `bank_name` (only leads shared with that
bank), `shared_only` (only leads shared with at least one bank).

---

## Hover

The grid payload already carries enough for an instant tooltip —
`shared_at`, `shared_by_name`, `message_count`, `last_message_preview`.
**Render that immediately on hover**, then fetch the full thread:

```http
GET /api/v1/leads/{lead_id}/bank-shares/{bank_name}
```

```jsonc
{
  "bank_name": "PNB",
  "shared_at": "2026-08-07T09:15:00Z",
  "shared_by_name": "Ankit Dubey",
  "bank_status": "applied",
  "messages": [
    { "id": "…", "body": "Sharing the file",
      "sender_name": "Ankit", "sender_phone": "+9198…",
      "is_our_team": true,  "created_at": "2026-08-07T09:15:10Z" },
    { "id": "…", "body": "Received, will review",
      "sender_name": null,  "sender_phone": "+9199…",
      "is_our_team": false, "created_at": "2026-08-07T09:40:00Z" }
  ]
}
```

Full conversations are **not** inlined in the grid — 25 leads × 19 banks
of message history would dwarf the rest of the payload. Fetch on hover,
cache per cell for the session, and don't refetch on every mouse-over.

Style `is_our_team: true` as our side and `false` as the bank's — a
two-sided chat layout. `sender_name` is frequently `null` for the bank's
staff; fall back to `sender_phone`.

---

## Notes that will save you time

- **Latency.** The backing database runs 2–20s per request. Show a
  skeleton, not a spinner-blocking-everything, and keep `page_size` at
  25–50. The endpoint is three queries regardless of page size, so a
  bigger page is not proportionally slower — but the payload grows.
- **Horizontal scroll.** 5 fixed columns + 19 bank columns will not fit.
  Freeze the left columns and scroll the bank block.
- **`loan_amount` is a string** meaning lakhs (`"17.5"`). Some legacy rows
  still contain text like `"19 L"` — render defensively.
- **`counsellor_name` is null** when a lead is unassigned.
- **Restricted roles see only their own leads.** Managers and
  pre-counsellors get a filtered grid automatically; no client-side work,
  but their `total` will differ from an admin's.
- **Read-only page.** There is no UI flow for creating shares — the
  WhatsApp bot writes them. No delete endpoints exist for shares or
  messages, by design.

---

## Suggested filters in the toolbar

`q` search · stage dropdown · counsellor dropdown · a bank dropdown
(`bank_name`) for "everything sitting with PNB" · a `shared_only` toggle
for "only files that have gone out".
