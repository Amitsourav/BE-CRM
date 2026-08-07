# WhatsApp → CRM ingest — handoff for the bot repo

> Give this whole file to the agent/dev working in the **WhatsApp bot** repo.
> No CRM-side changes are needed — everything described here is live.
>
> Written 2026-08-05 against BE-CRM `main` @ `29966df`.

---

## 1. What you are building

A service that reads messages from a WhatsApp group, extracts a lead, and
writes it into the FundMyCampus CRM — assigned to a specific employee, at
stage `created`. When people later reply with more detail, it updates that
same lead and appends notes.

```
WhatsApp group message
        │
        ├─ new enquiry      → POST /leads              → store the returned id
        └─ reply on a lead  → PUT  /leads/{id}         (patch individual fields)
                            → POST /leads/{id}/remarks (append a note)
```

**You never delete anything.** The credential you are given is physically
incapable of it — see §4.

---

## 2. ⚠️ FundMyCampus only

One codebase serves two brands from two deployments:

| Brand | Backend | Your key |
|---|---|---|
| **FundMyCampus** | `https://be-crm-production.up.railway.app` | ✅ works |
| Admitverse | `https://pretty-insight-production.up.railway.app` | ❌ 401, always |

They have **separate databases**. Your key's hash exists only in FMC's, so
it cannot authenticate against Admitverse — and the AV deployment also runs
with `API_KEYS_ENABLED=false`, which refuses keys before any database
lookup and 404s the key-management surface entirely.

**Hard-code the FMC base URL. Never make it a runtime-switchable setting.**
If a request ever 401s unexpectedly, check you aren't pointed at AV before
assuming the key is bad.

---

## 3. Credentials

Ask Amit for these through a private channel — they are not in any repo.

```
CRM_BASE_URL   https://be-crm-production.up.railway.app/api/v1
CRM_API_KEY    crmk_live_…
```

Send the key as a header on every request:

```http
X-API-Key: crmk_live_…
```

Not `Authorization: Bearer`. That header is for human Supabase logins and
expires hourly; your key does not expire and is revoked independently of
any person's account.

The key authenticates as a **service account** (`whatsapp-ingest@…`,
role `admin`). Everything it writes is attributed to that account in the
CRM's audit trail, so your writes are always distinguishable from a
counsellor's.

**There is a sandbox tenant** for development — same base URL, different
key, isolated from live data by `company_id`. Use it while building. Ask
for the sandbox key rather than testing against real leads.

---

## 4. What the credential cannot do

| Action | Result |
|---|---|
| Any `DELETE` request | **403**, always, before the route even runs |
| `POST/GET /api-keys` | 403 — keys cannot mint or manage keys |
| Reach the Admitverse backend | 401 |
| Cross into another company's data | Impossible — `company_id` comes from the credential, never from your payload |

The DELETE block is global middleware, so it also covers soft-deletes
(`DELETE /leads/{id}` and `DELETE /users/{id}` are both soft) and any
DELETE route added in future.

---

## 5. Creating a lead

```http
POST /leads
X-API-Key: crmk_live_…
Content-Type: application/json
```

```json
{
  "full_name": "Rohit Verma",
  "phone": "09812345678",
  "email": "rohit@example.com",
  "city": "Pune",
  "target_degree": "MS Computer Science",
  "assigned_agent_id": "c6124358-94b3-4fa9-9ff5-831f2e3ff2a6",
  "custom_fields": {"wa_group": "FMC Leads Aug", "wa_message_id": "ABGH1234"},
  "notes": null
}
```

**`201` response — store `id`.** This is the only thing linking the
WhatsApp conversation to the CRM record.

```json
{
  "id": "0d9e3d24-be59-43ea-afff-754c36e0677c",
  "serial_no": 3,
  "full_name": "Rohit Verma",
  "phone": "+919812345678",
  "current_stage": "created",
  "assigned_agent_id": "c6124358-94b3-4fa9-9ff5-831f2e3ff2a6",
  "created_at": "2026-08-05T10:14:02.881000Z"
}
```
*(~60 fields in reality; abbreviated.)*

Three things that are already handled for you:

- **Stage is `created` automatically.** You cannot set it on create and
  do not need to.
- **Assignment happens in the same call** via `assigned_agent_id`. No
  second request.
- **The phone is normalised** — `09812345678` was stored as
  `+919812345678`.

### Do not send

`notes` — write to `POST /leads/{id}/remarks` instead. `lead.notes` is a
single column that `PUT` **replaces**, and the CRM's AI call pipeline
appends its call history to that same column. Writing it destroys that
history. This is the one field that will quietly cause real damage.

Unknown field names are **silently ignored** — a typo won't error, it just
won't save. Check payloads against `docs/LEAD_FIELD_REFERENCE.md`.

---

## 6. Duplicates — the important flow

Phone and email are **unique per tenant**. A duplicate create returns
`400`, not a success:

