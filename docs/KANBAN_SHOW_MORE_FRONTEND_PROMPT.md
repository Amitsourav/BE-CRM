# Kanban "Show more" — frontend build prompt

> Give this file to the agent/dev working in the **CRM-UI** repo.
>
> The backend is live — build §4, the real fix. §3 is kept only as a
> fallback if you need something shippable before a backend deploy.
>
> Written 2026-08-31 against BE-CRM `main`.

---

## The bug

Every column on the Pipeline board silently stops at **50 cards**.

Scroll to the bottom of Created on the FMC AI board and the cards just
end. There is no "Show more", no "+N more", no spinner — nothing tells
you that 6,585 more leads exist behind the 50 you can see. Users assume
the column *is* 50 leads.

The backend is not at fault and needs no change for you to detect this.
It caps each column at `per_stage_limit` (default **50**) on purpose — a
19-column Admitverse board shipping thousands of cards per refresh would
be unusable — and it **already tells you the real total** in the same
response.

---

## 1. The two numbers you need

```http
GET /api/v1/leads/by-stage?pipeline=ai
```

```jsonc
{
  "stages": ["created", "contacted", "dnp", "qualified", "lost"],
  "pipeline": "ai",
  "items_by_stage": {
    "created": [ /* at most 50 cards */ ]
  },
  "counts_by_stage": {
    "created": 6635          // ← the REAL total, filters applied
  },
  "total": 6635
}
```

- `items_by_stage[stage]` — the cards you render. Capped.
- `counts_by_stage[stage]` — how many actually match. **Not capped.**

`counts_by_stage` is computed by a second query that runs through the
exact same filter helper as the card query, so the two can never
disagree. Whatever the user has filtered to, this number is correct for
that filter.

**The rule:**

```ts
const shown = items_by_stage[stage]?.length ?? 0
const total = counts_by_stage[stage] ?? 0
const remaining = total - shown        // render "Show more" when > 0
```

That's the whole detection. If you do nothing else in this document,
doing this much at least stops lying to the user.

---

## 2. What to render

At the **bottom of the column's scroll area**, after the last card — not
pinned, not in the header. The user has to reach the end of the cards to
learn there are more, so that is where the answer belongs.

```
┌─────────────────────────┐
│  … last card …          │
├─────────────────────────┤
│   Show 50 more          │   ← full-width button
│   6,585 remaining       │   ← muted subtext
└─────────────────────────┘
```

Details that matter:

- **Format the number** — `6,585`, not `6585`.
- **Loading state** — swap the label for a spinner and disable the
  button. Columns with thousands of leads are the slow ones; a
  double-click must not fire two fetches.
- **Append, don't replace.** New cards go on the end of the existing
  array. Scroll position must not jump.
- **Hide it at zero.** When `remaining <= 0` render nothing at all — no
  empty div, no "0 remaining", no disabled button.
- **The column header count stays `counts_by_stage`.** It already shows
  the true total (6,635) and should keep doing so as you page in. Don't
  switch it to the loaded-card count.

Do this for **every** column on **both** boards. The counts differ wildly
per stage — Lost and Created are huge, Qualified is small — so a column
that never truncates simply never shows the button, for free.

---

## 3. What works TODAY (no backend change)

`per_stage_limit` is already a query param, and it accepts up to **200**:

```http
GET /api/v1/leads/by-stage?pipeline=ai&per_stage_limit=200
```

So you can ship a working button immediately by refetching the whole
board at a higher limit: 50 → 100 → 150 → 200.

Understand the two costs before choosing this:

1. **It raises the limit on every column at once**, not just the one the
   user clicked. Clicking "Show more" on Created also pulls 50 more Lost
   cards nobody asked for.
2. **It hard-stops at 200.** Past that the button cannot do anything, and
   a 6,635-lead column is still 97% invisible.

This is a legitimate ship-this-week fix and it is strictly better than
today. But it does not solve the problem, so treat it as interim.

---

## 4. The real fix — build this

The clean version fetches the next page of **one** column, carrying the
board's current filters:

```http
GET /api/v1/leads/by-stage?pipeline=ai&stage=created&offset=50&per_stage_limit=50
```

Response uses the same envelope, with one entry:

```jsonc
{
  "stages": ["created"],
  "pipeline": "ai",
  "items_by_stage": { "created": [ /* leads 51–100 */ ] },
  "counts_by_stage": { "created": 6635 },
  "total": 6635
}
```

**You must resend every filter param that is currently active on the
board** — `q`, `sort_by`, `loan_min`, `bank_name`, `tags`,
`target_intake`, `created_from`, `important_only`, the Admitverse
`budget_*` / `university` / `application_status` ones, all of them.
Keep the board's filter state in one object and spread it into both the
initial load and every "Show more" call. If the two calls disagree on
filters, page 2 returns leads that don't match what the user searched
for and they land in the same column, mixed in with page 1.

`counts_by_stage[stage]` comes back on every page and already reflects
those filters, so keep using it for both the header and the remaining
count — don't cache the number from the first load.

### Errors it will return

| Condition | Response |
|---|---|
| `offset` sent without `stage` | 400 `offset requires stage` |
| `stage` not a column on this board | 400 `unknown stage '…' for this board; expected one of [...]` |
| `offset` past the end | 200, empty array for that stage, count still correct |

The second one is worth noting: the stage list is **per board**. `dnp` is
valid on the AI board; a normal-board-only stage is not. Send the same
`pipeline` value you loaded the board with and this can't bite you.

### Do NOT use `GET /leads?current_stage=X&page=2` for this

It looks like the obvious answer — it paginates properly — but it accepts
only 5 of the board's filters and silently ignores the other 22
(`q`, `pipeline`, `sort_by`, `loan_min/max`, `bank_name`, `bank_status`,
`tags`, `target_country`, `target_intake`, `created_from/to`,
`due_from/to`, `dnp_min/max`, `important_only`, `university`,
`budget_*`, `application_status`).

With a filter active, "Show more" via that endpoint appends **unfiltered
leads** to a filtered column. No error, no warning — the column just
quietly fills with wrong data and the header count stops matching the
cards. That is worse than having no button.

It also returns `LeadOut` (35 fields incl. `notes` and `custom_fields`
JSONB), not the slim `LeadCardOut` the board renders, so the card
components would need a second shape anyway.

---

## 5. Acceptance

- [ ] Bottom of a truncated column shows "Show N more" + formatted remaining count
- [ ] Column with fewer leads than the limit shows nothing
- [ ] Clicking appends; scroll position holds; header count unchanged
- [ ] Button disabled + spinner while in flight; double-click fires one request
- [ ] Works on both `pipeline=ai` and `pipeline=normal`
- [ ] **With a filter applied** (try `q=` plus a `bank_name`), paged-in cards still match the filter
- [ ] Button disappears once the last page is loaded
- [ ] Paging past the end returns an empty array, not an error, and the button hides
