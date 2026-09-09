# Iconiq Energy CRM — frontend build prompt

> Give this file to the agent/dev working in the **CRM-UI** repo.
> The backend is **live and verified**. No BE-CRM changes are needed.
>
> Written 2026-09-09 against branch `iconiq-crm` @ `73402f1`.

---

## 0. What you are building

**Iconiq Energy** manufactures hybrid inverters (3–125 kW), Li-ion
batteries (5–100 kWh) and complete Battery Energy Storage Systems
(125 kWh – 5 MWh) from a 2.5 GWh plant. They sell **B2B** — to factories,
hospitals, hotels, data centres and grid operators.

This is the **third brand** on the same CRM-UI codebase, alongside
FundMyCampus and Admitverse. Same app, third Vercel deployment, pointed
at a different API.

**API base URL**

```
https://iconiq-crm-production.up.railway.app
```

Health check, no auth: `GET /health` → `{"status":"healthy"}`

---

## 1. Vercel — third deployment

Create a **new Vercel project** from the same CRM-UI repo (this is how
FMC and Admitverse already work — one repo, several projects).

| Env var | Value |
|---|---|
| `NEXT_PUBLIC_API_URL` | `https://iconiq-crm-production.up.railway.app` |
| `NEXT_PUBLIC_APP_NAME` | `Iconiq Energy CRM` |

> ⚠️ **Get `NEXT_PUBLIC_APP_NAME` right.** The FundMyCampus deployment
> has shipped with the wrong app name in it for months and still shows
> "Admitverse CRM" in places. It is a one-line fix that nobody makes
> because nobody notices. Set it correctly on day one.

**When you have the Vercel URL, send it to the backend team.** The API's
`CORS_ORIGINS` is currently a placeholder (`http://localhost:3000`), so
until the real URL is added **every request from the deployed frontend
will be blocked by the browser** and the app will look completely broken
while the API is perfectly healthy. This is a one-minute backend change;
it just has to actually happen.

---

## 2. Scope — eight pages, and nothing else

| Nav | Page |
|---|---|
| Main | Leads |
| Main | Calls |
| Main | Pipeline (Kanban) |
| Main | Tasks |
| Main | Notifications |
| Admin | Website Leads |
| Admin | Users |
| Admin | CSV History |

**Explicitly NOT in scope — do not render these for Iconiq:**

- Reports / Dashboard (deferred by the client, will come later)
- AI voice agents, campaigns, call recordings, transcripts, sentiment
- Banks, lenders, loan amounts, sanctions, disbursements, commission,
  the bank-share grid, invoicing
- Universities, applications, visas, document checklists

If the CRM-UI codebase renders these from a shared layout, gate them on
the brand. The backend already refuses or empties them (§8), but a tab
that loads and shows nothing is worse than a tab that isn't there.

---

## 3. Auth

Unchanged from the other two brands — Supabase JWT, `Authorization:
Bearer <token>`, `GET /api/v1/users/me` to load the profile.

Roles are `admin` > `manager` > `pre_counsellor`.

> The role **`pre_counsellor`** is FundMyCampus vocabulary and will read
> oddly to a solar sales team. The backend value cannot change without
> touching all three brands, so **relabel it in the UI** — "Sales Rep" or
> similar. Send the label you pick to the backend team so the docs agree.

---

## 4. The lead — 10 capture fields

This is the biggest visible difference from the other two brands. An
Iconiq lead is **an organisation with a site and an electrical load**,
not a student.

| # | Label | Field | Type |
|---|---|---|---|
| 1 | Name | `full_name` | text, **required** |
| 2 | Phone | `phone` | text |
| 3 | Email | `email` | text |
| 4 | Organization | `organization` | text |
| 5 | Website | `website` | text |
| 6 | City of project | `city` | text |
| 7 | Application (industry) | `application_industry` | dropdown — `GET /leads/industries` |
| 8 | Load / Capacity (PCS sizing) | `load_capacity_kw` | number, **kW** |
| 9 | Backup required | `backup_duration_hours` | number, **hours**, allows decimals |
| 10 | Solar present | `solar_present` | Yes / No |
| 10a | ↳ Solar plant capacity | `solar_capacity_kw` | number, **kW** — show only when 10 = Yes |
| 11 | DG available | `dg_available` | Yes / No |
| 11a | ↳ DG capacity | `dg_capacity_kva` | number, **kVA** — show only when 11 = Yes |

Notes that matter:

- **`dg_capacity_kva` is kVA, not kW.** DG sets are rated in kVA and
  that is what the customer reads off the plate. Label the unit on the
  input; do not convert.
