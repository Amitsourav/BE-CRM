# Iconiq Energy CRM — build plan

**Company:** Iconiq Energy (`iconiqenergy.in`) — manufacturer of hybrid
inverters (3–125 kW), Li-ion batteries (5–100 kWh) and complete BESS
(125 kWh – 5 MWh), from a 2.5 GWh battery plant. Sells to residential,
C&I and utility-scale customers.

**Decision:** Iconiq becomes the **third brand on this codebase**, with
its **own Supabase project** (own `auth.users`, own Postgres), its own
Railway service and its own Vercel frontend. Same pattern as
FundMyCampus (`slug = "default"`) and Admitverse (`slug = "admitverse"`).
Iconiq's slug is **`iconiq`**.

Nothing is shared with FMC or Admitverse — not logins, not leads, not
storage. A third database is the cheapest way to get that guarantee.

---

## 1. Scope

### In — eight pages, all of which already exist

| Page | Backend module | Status |
|---|---|---|
| Leads | `app/api/v1/leads.py`, `services/lead_service.py` | exists |
| Calls | `app/api/v1/call_attempts.py`, `services/call_service.py` | exists |
| Pipeline (Kanban) | `GET /leads/by-stage`, `services/stage_machine.py` | exists |
| Tasks | `app/api/v1/tasks.py` | exists |
| Notifications | `app/api/v1/notifications.py` | exists |
| **Admin** — Website Leads | `app/api/v1/website_leads.py` | exists |
| **Admin** — Users | `app/api/v1/users.py` | exists |
| **Admin** — CSV History | `app/api/v1/csv_import.py` | exists |

**This is a configure-and-deploy job, not a build job.** No new
subsystem is required. The backend work is a migration, one constants
file, and brand-gating.

### Out — explicitly not built

- AI voice agent, campaigns, Plivo / Sarvam / Smallest / OpenRouter / Bolna
- Commission, lender files, tranches, payout sharing, reconciliation dashboard
- Invoicing and GST
- Admitverse applications, universities, visa
- **Reports / dashboard — deferred by Amit, to be added later**

Every AI and telephony setting in `app/config.py` defaults to an empty
string, so leaving them unset on the Iconiq service switches the whole
voice stack off. **No code deletion is needed to strip it.**

---

## 2. Lead capture fields

Eleven fields required. Three already exist on `leads`; **phone is
required too** (see §5), and it exists. So **eight new columns**:

| # | Field | Column | Type | Notes |
|---|---|---|---|---|
| 1 | Name | `full_name` | text | exists |
| 2 | Phone | `phone` | text | exists — **unique, see §5** |
| 3 | Email | `email` | text | exists |
| 4 | Organization | `organization` | text | **new** |
| 5 | Website | `website` | text | **new** |
| 6 | City of project | `city` | text | exists |
| 7 | Application (industry) | `application_industry` | text | **new**, dropdown |
| 8 | Load / Capacity (PCS sizing) | `load_capacity_kw` | numeric(10,2) | **new** |
| 9 | Backup required (duration) | `backup_duration_hours` | numeric(6,2) | **new** |
| 10 | Solar present | `solar_present` | boolean | **new**, Y/N |
| 11 | Solar plant capacity | `solar_capacity_kw` | numeric(10,2) | **new**, nullable |
| 12 | DG available | `dg_available` | boolean | **new**, Y/N |
| 13 | DG capacity | `dg_capacity_kva` | numeric(10,2) | **new**, nullable |

**Conditional fields are frontend-only.** `solar_capacity_kw` shows when
Solar Present = Yes; `dg_capacity_kva` shows when DG Available = Yes.
The backend simply stores null when the answer is No — no validation
coupling, so a lead can be saved half-filled and completed later.

These go on the `leads` table as real columns, matching the existing
precedent: `leads` already carries FMC's loan tile (`loan_amount`,
`bank_name`, `dnp_count`) and Admitverse's tile (`budget`,
`primary_university`) side by side. Real columns — not `custom_fields`
JSONB — because CSV import, list filters and search only see columns.

