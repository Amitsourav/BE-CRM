# Lead Field Reference — API contract

For integrations writing leads into the CRM over HTTP. Generated from a
direct read of `app/schemas/lead.py`, `app/models/lead.py`,
`app/core/constants.py` and `app/services/lead_service.py` on `main`
@ `23c0d72` (2026-08-05).

Base URL + prefix: `{base}/api/v1` — FMC `https://be-crm-production.up.railway.app`,
Admitverse `https://pretty-insight-production.up.railway.app`.

> **The create schema and the update schema accept different fields.** A
> field existing on the lead does not mean you may write it, and not
> every writable field comes back in the response. The three tables below
> are the authority; §5 lists what is off-limits entirely.

---

## 1. Fields accepted by `POST /leads`

Schema: `LeadCreate`. Unknown keys are **silently ignored** (no
`extra="forbid"`), so a typo'd field name fails quietly — check your
payload against this list rather than trusting a 201.

| API name | Type | Required | Notes |
|---|---|---|---|
| `full_name` | string | **yes** | No min-length check — `""` is accepted |
| `email` | string \| null | no | Plain string, **not** validated as an email. Unique per tenant (case-insensitive) |
| `phone` | string \| null | no | Normalised server-side (§4). Unique per tenant |
| `alternate_phone` | string \| null | no | Not normalised, not deduped |
| `date_of_birth` | string \| null | no | ISO date, `YYYY-MM-DD` |
| `gender` | string \| null | no | Free text — no enum |
| `city` | string \| null | no | Free text |
| `state` | string \| null | no | Free text |
| `country` | string \| null | no | Defaults to `"India"` when omitted |
| `pincode` | string \| null | no | Free text, no format check |
| `highest_qualification` | string \| null | no | Free text |
| `stream` | string \| null | no | Free text |
| `passing_year` | integer \| null | no | No range check |
| `college_name` | string \| null | no | Free text |
| `university` | string \| null | no | Free text |
| `percentage` | number \| null | no | Stored `NUMERIC(5,2)` — max 999.99, 2 dp |
| `target_degree` | string \| null | no | Free text |
| `target_intake` | string \| null | no | Free text, e.g. `"Sep-2026"` |
| `preferred_countries` | string[] \| null | no | |
| `preferred_universities` | string[] \| null | no | |
| `lead_source_id` | UUID \| null | no | Must exist in `lead_sources`. List via `GET /leads/sources/list` |
| `assigned_agent_id` | UUID \| null | no | Counsellor. **Not validated** — a bad UUID 500s at the FK |
| `custom_fields` | object \| null | no | JSONB. **Replace-not-merge on update** |
| `tags` | string[] \| null | no | **Replace-not-merge on update** |
| `notes` | string \| null | no | Single text column. See §5 — prefer remarks |

**Not accepted on create** (and silently dropped if sent):
`current_stage`, `pre_counsellor_id`, `due_date`, `is_important`,
`loan_amount`, `budget`, `bank_name`, `bank_status`, `docs_required`,
`docs_submitted`, `submitted_docs`, `dnp_count`.

A new lead always starts at stage **`created`** — you cannot set it, and
do not need to.

---

## 2. Fields accepted by `PUT /leads/{id}`

Schema: `LeadUpdate`. Despite the verb, this is a **partial** update —
the router applies `exclude_unset=True`, so only keys present in the
JSON body are written and omitted fields are untouched.

Everything from §1 **except `lead_source_id`**, plus:

| API name | Type | Notes |
|---|---|---|
| `current_stage` | string | Routed through the stage machine — see §3 for values and gates |
| `assigned_agent_id` | UUID \| null | Counsellor |
| `pre_counsellor_id` | UUID \| null | Pre-Counsellor (second assignment slot) |
| `due_date` | datetime \| null | ISO 8601. Setting it auto-creates a follow-up Task |
| `is_important` | boolean | Star flag; does not affect stage |
| `loan_amount` | string(50) | FMC. Free text — `"35 lakh"`, `"1.5cr"`. Parsed into `loan_amount_lakh` |
| `bank_name` | string(100) | FMC. **Locked list** — see §3 |
| `bank_status` | string | FMC. Enum — see §3 |
| `docs_required` | integer | Defaults 6 on FMC, 8 on Admitverse |
| `docs_submitted` | integer | **Overwritten** to `len(submitted_docs)` whenever `submitted_docs` is sent |
| `submitted_docs` | string[] | Checklist keys — see §3. Unknown keys are silently dropped |
| `dnp_count` | integer | Requires a non-empty `conversation_notes` in the same body, else 400 |
| `budget` | string(50) | Admitverse. Free text — `"£18,000"`. Parsed into `budget_amount` + `budget_currency` |
| `conversation_notes` | string | Only meaningful alongside `current_stage` or `dnp_count`; written as a remark |
| `agent_agenda` | string | Only meaningful alongside `current_stage` |
| `lost_reason` | string | **Required** when moving to `lost`. FMC validates against the locked list in §3 |