- **Backup duration is fractional.** A 30-minute UPS-style backup is
  `0.5`. Don't restrict to integers.
- **The conditional fields are display-only logic.** The backend stores
  them independently and never validates one against the other, so a
  half-filled enquiry saves fine. If someone answers "Solar: Yes", types
  a capacity, then flips to "No", decide deliberately whether you clear
  the value or keep it hidden — the API will store whatever you send.
- Only `full_name` is required by the backend. Everything else is
  optional, because a website enquiry arrives with a name and a phone
  number and nothing more.

### Creating a lead — two things will 400 you

```http
POST /api/v1/leads
```

**1. `lead_source_id` is mandatory.** A create without it fails with:

> "A lead source is required. Pick where this lead came from…"

Fetch the options from `GET /api/v1/leads/sources/list` and make it a
required field in the form. Seven are already set up: Website, Referral,
Calling, Meta Ads, WhatsApp, Exhibition / Trade Show, Dealer / Channel
Partner.

**2. Duplicate phone and email are rejected.** The client asked for this
explicitly, and it is enforced in three places including a database
constraint. Formatting is ignored — `+91 98450 12345` collides with a
stored `+919845012345`, because only the last 10 digits are compared.

The 400 body carries **`existing_lead_id`**. Use it: show "This number
already belongs to lead #42 — open it?" with a link, rather than a bare
error. That field exists specifically so the UI can do this.

---

## 5. Pipeline (Kanban) — 7 stages

```http
GET /api/v1/leads/by-stage
```

```jsonc
{
  "stages": ["created", "contacted", "not_interested",
             "interested", "quoted", "won", "lost"],
  "pipeline": "normal",
  "items_by_stage": { "created": [ /* LeadCardOut */ ], ... },
  "counts_by_stage": { "created": 12, ... },
  "total": 40
}
```

> **Render columns from the `stages` array. Do not hard-code them.**
> Three brands share this component and the lists differ (Iconiq 7,
> FMC 11, Admitverse 19). Hard-coding is how the board silently drifts
> from the backend — and a lead sitting in a stage with no column is
> invisible, which has already happened once on FMC to 1,575 leads.

Useful query params: `q` (name/phone/email search), `agent_id`,
`source_id`, `created_from` / `created_to`, `due_from` / `due_to`,
`tags`, `per_stage_limit` (default 50, max 200).

### Moving a card

```http
POST /api/v1/leads/{lead_id}/stage
{ "to_stage": "quoted", "due_date": "2026-09-20T10:00:00Z" }
```

Three rules the UI must respect, or drags will fail:

**a. A follow-up `due_date` is REQUIRED on every move to a non-terminal
stage.** This is existing behaviour on all three brands. Dragging a card
must open a small dialog asking for the next follow-up date — you cannot
just fire the request.

**b. `lost` requires `lost_reason`, from a fixed list.** Fetch it from
`GET /api/v1/leads/lost-reasons` (6 values for Iconiq). Free text is
rejected. Render a dropdown, never an input.

**c. `won` and `lost` are terminal — nothing moves out of them.** Show
those columns as drop-targets but not drag-sources. Reopening from
`lost` is admin-only and goes through a different path.

**`not_interested` is deliberately NOT terminal.** In this market it
means "not this quarter" — a factory that has just bought a DG set is a
live lead again in eighteen months. Cards must drag freely out of it.

### Card contents

`LeadCardOut` is a slim projection: `id`, `serial_no`, `full_name`,
`phone`, `email`, `current_stage`, `assigned_agent_id`, `due_date`.
Show `#{serial_no}` on the tile — the team refers to leads by that
number. For a B2B board, `organization` is worth showing too; ask the
backend team to add it to the card projection rather than fetching each
lead.

---

## 6. Calls — manual logging only

There is **no AI voice agent** on Iconiq. This page is a rep recording
what happened on a call they made themselves. No recordings, no
transcripts, no sentiment — those fields exist in the response but will
always be null. Don't render them.

```http
GET  /api/v1/leads/{lead_id}/calls      # history for one lead
POST /api/v1/leads/{lead_id}/calls      # log a call
GET  /api/v1/calls                      # all calls, filterable
```

Log body:

```jsonc
{
  "disposition": "connected",
  "conversation_notes": "Wants 250 kW / 4h. Sending quote Friday.",
  "agent_agenda": "Send BESS quote",
  "due_date_for_next": "2026-09-12T10:00:00Z"
}
```

`disposition` is one of: `connected`, `dnp`, `busy`, `switched_off`,
`wrong_number`, `callback`.