```json
{
  "detail": "A lead with phone +919812345678 already exists (Rohit Verma).",
  "error_code": "duplicate_lead",
  "duplicate_field": "phone",
  "existing_lead_id": "0d9e3d24-be59-43ea-afff-754c36e0677c",
  "existing_lead_name": "Rohit Verma"
}
```

**`existing_lead_id` is your recovery path.** Treat this 400 as
"already exists, here it is" and pivot to `PUT /leads/{id}`:

```python
r = post("/leads", json=payload)
if r.status_code == 201:
    lead_id = r.json()["id"]
elif r.status_code == 400 and r.json().get("error_code") == "duplicate_lead":
    lead_id = r.json()["existing_lead_id"]      # update this one instead
else:
    raise
```

Match on `error_code == "duplicate_lead"`, never on the `detail` text.

### There is no idempotency key

If a create times out, you cannot tell whether it committed. The
duplicate check is your safety net — retry, and a committed create comes
back as the 400 above with the id. **This only works if the lead has a
phone or an email.** A lead with neither has no uniqueness key and *will*
duplicate on retry. Never send a lead without at least one.

Phone normalisation only recognises Indian formats
(`0091…`, `91…`, `0…`, or 10 digits → `+91…`). Anything else — a foreign
number, an extension, `"98765 43210 call after 6"` — is stored verbatim
and will not dedupe. Normalise or reject client-side.

---

## 7. Updating a lead later

```http
PUT /leads/{id}
```

Despite the verb it is a **partial** update — only keys present in the body
are written.

```json
{"college_name": "Northeastern University", "loan_amount": "35"}
```

⚠️ **`loan_amount` on FMC must be a bare number meaning lakhs** — `"35"`,
not `"35 lakh"`. The backend accepts either and parses both correctly, but
the FMC lead-edit form puts this field behind a numeric-only input, and a
stored value containing letters becomes **completely uneditable** in the
UI — every keystroke including backspace is silently rejected and the
field appears frozen. This already happened twice in live data
("7.5 Lakh", "50 Lakh") and had to be corrected by hand.

Three traps:

- **`custom_fields` and `tags` are replace-not-merge.** Sending
  `{"custom_fields": {"a": 1}}` erases every other key, including the
  `ai_last_call` block the voice pipeline writes there. GET, merge
  locally, send the whole object — or don't write them at all.
- **Changing `current_stage` requires `due_date`** in the same request
  for any non-terminal stage, and `lost_reason` for `lost`. If you're
  only patching data fields, don't touch `current_stage`.

---

## 8. Appending notes

```http
POST /leads/{id}/remarks
{"body": "Replied on WhatsApp: budget 30L, targeting Fall 2026."}
```

Append-only, 1–5000 chars, timestamped. Attributed to the service
account — there is **no way to attribute a remark to the WhatsApp
sender**, so put their name inside `body` if it matters:

```json
{"body": "[from +919812345678 — Rohit] budget 30L, Fall 2026"}
```

---

## 9. Mapping employees

```http
GET /users
```

Returns a **bare array** (not paginated) of profiles in your company.

```json
[{
  "id": "c6124358-94b3-4fa9-9ff5-831f2e3ff2a6",
  "email": "ankit@fundmycampus.com",
  "full_name": "Ankit Sharma",
  "phone": "+919812345678",
  "role": "pre_counsellor",
  "is_active": true
}]
```

`id` is what goes in `assigned_agent_id`. **Cache it, refresh periodically
— do not hand-maintain a copy.**

⚠️ `phone` is optional and frequently `null` in live data. If you plan to
map employees by their WhatsApp number, check coverage first and fall back
to `full_name`/`email`. And `assigned_agent_id` is **not validated** — a
wrong UUID fails at the database FK as a **500**, not a clean 400. Only
ever send ids you got from this endpoint.

---

## 10. Finding an existing lead

There is **no exact-match lookup by phone**. Only:

```http
GET /leads/search?q=%2B919812345678
```

A substring `ILIKE` across name, email and phone, returning the paginated
envelope (`{items, total, page, page_size, total_pages}`). It can match
several leads, so check `total` and don't blindly take `items[0]`.

**Prefer the §6 duplicate flow** — attempt the create and read
`existing_lead_id` off the 400. It's exact, atomic, and one request.

---

## 11. Operational notes

- **Rate limits:** none on any `/leads` endpoint. The real constraint is
  latency — the database is in Supabase's Korea region and a request can
  take 2–20 seconds. Set generous timeouts (30s+), keep concurrency low,
  and don't interpret a slow response as a failure.
- **Retries:** safe for `PUT` and `GET`. For `POST /leads`, retry is safe
  *only* because of the duplicate check — handle the 400 as success.
  `POST /remarks` is **not** idempotent; a retry appends a second note.
- **Errors:** `401` bad/missing key · `403` forbidden (or a DELETE
  attempt) · `404` not found · `400` duplicate or validation ·
  `422` schema violation · `500` unexpected.