---

## 3. Every enum, in full

### `current_stage`

Postgres enum `lead_stage`. **29 values.** (The comment at
`constants.py:128` and `docs/ARCHITECTURE.md` both say "23" — that is
wrong; `len(LEAD_STAGE_VALUES)` is 29.)

Complete set, in DB order:

```
lead                    called                  connected
qualified_lead          won                     lost
created                 contacted               dnp_pre_qualified
qualified               opportunity             dnp_post_qualified
processing              important               partial_docs_collected
docs_collected          application_done        conditional_draft
ucol                    deposit_paid            cas_received
visa_applied            enrolled                dnp
docs_pending            logged_in               sanctioned
pf_paid                 disbursed
```

The enum is shared by both brands; only a subset is reachable on each.

**FundMyCampus** (11) — `created` · `contacted` · `dnp` · `qualified` ·
`processing` · `logged_in` · `sanctioned` · `pf_paid` · `disbursed` ·
`opportunity` · `lost`. Terminal: `disbursed`, `lost`.

**Admitverse** (19) — `created` · `contacted` · `dnp_pre_qualified` ·
`connected` · `qualified` · `opportunity` · `dnp_post_qualified` ·
`processing` · `important` · `partial_docs_collected` ·
`docs_collected` · `application_done` · `conditional_draft` · `ucol` ·
`deposit_paid` · `cas_received` · `visa_applied` · `enrolled` · `lost`.
Terminal: `enrolled`, `lost`.

`lead` / `called` / `connected` / `qualified_lead` / `won` are legacy —
retained for old rows, not reachable on FMC.

**The value meaning "Created" is `created`**, on both brands.

**Gates when changing stage** (all 400 on failure):
- the transition must be legal for the brand — both allow any
  non-terminal → any non-terminal; terminals are one-way
- moving to `lost` requires `lost_reason`
- moving to **any non-terminal** stage requires `due_date` in the same request

### `lost_reason` — FMC locked list (21)

Live copy: `GET /leads/lost-reasons`. Admitverse returns `[]` and accepts free text.

```
Future Plans
Not responding
Not Interested
Not reachable / Out of service / Wrong number
Plan Dropped
Repeat lead
Self funding
Indian University
Junk Lead
Loan already secured
Low loan amount
Wrong Product (Personal/Business Loan)
Country not approved/ Courses not approved.
Location Not Serviceable
No collateral
No Cosigner
Student profile not eligible
Cosigner - Ineligible
Lost to competitor
Already applied to multiple banks
Visa Reject
```

Match is exact and case-sensitive, including the trailing period on
`Country not approved/ Courses not approved.`

### `bank_name` — FMC locked list (18)

Live copy: `GET /leads/banks`. Anything else → 400. Admitverse returns `[]`.

```
Axis · PNB · SBI · Yes Bank · ICICI · IDFC · BOI · Kuhoo · Avanse
Credila · Propelld · Tata Capital · Zolve · Nomad · UniCred · Auxilo
Incred · Edgro
```

### `bank_status` — FMC (7)

```
applied · docs_reviewed · under_review · loan_login · sanctioned · pf_paid · disbursed
```

### `application_status` — Admitverse (12)

Read-only on the lead (synced from `lead_applications`); writable on the
per-application endpoints.

```
applied · shortlisted · offer_received · conditional_offer
unconditional_offer · deposit_paid · cas_received · visa_applied
visa_approved · enrolled · rejected · withdrawn
```

### `submitted_docs` — checklist keys

Live copy: `GET /leads/docs/checklist`. Values outside the brand's set
are **silently dropped**, not rejected.

- **FMC (6):** `aadhaar` · `pan` · `academic` · `offer_letter` · `financial` · `itr`
- **Admitverse (8):** `passport` · `academic` · `degree` · `english_test` · `sop` · `lor` · `cv` · `financial`

### `visa_status` — Admitverse, on applications only

```
not_started · applied · approved · rejected
```

### Fields that look like enums but are **not**

`gender`, `state`, `country`, `stream`, `highest_qualification`,
`target_degree`, `target_intake`, `tags` — all free text, no validation.
`GET /leads/universities` returns an autocomplete *suggestion* list for
Admitverse; `university_name` is not constrained to it.

---

## 4. Validation and constraints

**Uniqueness** — `phone` and `lower(email)` are unique **per company,
among non-deleted leads**, enforced in the service and by partial unique
indexes (`uniq_leads_phone_active`, `uniq_leads_email_active`).

A duplicate create returns **400** with the existing lead's id:

```json
{
  "detail": "A lead with phone +919812345678 already exists (Rohit Verma).",
  "error_code": "duplicate_lead",
  "duplicate_field": "phone",
  "existing_lead_id": "c964c6c3-3df1-4774-b596-35e6965e25d6",
  "existing_lead_name": "Rohit Verma"
}
```