**Still to confirm:** the dropdown values for *Application (industry)*.
Suggested starting list, to be corrected by Iconiq: Manufacturing,
Hospital, Hotel, Data Centre, Commercial Building, Warehouse, Telecom,
EV Charging, Utility / Grid, Residential, Other.

---

## 3. Pipeline stages

```
Created ─→ Contacted ─→ Not Interested          (revivable)
                     └→ Interested ─→ Quoted ─→ Won    (terminal)
                                             └→ Lost   (terminal)
```

Existing enum values reused: `created`, `contacted`, `lost` — **and
`won`, which was already in the type** (it is one of the six original
values from before the FMC/AV pipelines were built, and no brand uses
it today). So only **three new values** are needed:
`not_interested`, `interested`, `quoted`.

- **Terminal stages: Won and Lost only.** *Not Interested* stays
  non-terminal so a rep can revive a "not now" lead without an admin.
- **Won** stamps `won_time`, **Lost** stamps `lost_time` — both columns
  already exist, so a future report needs no schema work.
- **Lost reason is mandatory** and validated against a fixed list, the
  way FMC works. A locked list — not free text — is what makes the
  deferred reports comparable across reps later.

Proposed lost reasons, **awaiting Amit's correction**:

1. Price / too expensive
2. Competitor won
3. Project shelved or deferred
4. No budget
5. Went with DG instead
6. No response after quote

> ⚠️ **For the frontend:** the stage machine requires a **follow-up date
> on every non-terminal stage move**. This is existing behaviour on both
> live brands. The Kanban must prompt for a date when a card is dragged,
> or the move is rejected with a 400.

---

## 4. Backend changes

### 4.1 `app/core/constants.py`

Add `ICONIQ_STAGES`, `ICONIQ_TERMINAL`, `ICONIQ_LOST_REASONS` and a
generated transition table, then one `elif slug == "iconiq"` branch in
each of the **eight** brand-dispatch functions that today only branch on
`"admitverse"`:

```
get_transitions_for_brand      :359
get_terminal_stages_for_brand  :370
get_notes_required_for_brand   :394
get_lost_reasons_for_brand     :400
get_initial_stage_for_brand    :411
get_doc_checklist_for_brand    :453
get_doc_keys_for_brand         :459
get_universities_for_brand     :528
```

The last three return empty for Iconiq — no document checklist, no
universities. Transitions follow the existing model: free movement
between any two non-terminal stages, admin-only reopen from Lost.

### 4.2 One Alembic migration

- `ALTER TYPE lead_stage ADD VALUE` × 4 — **must run in an
  `autocommit_block()`**; `ALTER TYPE` cannot run inside a transaction.
  Precedent: migration `e5f6a7b8c9d0` did exactly this for the
  `website` lead-source type.
- `ALTER TABLE leads ADD COLUMN` × 8, all nullable.

Both are additive, so FMC and Admitverse are unaffected — they simply
gain eight null columns and four enum values they never use.

### 4.3 Brand gating

Hide what Iconiq does not get, using the `_require_fmc` pattern already
in `app/api/v1/reconciliation.py`. The API must return 404/403 rather
than an empty 200, so the frontend never renders a dead tab.

---

## 5. Duplicate phone numbers — already enforced

Amit's requirement: **no duplicate phone numbers.** This is already
enforced at three layers and needs no new work:

1. **On create** — `LeadService.create_lead` normalises to `+91…` and
   rejects a duplicate with a 400 carrying `existing_lead_id`, so the UI
   can link straight to the lead that already exists.
2. **On update and CSV import** — comparison is on the **last 10 digits**,
   backed by a functional index. This matters: an earlier bug compared a
   normalised incoming value against the raw stored column, so `+9170…`
   sailed past an existing `70…`. Fixed Aug 2026.
3. **In the database** — a unique partial index on
   `(company_id, phone) WHERE NOT is_deleted` (migration `r5o6p7q8r9s0`)
   as the backstop.

Email duplicates are also rejected, case-insensitively.