`conversation_notes` and `agent_agenda` are both **required** — the
backend rejects empty strings. Label them plainly ("What happened" /
"Next step").

> **DNP auto-lost:** logging repeated `dnp` calls increments a counter.
> At 5 attempts the backend warns; at 6 it moves the lead to `lost`
> automatically. Surface the count on the lead so a rep isn't surprised
> when a lead disappears off their board.

---

## 7. The other pages

**Tasks** — `GET /tasks`, `/tasks/today`, `/tasks/overdue`,
`/tasks/completed-today`, `/tasks/count` (badge), `POST /tasks`,
`POST /tasks/{id}/complete`. Tasks are auto-created when a stage move
sets a follow-up date, so most will appear without anyone typing them.

**Notifications** — `GET /notifications`, `/notifications/unread-count`,
`PUT /notifications/{id}/read`, `PUT /notifications/read-all`.
`unread-count` is deliberately fail-soft: on a slow database it returns
`{"count": 0}` rather than erroring. Never block page render on it.

**Website Leads (admin)** — the review inbox for the iconiqenergy.in
contact form. `GET /website-leads` (filter by `status`),
`/website-leads/count`, `/website-leads/forms`, then
`POST /website-leads/{id}/convert` | `/spam` | `/reopen`.
Statuses: `new` · `converted` · `duplicate` · `spam`. Submissions land
here for triage rather than straight into the pipeline, because public
forms collect junk. A convert that hits an existing lead returns **409**
and marks the submission `duplicate` — show the linked lead, don't show
a failure.

> Not wired yet: Iconiq's website still has to POST its form to the CRM.
> Until then this page will legitimately be empty.

**Users (admin)** — `GET /users`, `POST /auth/register`,
`PUT /users/{id}`, `DELETE /users/{id}` (deactivate),
`GET /users/{id}/stats`.

**CSV History (admin)** — `GET /csv/template`, `POST /csv/upload`,
`POST /csv/{id}/preview` (returns suggested column mapping),
`POST /csv/{id}/process`, `GET /csv/history`, `GET /csv/{id}/status`.
The importer recognises Iconiq's headers — "Organization", "Company",
"Application", "Industry", "Load", "PCS sizing", "Backup", "Solar",
"DG capacity" and more. Show the preview mapping and let the user
correct it: bare "kW" and "capacity" are deliberately left unmapped
because they're ambiguous between load and solar.
**Hard limit 5,000 rows** — a bigger file is rejected outright, not
silently truncated. Surface that message.

---

## 8. Read brand config from the API — never hard-code

These endpoints return **empty arrays** for Iconiq. That emptiness is
the signal to hide the widget entirely, not to render an empty dropdown:

| Endpoint | Iconiq |
|---|---|
| `GET /leads/industries` | **11 options** — Iconiq only |
| `GET /leads/lost-reasons` | **6** |
| `GET /leads/sources/list` | **7** |
| `GET /leads/banks` | `[]` |
| `GET /leads/bank-statuses` | `[]` |
| `GET /leads/universities` | `[]` |
| `GET /leads/docs/checklist` | `[]` |

Bank-share, application, disbursement and commission endpoints return
**400** for Iconiq with a clear message. Don't call them.

---

## 9. Traps, in one list

1. **CORS.** Send the Vercel URL to the backend or nothing works.
2. **`lead_source_id` is required** on create — 400 without it.
3. **`due_date` is required** on every non-terminal stage move.
4. **`lost_reason` must come from the API list** — free text is rejected.
5. **Won and Lost are terminal.** No dragging out.
6. **Render Kanban columns from `stages`**, never a hard-coded list.
7. **Duplicate phone/email are rejected** — use `existing_lead_id` to
   link to the lead that already exists.
8. **kVA vs kW** — DG is kVA, load and solar are kW.
9. `NEXT_PUBLIC_APP_NAME` — set it correctly.
10. **No reports page.** It is coming later; `won_time` and `lost_time`
    are already being recorded so nothing needs backfilling.

---

## 10. Two things still provisional

The **6 lost reasons** and the **11 industry options** are the backend
team's suggestions and have not been confirmed by Iconiq yet. Both are
served from the API, so when they change, your UI picks them up with no
frontend release — **provided you fetch them rather than hard-coding.**

---

## Reference

- Backend scope, schema and deployment: `docs/ICONIQ_CRM_BUILD_PLAN.md`
- Full backend map: `docs/ARCHITECTURE.md`
- Website form contract: `docs/WEBSITE_LEADS.md`