`duplicate_field` is `"phone"` or `"email"`. Soft-deleting a lead frees
its phone and email for reuse.

**Phone normalisation** (`app/utils/csv_parser.py:99`) — applied on
**create only, not on update**. Digits are extracted, then:

| Input shape | Result |
|---|---|
| `0091XXXXXXXXXX` | → `+91XXXXXXXXXX` |
| `91XXXXXXXXXX` | → `+91XXXXXXXXXX` |
| `0XXXXXXXXXX` | → `+91XXXXXXXXXX` |
| `XXXXXXXXXX` (10 digits) | → `+91XXXXXXXXXX` |
| anything else | **returned unchanged**, `.strip()`ed only |

So non-Indian numbers, numbers with extensions, and free text
(`"98765 43210 call after 6"`) are stored verbatim and will not dedupe
against their normalised form. Normalise client-side if you ever patch a
phone via `PUT`.

**String lengths enforced only at the DB layer** — the Pydantic schemas
don't mirror them, so over-length input surfaces as a **500**, not a 422.
Truncate client-side: `loan_amount` 50 · `budget` 50 · `bank_name` 100 ·
`primary_university` 200 · `budget_currency` 3.

**Remark body** is properly validated: 1–5000 chars, clean 422.

---

## 5. System-managed — never write these

| Field | Managed by |
|---|---|
| `id` | `gen_random_uuid()` |
| `company_id` | Derived from the caller's credential. Cannot be set by any client |
| `serial_no` | Auto-reserved per tenant on create (`#1`, `#2`, …) |
| `current_stage` | `created` on create; afterwards only via the stage machine |
| `created_by` | The authenticated principal |
| `loan_amount_lakh` | Parsed from `loan_amount` |
| `budget_amount`, `budget_currency` | Parsed from `budget` |
| `bank_name`, `bank_status` | Re-synced from `lead_banks` when that table is used |
| `primary_university`, `application_status` | Re-synced from `lead_applications` |
| `docs_submitted` | Recomputed whenever `submitted_docs` is written |
| `call_attempt_count`, `dnp_count`, `connected_time`, `won_time`, `lost_time`, `last_contacted_at` | Stage machine / call pipeline |
| `is_deleted`, `deleted_at` | Soft delete |
| `csv_import_id`, `last_call_provider`, `last_call_recording_url` | Not on any write schema |

### `notes` — leave it alone

`lead.notes` is a single text column, and `PUT /leads/{id}` **replaces**
it. The AI post-call pipeline **appends** to that same column
(`app/api/v1/voice.py:1308`), so on any AI-called lead it holds
accumulated call history — writing it destroys that history.

Use the remarks table instead:

```
POST /leads/{lead_id}/remarks     {"body": "..."}      → 201
GET  /leads/{lead_id}/remarks                          → newest first
```

Append-only (no update, no delete endpoint), validated, timestamped, and
attributed to the authenticated principal. There is no way to attribute
a remark to a third party — record the original sender inside `body`.

---

## 6. Response-only fields

Present in `LeadOut`, never accepted as input: `serial_no`,
`call_attempt_count`, `connected_time`, `won_time`, `lost_time`,
`bank_count`, `top_banks`, `application_count`, `top_applications`,
`latest_note`, `assigned_agent_name`, `assigned_agent_role`,
`pre_counsellor_name`, `source_name`, `task_count`, `call_count`,
`notes_count`, `has_active_ai_campaign`, `budget_amount`,
`budget_currency`, `primary_university`, `created_by`, `created_at`,
`updated_at`.

**`loan_amount_lakh` is written but never returned** — it is not a field
on `LeadOut`, unlike its Admitverse counterpart `budget_amount` which is.
Read `loan_amount` (the free-text original) instead. The parsed value is
stored correctly; it is only the response schema that omits it.

---

## 7. Minimal working example

```bash
curl -X POST https://be-crm-production.up.railway.app/api/v1/leads \
  -H "X-API-Key: crmk_live_…" \
  -H "Content-Type: application/json" \
  -d '{
        "full_name": "Rohit Verma",
        "phone": "09812345678",
        "city": "Pune",
        "assigned_agent_id": "c6124358-94b3-4fa9-9ff5-831f2e3ff2a6"
      }'
```

→ `201`, phone stored as `+919812345678`, `current_stage` = `created`,
`serial_no` assigned, `assigned_agent_id` set — one call, no follow-up
needed.

```bash
# later, patch individual fields
curl -X PUT  …/api/v1/leads/{id} -H "X-API-Key: …" \
     -d '{"college_name": "Northeastern", "loan_amount": "35 lakh"}'

# and append a note
curl -X POST …/api/v1/leads/{id}/remarks -H "X-API-Key: …" \
     -d '{"body": "Replied on WhatsApp: budget 30L, Fall 2026."}'
```