> **One edge case worth knowing:** Iconiq sells B2B, so two genuine
> contacts at the same company may share one switchboard number. A hard
> unique rule blocks the second. If that turns out to matter in practice,
> the fix is to dedup on `(phone, organization)` — but start strict, and
> loosen only if it actually bites.

---

## 6. Deployment

Proven twice; the hard parts are already solved in code.

1. **New Supabase project** — gives Iconiq its own `auth.users` and
   Postgres. `app/db/bootstrap.py` detects a fresh database and runs
   `CREATE EXTENSION pgcrypto`, creates the 8 ENUM types, then
   `Base.metadata.create_all` + `alembic stamp head` (the empty-baseline
   migration does not create tables — this is why that code exists).
2. **New Railway service** off `Amitsourav/BE-CRM`, same repo as the
   other two. Nine environment variables:
   `APP_ENV`, `SECRET_KEY`, `CORS_ORIGINS`, `BACKEND_URL`,
   `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`,
   `SUPABASE_JWT_SECRET`, `SUPABASE_DB_URL`.
   Plus `WEBSITE_LEAD_SECRET` (§7). Everything AI-related stays unset.
3. **Seed** — `scripts/seed_admin.py` creates the company (slug
   `iconiq`, auto-derived) and the first admin.
4. **New Vercel frontend** from CRM-UI with the sidebar trimmed to the
   eight pages.

> ⚠️ **Railway deploy trap.** On 2026-09-07 the workspace trial expired
> and **both** existing deployments silently stopped building for two
> days — auto-deploy stayed "enabled" and Railway said nothing, while
> running containers kept serving. A third service must not be added
> without an active plan. Verify a deploy landed by diffing
> `curl <backend>/openapi.json` against local, not by trusting the UI.

---

## 7. Website form → Website Leads inbox

Website Leads is on the admin list, and its source is the contact form
on **iconiqenergy.in**. Submissions land in `website_submissions` with
status `new` for human triage — deliberately **not** straight into the
pipeline, because public forms collect junk. A manager then converts to
a lead, or marks it spam.

The site must POST to
`POST /api/v1/internal/website/ingest` with header
`X-Internal-Secret: <WEBSITE_LEAD_SECRET>`. Unknown fields can be sent
freely — they land in the `payload` JSONB, so the form can add fields
with no backend deploy. Contract, field list and a Next.js snippet are
in **`docs/WEBSITE_LEADS.md`**.

**This is the only task that needs someone on Iconiq's side** — likely
Pallavi Singh (Digital & Marketing). Raise it early; it is the kind of
dependency that is otherwise discovered in the last week.

---

## 8. Sequence and estimate

| Step | Work | Owner | Est. |
|---|---|---|---|
| 1 | Supabase project + Railway service + env vars + seed admin | backend | 1 day |
| 2 | Constants: stages, transitions, lost reasons, 8 brand branches | backend | 0.5 day |
| 3 | Migration: 4 enum values + 8 columns | backend | 0.5 day |
| 4 | Brand-gate the excluded modules | backend | 0.5 day |
| 5 | Lead schema/API: expose the 8 new fields + CSV header aliases | backend | 1 day |
| 6 | Frontend: third deployment, 8 pages, new lead form, Kanban | **frontend** | the long pole |
| 7 | Website form wiring | **Iconiq** | small, but external |

**Backend is roughly 3–4 days.** The critical path is the frontend and
the form wiring, neither of which is backend work.

---

## 9. Open items

1. **Lost reason list** — 6 proposed in §3, awaiting Amit's correction.
2. **Application (industry) dropdown values** — 11 proposed in §2,
   awaiting Iconiq.
3. **Who logs in, and with what roles.** Role hierarchy is fixed at
   admin > manager > pre_counsellor. "pre_counsellor" is FMC vocabulary
   and will read oddly to a solar sales team; renaming the label for
   this brand is cosmetic but should be decided before users are seeded.
