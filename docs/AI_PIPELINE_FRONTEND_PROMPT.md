# AI calling pipeline — frontend build prompt

> Give this file to the agent/dev working in the **CRM-UI** repo.
> The backend is live; no CRM-side changes are needed.
>
> Written 2026-08-11 against BE-CRM `main`.

---

## What changed

Leads now sit on one of **two boards**:

| Board | Who works it | Stages |
|---|---|---|
| **AI calling** | AI campaigns | `created` · `contacted` · `dnp` · `qualified` · `lost` |
| **Normal** | Counsellors | the full FMC funnel (11 stages) |

Every lead carries `pipeline: "ai" | "normal"`. Enrolling a lead in a
campaign puts it on the AI board; a counsellor **hands it over** to the
normal board when it's worth working by hand — and after that, campaigns
skip it, so the AI never cold-calls someone being actively worked.

Today that's **6,635 leads on the AI board and 4,009 on normal.**

---

## 1. Two boards, one endpoint

```http
GET /api/v1/leads/by-stage?pipeline=ai
GET /api/v1/leads/by-stage?pipeline=normal
```

The response now carries its own column list:

```jsonc
{
  "pipeline": "ai",
  "stages": ["created", "contacted", "dnp", "qualified", "lost"],
  "items_by_stage": { "created": [ /* cards */ ], … },
  "counts_by_stage": { "created": 3947, … },
  "total": 6635
}
```

**Render columns from `stages`. Do not hard-code either list.** The AI
board is deliberately short — an AI phone call can dial, fail to reach,
qualify or lose someone; it cannot collect documents or log a file with a
bank. If you hard-code the 11-stage funnel on the AI board you get six
permanently empty columns; if you hard-code the 5-stage set on the normal
board you hide live loan pipeline.

Omitting `pipeline` returns **both** boards and `stages: []` — the old
behaviour, kept so nothing breaks mid-rollout. Don't rely on it for the
new UI.

Suggested UI: a toggle at the top of the Pipeline page — **AI Calling |
Normal** — that swaps the `pipeline` param and re-renders. Everything
else (filters, drag-drop, cards) works unchanged on both.

---

## 2. The handover button

```http
POST /api/v1/leads/{lead_id}/pipeline
{"pipeline": "normal", "reason": "student confirmed 60L for UCL"}
```

Returns the updated lead. `reason` is optional and is written to the
lead's remarks timeline.

Put this on the **lead detail page** and ideally on the AI-board card
menu — something like **"Move to normal pipeline"**. It's the main action
a counsellor takes after reading a promising AI call.

Reversible: `{"pipeline": "ai"}` moves it back. That direction returns
**400** if the lead's stage isn't one the AI board renders (e.g.
`processing`) — surface the message, it explains why.

Idempotent, so a double-click is harmless.

### One behaviour to know about

If someone advances an AI-board lead to a stage beyond the AI set —
dragging it to `Processing`, say — the backend **automatically** moves it
to the normal board. Advancing a lead into loan processing *is* a
counsellor taking it over, and leaving it on the AI board would put it in
a column that board doesn't render.

So after a drag that crosses that line, **the card will disappear from
the AI board.** Refetch and, ideally, toast something like *"Moved to the
normal pipeline."* Otherwise it looks like the card vanished.

---

## 3. "Why is this lead here?" — campaign info

`GET /api/v1/leads/{id}` now returns a `campaigns` array:

```jsonc
"pipeline": "ai",
"campaigns": [
  {
    "campaign_id": "71f8dff2-…",
    "campaign_name": "University college London",
    "campaign_status": "active",
    "lead_status": "completed",
    "attempt_count": 1,
    "enrolled_at": "2026-08-11T09:57:00Z"
  }
]
```

Newest first; empty for leads that were never in a campaign.

Show this on the lead detail page — a small block near the top:

> **From campaign:** University college London · called 1× · completed

It answers the question a counsellor actually has when an AI lead lands
in front of them. Only present on the detail endpoint, not on list/Kanban
payloads (it would cost a query per card).

The list/card payloads do carry `pipeline` and the existing
`has_active_ai_campaign` boolean if you want a badge.

---

## 4. Why this exists — worth reading

Before this, "is this an AI lead?" was **derived** from having a campaign
row, so it could never be undone: the only way to stop a lead counting as
a campaign lead was to delete its call history.

Separately, **1,575 leads were stranded** at a legacy stage (`lead`) that
was dropped from the pipeline in May but never migrated. They had no
Kanban column and no valid transitions — visible in the leads list,
impossible to open in the pipeline or move. Users hit this as
*"invalid stage transition from lead to qualified"*. All 1,575 came from
AI campaigns, and they've been repaired.

**So the rule that matters: a lead must never sit on a board that has no
column for its stage.** The backend enforces it in three places. On the
frontend, the equivalent is: always render from `stages`, never from a
local list.

---

## 5. Quick checklist

- [ ] Toggle: **AI Calling | Normal** on the Pipeline page
- [ ] Columns rendered from `stages`, not hard-coded
- [ ] "Move to normal pipeline" on the lead page + AI card menu
- [ ] Handle the auto-promote disappearance with a refetch + toast
- [ ] Campaign block on the lead detail page
- [ ] `counts_by_stage` still drives the "+N more" headers, unchanged