- **Over-length strings 500 instead of 422.** Truncate client-side:
  `loan_amount` 50, `bank_name` 100, `budget` 50, `primary_university` 200.
- **No webhooks.** Nothing tells you when a counsellor edits a lead in the
  CRM. If both sides can edit the same field, it's last-write-wins with no
  conflict detection.

---

## 12. Full field list

Every field, its type, whether it's writable on create vs update, and the
complete set of allowed values for all seven enums:

**`docs/LEAD_FIELD_REFERENCE.md`** in the BE-CRM repo.

Read it before designing the text→field mapping. Notes that catch people out:

- `current_stage` has **29** values (a stale comment in the code says 23).
  "Created" is `created`.
- `lost_reason` and `bank_name` are **locked lists** on FMC — exact,
  case-sensitive strings only. Fetch them live from `GET /leads/lost-reasons`
  and `GET /leads/banks`.
- `gender`, `city`, `state`, `stream`, `target_intake` and `tags` are free
  text with **no validation** — write what the message says.

API-key minting, rotation and failure modes: **`docs/API_KEYS.md`**.

---

## 13. Phase two — bank shares

When your team shares a lead into a lender's WhatsApp group, that *is*
the submission to that bank. These endpoints record it, keep the
conversation that follows, and feed the grid.

### Where it lives — read this first

It is **`lead_banks`**, the table the CRM already uses for a lead's
relationship with a bank — extended, not duplicated. That table was
already exactly one row per (lead, bank), enforced by
`uniq_lead_banks_lead_bank`. Four columns were added to it:
`shared_at`, `shared_by`, `source`, `wa_group_id`.

So a lead's PNB row is the *same row* whether a counsellor created it in
the UI or your bot recorded the share. There is one place recording which
bank a lead is with.

**`bank_status` is not yours.** It is the bank's decision (`applied` →
`sanctioned` → `disbursed` …) and these endpoints never write it. A
brand-new row takes the default `applied`; an existing row's status is
left exactly as the team set it.

### Record a share

```http
POST /leads/{lead_id}/bank-shares
{
  "bank_name": "PNB",
  "shared_by": "ca52fa93-e695-48d4-8803-d7715d53e6a3",
  "shared_at": "2026-08-07T09:15:00Z",
  "wa_group_id": "grp-pnb-001",
  "source": "whatsapp"
}
```

- `bank_name` **must** be one of the canonical list — **fetch it from
  `GET /leads/banks`, don't hard-code it.** It grows as new lender
  relationships start (Poonawalla was added 2026-08-07, taking it to 19).
  Spelling is exact and case-sensitive: `"Poonawalla"` has TWO Ls.
- `shared_by` is a `profile_id` from `GET /users`, and **is validated**;
  an unknown id returns 400 rather than 500ing at the FK.
- `shared_at` defaults to now if omitted.

**Idempotent on (lead, bank).** `201` when the share is new, `200` when
it already existed. Both are success — don't branch. A repeat keeps the
**original** `shared_at`, because the grid answers *"when did this file
first reach this bank"*. Log the re-share as a message instead.

### Append a message

```http
POST /leads/{lead_id}/bank-shares/{bank_name}/messages
{
  "body": "Docs received, login by Friday",
  "sender_phone": "+919812345678",
  "sender_name": "Ankit",
  "is_our_team": true,
  "wa_message_id": "wamid.HBgMOTE5..."
}
```

- **Idempotent on `wa_message_id`** — redelivery returns the stored row
  (`200`) instead of duplicating the thread. `201` when newly stored.
  Send it on every message; it is the only thing making this safe to retry.
- `is_our_team` is **yours to decide** — you know the team's numbers. The
  bank's staff will never have CRM profiles, which is why `sender_phone`
  is a plain string and not a foreign key.
- `404` if the lead hasn't been shared with that bank yet. Record the
  share first.

These messages are deliberately **not** `lead_remarks`. Remarks are the
lead's general internal timeline; this is the conversation about that
lead in that specific bank's group.

### Read

```http
GET /leads/{lead_id}/bank-shares              # every bank this lead went to
GET /leads/{lead_id}/bank-shares/{bank_name}  # one share + full conversation
GET /leads/bank-share-grid                    # the grid
```

The grid returns `banks` (column order) and one row per lead whose
`shares` maps bank_name → cell. **Banks absent from the map are blank
cells** — that is how the UI decides what to colour.

Each cell carries `shared_at`, `shared_by_name`, `source`, `bank_status`,
`message_count`, `last_message_at` and a 120-char `last_message_preview`
— enough to render and show a useful tooltip with no extra request. The
**full** conversation is loaded on hover from
`GET /leads/{id}/bank-shares/{bank}`; inlining every message for 25 leads
× 19 banks would dwarf the payload.

Filters: `page`, `page_size` (max 100), `q` (name/phone/email),
`current_stage`, `agent_id`, `bank_name` (only leads shared with it),
`shared_only`. Three queries regardless of page size.

### Still no deletes

There is no delete route for shares or messages, and your key would be
refused one anyway.