4. **Reports** — deferred by Amit. `won_time`, `lost_time` and
   `lost_reason` are being captured from day one specifically so the
   deferred reports need no backfill later.
5. **Meta Ads ingest** not in scope. If Iconiq runs lead ads later, FMC
   is the single Meta gateway and would forward to Iconiq the same way
   it forwards to Admitverse — a small addition, not a rebuild.

---

---

## 10. Status — database is live (2026-09-09)

**Supabase project created and fully bootstrapped.** Nothing else in
this plan has been built yet.

| | |
|---|---|
| Project ref | `mwsvkuqzeyqiwkieozdl` |
| Name | `iconiq-crm` |
| Region | **`ap-south-1` (Mumbai)** |
| API URL | `https://mwsvkuqzeyqiwkieozdl.supabase.co` |
| Org | `Number track for calling` (`vxkmmalrezqptrhhoxpy`) |
| Cost | $0/month |

**Mumbai, not Korea.** FMC and Admitverse both sit in `ap-northeast-2`,
and that latency is why the 30s profile cache exists, why DB-backed
pytest is unusable, and why the dedupe job died mid-run. Iconiq shares
no data with them, so there was no reason to inherit it. Region cannot
be changed later, which is why it was worth deciding up front.

Note this is a **different Supabase account** from the one holding FMC
(`yytjlkrequxyqupxrlpx`) and Admitverse (`mhlfbkjpyrabpeoyqsbs`).
Deliberate, and confirmed — Iconiq is a separate company.

### What is in the database

26 tables (24 from the models, plus `company_lead_counters` and
`alembic_version`), 12 ENUM types, 70 indexes, stamped at Alembic head
`w3x4y5z6a7b8` so the backend's startup step takes the "existing DB"
path and does nothing.

### Verified by smoke test, not assumed

| Check | Result |
|---|---|
| A new lead defaults to stage | `created` |
| Duplicate phone | blocked |
| Duplicate email, different case | blocked |
| A different phone | still accepted |
| `created` / `contacted` / `won` / `lost` are valid enum values | all valid |
| Test rows cleaned up | 0 left |

### Three bugs found and fixed while doing it

**1. `app/db/bootstrap.py` would have produced a broken database.**
Its `ENUM_TYPES` map was a hand-maintained copy that had rotted. It
declared `lead_stage` as the original **6** values while `LeadStage` had
grown to **29**, and it was missing four types outright (`bank_status`,
`pf_status_enum`, `application_status`, `visa_status_enum`). On a fresh
database that fails twice over: `create_all` cannot build
`lead_banks` / `lead_applications` / `leads` without the missing types,
and even past that, `alembic stamp head` marks every value-adding
migration as already applied, so the 23 absent stage values never
arrive and the **first lead insert fails**. Admitverse escaped this in
May 2026 only because the list happened to be current that day.
**Fixed** — the map is now derived from the enum classes in
`constants.py`, so it cannot drift again.

**2. `create_all` silently skips migration-only objects.** Several
things exist only in Alembic migrations and have no model, so they were
absent: the `company_lead_counters` table (lead serial numbers — the
same gap that left 1,578 FMC leads with no serial), the
`uniq_leads_phone_active` / `uniq_leads_email_active` unique indexes,
the `ix_leads_phone_dedupe_key` last-10-digit index, and the search
indexes. **Applied manually here.** Worth folding into `bootstrap.py`
before a fourth brand is ever stood up.

**3. `leads.current_stage` defaulted to `'lead'`** — the dead legacy
stage with no Kanban column and no valid transitions, which stranded
1,575 FMC leads. Changed to `'created'` on this database.

### ⚠️ One open security finding

**RLS is disabled on all 26 tables.** The backend connects as the
service role and enforces tenancy in application code, so the API is
fine — but the **anon key is held by the frontend**, and with RLS off
and default Supabase grants, that key can read and write every table
directly through PostgREST.

This is **not new** — FMC and Admitverse have exactly the same posture,
and it is already logged as a critical frontend item. Iconiq is the one
chance to start clean, and the fix is cheap because this stack never
uses PostgREST at all: revoke the `anon` and `authenticated` grants on
`public`, leaving Supabase Auth (which lives in the `auth` schema)
untouched.

**Not done, deliberately.** It should be applied and then confirmed
against the frontend during the CRM-UI build, since the only real risk
is a frontend path that talks to PostgREST directly. Decide it then —
but do not forget it.

### Credentials

`SUPABASE_URL` and `SUPABASE_ANON_KEY` are recoverable from the
dashboard or the Supabase MCP. The three that are **not** and must be
copied from **Project Settings → API / Database**:

- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_JWT_SECRET`
- `SUPABASE_DB_URL` (the **pooler** URL, matching the other two
  deployments — note the password is set in the dashboard)

Keep them out of git, the way the existing services do.

---

## 11. Backend built (2026-09-09)

Steps 2–5 of §8 are done and applied to the Iconiq database. What
remains is the frontend and the website form.

| Change | Where |
|---|---|
| Iconiq stages, transitions, terminals, lost reasons, industries | `app/core/constants.py` |
| 3 new enum values + 9 nullable columns | `alembic/versions/x5y6z7a8b9c0_…py` |
| The 9 capture fields on the model | `app/models/lead.py` |
| Exposed on create / update / detail / Kanban card | `app/schemas/lead.py` |
| CSV header aliases for all 9 | `app/utils/csv_parser.py` |
| `GET /leads/industries` dropdown | `app/api/v1/leads.py` |
| Lender features gated off | `constants.brand_has_lender_features` + 5 call sites |
| `won` as the Iconiq report stage | `app/services/report_service.py` |
| 18 brand tests, DB-free | `tests/test_iconiq_brand.py` |

Migration is applied: Iconiq is at `x5y6z7a8b9c0`, 32 stage values, all
9 columns present.

### The bug this surfaced

Every brand gate in the codebase asked **"is this Admitverse?"** rather
than "is this FundMyCampus?". That is fine with two brands and wrong
with three: on the day a third appeared, Iconiq silently inherited the
bank dropdowns, `bank_name` validation, the bank-share grid and the
commission reconciliation API. Replaced with a single rule,
`brand_has_lender_features(slug)`, which is a **deny-list** — so an
unknown slug still falls back to FMC exactly as it does everywhere else,
and a fourth brand cannot repeat the mistake by accident.

`report_service._BRAND_WON_STAGE` had the same shape of fault: with no
Iconiq entry it fell through to FMC's `disbursed`, a stage Iconiq's
board does not contain, so every conversion figure would have read zero
the moment reports were switched on.

### Verified against the live database, not asserted

A lead was created through `LeadService` and walked through the real
`StageMachine`:

| Check | Result |
|---|---|
| Lead created | `#1`, stage `created` |
| All 9 Iconiq fields stored and read back | yes |
| created → contacted → interested → quoted | reached `quoted` |
| Lost with no reason | refused |
| Lost with an FMC-only reason ("Visa Reject") | refused |
| Lost with "Went with DG instead" | accepted, `lost_time` stamped |
| Moving back out of `lost` | refused (terminal) |
| Won | `won_time` stamped |
| Second lead, same phone | refused |
| Test rows removed | 0 left |

Plus 110 DB-free tests passing and a clean `pyflakes` sweep (0 undefined
names).

### Still to do

1. **Frontend** — the third CRM-UI deployment. The long pole.
2. **Website form → `/internal/website/ingest`** — needs Iconiq's side.
3. **Switch `SUPABASE_DB_URL` to the Session pooler** before Railway.
   The current value is the direct connection; it works locally, but
   FMC and AV both use the pooler and the codebase carries a
   pgbouncer-specific asyncpg patch.
4. **Confirm the two provisional lists** — 6 lost reasons, 11 industries.
   Both are one-line edits, no migration.
5. **RLS** (§10) — still open, still deliberate.

---

*Scope confirmed by Amit, 2026-09-09. Database created and backend built
2026-09-09. Written against `main` @ `6d814a5`.*
