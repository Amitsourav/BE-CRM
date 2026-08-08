# MIS Lead-Provider Portal — Build Prompt (Backend + Frontend)

> Hand this entire file to the new repo. It is self-contained: it specifies the goal, architecture, database, sync layer, API, auth, and UI for a **separate** application that lets external **lead providers** log in and see how the leads they supplied are performing across **two CRMs (FundMyCampus + Admitverse)**.

---

## 0 — Goal & non-negotiables

Build a standalone **MIS (Management Information System) web app** — its own backend + its own frontend + its own database — completely separate from the existing CRMs. It:

- Lets each **lead provider** (external vendor) log in and see **only their own leads** and performance.
- Aggregates data from **two separate CRM databases** (FMC + Admitverse), each a Supabase Postgres.
- Reads those CRMs **read-only** — it must NEVER write to them and must NOT require any code change in them.
- Is **scalable**: pre-aggregated rollups, incremental sync, add-a-brand = add-a-connector.

**Hard rules**
1. The MIS connects to the CRMs via **read-only Postgres connections only**. No write access, ever.
2. A provider can see **only** leads whose `lead_source_id` maps to that provider. Enforce server-side on every query — never trust the client.
3. External provider auth lives **only** in the MIS — never reuse the CRMs' Supabase auth.
4. All thresholds/targets/benchmarks are **configurable** (no hard-coded industry numbers — published benchmarks are unreliable).

---

## 1 — Architecture

```
[ FMC Supabase ] ──┐  read-only, incremental
                   ├──► [ MIS SYNC worker ] ──► [ MIS Postgres ] ──► [ MIS API ] ──► [ MIS Frontend ]
[ AV  Supabase ] ──┘     (every 30–60 min)      (normalized +         (fast local      (provider portal
                                                 pre-aggregated)       reads only)       + admin)
```

- **Sync worker**: scheduled job. Pulls changed rows from each CRM, normalizes both brands' pipelines into one **canonical funnel**, upserts into the MIS DB, recomputes daily rollups.
- **MIS Postgres**: the app's own database. Holds provider accounts, provider→source mapping, normalized lead facts, daily metric rollups, (later) disputes + payouts.
- **MIS API**: provider-facing + admin endpoints. Reads only the MIS DB. Fast.
- **MIS Frontend**: provider portal + internal admin.

The slow cross-region CRM latency only affects the background sync (nobody watches it). All user-facing reads hit the local MIS DB.

---

## 2 — Recommended tech stack

Chosen to match the team's existing skillset (the CRMs are FastAPI + async SQLAlchemy + Next.js) so patterns and deploy targets are reused.

- **Backend**: Python 3.11 · FastAPI · async SQLAlchemy 2.0 · asyncpg · Alembic · APScheduler (for the sync job) · python-jose (JWT) · passlib[bcrypt] (password hashing). Deploy on Railway.
- **Frontend**: Next.js 14 (App Router) · TypeScript · Tailwind · shadcn/ui · Recharts (charts) · Axios. Deploy on Vercel.
- **MIS database**: a fresh Postgres — a new Supabase project or Railway Postgres. (Separate from both CRM DBs.)
- **Two repos OR one monorepo** — either is fine; keep `backend/` and `frontend/` clearly separated.

*(If the team prefers Node/NestJS for the backend, the schema, sync logic, API contract, and UI below all still apply unchanged.)*

---

## 3 — MIS database schema (its own Postgres)

```sql
-- ENUMS
CREATE TYPE brand AS ENUM ('fmc', 'av');
CREATE TYPE canonical_stage AS ENUM (
  'delivered','contacted','connected','qualified','in_process',
  'converted','opportunity','dnp','lost'
);

-- Provider accounts (external vendors)
CREATE TABLE providers (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name          text NOT NULL,
  contact_email text,
  is_active     boolean NOT NULL DEFAULT true,
  payout_config jsonb DEFAULT '{}',          -- Phase 2 (billing model, rates)
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

-- Provider login users (admin creates these manually)
CREATE TABLE provider_users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_id   uuid NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
  email         text UNIQUE NOT NULL,
  password_hash text NOT NULL,               -- bcrypt
  is_active     boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- Internal admin users (you)
CREATE TABLE admin_users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email         text UNIQUE NOT NULL,
  password_hash text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- THE MAPPING: which CRM source(s) belong to which provider, per brand
CREATE TABLE provider_sources (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_id   uuid NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
  brand         brand NOT NULL,
  crm_source_id uuid NOT NULL,               -- = lead_sources.id in that brand's CRM
  source_name   text,                        -- cached label for display
  UNIQUE (brand, crm_source_id)              -- one source maps to exactly one provider
);

-- Normalized lead facts, synced from both CRMs
CREATE TABLE mis_leads (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  brand           brand NOT NULL,
  crm_lead_id     uuid NOT NULL,
  provider_id     uuid REFERENCES providers(id),     -- null if source unmapped
  crm_source_id   uuid,
  serial_no       integer,
  full_name       text,
  phone           text,
  raw_stage       text,                              -- original CRM stage (audit)
  canonical_stage canonical_stage NOT NULL,
  is_invalid      boolean NOT NULL DEFAULT false,
  invalid_reason  text,
  is_duplicate    boolean NOT NULL DEFAULT false,
  created_at      timestamptz,                       -- lead.created_at in CRM
  contacted_at    timestamptz,
  qualified_at    timestamptz,
  converted_at    timestamptz,
  lost_at         timestamptz,
  crm_updated_at  timestamptz,                       -- for incremental sync
  synced_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (brand, crm_lead_id)
);
CREATE INDEX idx_mis_leads_provider ON mis_leads (provider_id, brand, created_at);
CREATE INDEX idx_mis_leads_stage    ON mis_leads (provider_id, canonical_stage);

-- Pre-aggregated daily rollups (what the dashboard reads)
CREATE TABLE provider_daily_metrics (
  provider_id   uuid NOT NULL REFERENCES providers(id),
  brand         brand NOT NULL,
  metric_date   date NOT NULL,
  delivered     integer NOT NULL DEFAULT 0,
  invalid       integer NOT NULL DEFAULT 0,
  duplicates    integer NOT NULL DEFAULT 0,
  valid         integer NOT NULL DEFAULT 0,
  contacted     integer NOT NULL DEFAULT 0,
  connected     integer NOT NULL DEFAULT 0,
  qualified     integer NOT NULL DEFAULT 0,
  converted     integer NOT NULL DEFAULT 0,
  dnp           integer NOT NULL DEFAULT 0,
  lost          integer NOT NULL DEFAULT 0,
  PRIMARY KEY (provider_id, brand, metric_date)
);

-- Sync bookkeeping (incremental watermark per brand)
CREATE TABLE sync_state (
  brand          brand PRIMARY KEY,
  last_watermark timestamptz,                 -- max crm_updated_at processed
  last_run_at    timestamptz,
  last_status    text
);

-- Company-set targets (NO industry benchmarks — all configurable)
CREATE TABLE targets (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  brand       brand,                          -- null = applies to all
  metric_key  text NOT NULL,                  -- 'qualification_rate','conversion_rate','cpl', etc.
  target_value numeric NOT NULL
);

-- Phase 2 (do not build yet, but reserve the shape):
-- lead_disputes(provider_id, mis_lead_id, reason, status, raised_at, resolved_at)
-- payout_rules(provider_id, model, rate, currency), payout_ledger(...)
```

---

## 4 — Sync layer (the heart of "both brands handled perfectly + scalable")

A scheduled job (APScheduler, every 30–60 min) running per brand:

1. **Connect** read-only to the brand's CRM Supabase using `FMC_DB_URL` / `AV_DB_URL`.
2. **Pull incrementally**: `SELECT … FROM leads WHERE updated_at > :last_watermark` (join `lead_sources`; pull related `lead_stage_logs` for the changed leads to derive stage timestamps).
3. **Sync ALL leads** for the brand (do NOT filter by mapped sources). For each lead, look up its `lead_source_id` in `provider_sources`: if mapped, stamp the matching `provider_id`; **if the source is unmapped, store the lead anyway with `provider_id = null`.** This way, when an admin maps a source later, that source's full back-history retroactively attaches to the provider on the next rollup recompute — nothing is lost. Unmapped (`provider_id = null`) leads are simply never returned by provider-facing queries; only the admin can see them. **Never skip a lead just because its source isn't mapped yet.**
4. **Normalize** each lead's CRM stage → `canonical_stage` via the per-brand mapping (§5). Derive `contacted_at` / `qualified_at` / `converted_at` / `lost_at` from `lead_stage_logs`.
5. **Detect duplicates**: same `phone` already present for the same brand within the last **30 days** → `is_duplicate = true`. (Invalid flagging: bad/empty phone, etc. → `is_invalid`, with reason.)
6. **Upsert** into `mis_leads` (`ON CONFLICT (brand, crm_lead_id) DO UPDATE`).
7. **Recompute** `provider_daily_metrics` for affected `(provider, brand, date)` rows.
8. **Advance** `sync_state.last_watermark` to the max `crm_updated_at` seen.

**Scalability:** incremental watermark (cost ∝ changes, not total size) + pre-aggregated rollups (dashboard never scans raw leads) + indexed scoping. Adding a 3rd brand = add an enum value + a connector + a stage map.

**CRM read-only setup (run once in each Supabase SQL editor):**
```sql
CREATE ROLE mis_readonly LOGIN PASSWORD '<strong-password>';
GRANT CONNECT ON DATABASE postgres TO mis_readonly;
GRANT USAGE ON SCHEMA public TO mis_readonly;
GRANT SELECT ON public.leads, public.lead_sources, public.lead_stage_logs TO mis_readonly;
```

---

## 5 — Canonical funnel & per-brand stage mapping

Both CRMs map into one comparable funnel:
`delivered → contacted → connected → qualified → in_process → converted` (+ side states `opportunity`, `dnp`, `lost`).

| Canonical | FMC raw stages | Admitverse raw stages |
|---|---|---|
| delivered | *(every lead)* | *(every lead)* |
| contacted | `contacted` | `contacted` |
| connected | *(qualified-eligible / engaged)* | `connected` |
| qualified | `qualified` | `qualified` |
| in_process | `processing`, `logged_in`, `shared_to_bank`, `doc_pending`, `sanctioned`, `pf_paid` | `processing`, `partial_docs_collected`, `docs_collected`, `application_done`, `conditional_draft`, `ucol`, `deposit_paid`, `cas_received`, `visa_applied` |
| converted | `disbursed` | `enrolled` |
| opportunity | `opportunity` | `opportunity` |
| dnp | `dnp` | `dnp_pre_qualified`, `dnp_post_qualified` |
| lost | `lost` | `lost` |

> Verify the exact raw stage strings against each CRM's live `lead_stage` enum before finalizing (the FMC pipeline was revamped May 2026; AV has a 17-stage pipeline). Keep the maps in a single config module so they're easy to adjust.

---

## 6 — Metrics & formulas (see MIS_RESEARCH_REPORT.md for sourcing)

Compute from `provider_daily_metrics` (aggregate over the selected range), per brand and combined:

- `valid = delivered − invalid − duplicates`
- `contact_rate = contacted / valid`
- `qualification_rate = qualified / valid`
- `conversion_rate = converted / valid`  *(lead-to-sale)*
- `invalid_rate = invalid / delivered`, `duplicate_rate = duplicates / delivered`
- `time_to_first_contact = avg(contacted_at − created_at)` (from `mis_leads`)
- `time_to_qualify = avg(qualified_at − created_at)`
- **Economics (Phase 2, needs payout + revenue):** `CPL = paid / delivered`, `cost_per_qualified = paid / qualified`, `CPA = paid / converted`, `ROI = (revenue − cost) / cost`

**Provider scorecard (configurable):**
```
criteria (default weights): validity 25%, qualification_rate 30%,
  conversion_rate 30%, volume_reliability 10%, dispute_rate(inverse) 5%
each rated 1/3/5 against company-set bands
Score% = Σ(rating × weight) / 5 × 100
Grade  = A/B/C/D/F by company-set bands (store in `targets`)
```

---

## 7 — Backend API

**Auth**
- `POST /auth/login` — provider or admin login (email + password) → MIS-issued JWT (carries `sub`, `role`=provider|admin, `provider_id` for providers). bcrypt verify.
- `POST /auth/logout`, `GET /auth/me`.
- Middleware: every provider request resolves `provider_id` from the JWT and injects the provider's `source_ids` (from `provider_sources`) into the query scope. **Never accept provider_id/source from the client.**

**Provider-facing (scoped to caller's provider_id)**
- `GET /me/overview?brand=&from=&to=` — KPI cards + funnel counts + scorecard grade.
- `GET /me/trends?brand=&from=&to=&granularity=day|week` — time series.
- `GET /me/brand-split?from=&to=` — FMC vs AV breakdown.
- `GET /me/quality?from=&to=` — invalid rate, duplicate rate.
- `GET /me/leads?brand=&stage=&from=&to=&q=&page=&page_size=` — per-lead table.
- `GET /me/leads/export?…` — CSV.
- `GET /me/payout?…` — *Phase 2*.

**Admin-facing (role=admin)**
- `POST /admin/providers`, `GET /admin/providers`, `PUT /admin/providers/{id}`.
- `POST /admin/providers/{id}/users` — create a provider login (email + temp password).
- `POST /admin/providers/{id}/sources` — map a CRM source (brand + crm_source_id) to the provider. **On success, back-stamp `provider_id` onto all existing `mis_leads` for that `(brand, crm_source_id)` that currently have `provider_id = null`, then recompute `provider_daily_metrics` for the affected dates.** (Leads were already synced with `provider_id = null`; this is what makes the source's history retroactively appear in the provider's dashboard.)
- `GET /admin/crm-sources?brand=` — proxy list of `lead_sources` from a CRM (read-only) so admin can pick which to map.
- `GET /admin/leaderboard?from=&to=` — all providers ranked by scorecard.
- `GET/PUT /admin/targets` — set configurable targets/bands.
- `POST /admin/sync/run` — trigger a manual sync; `GET /admin/sync/status`.

All responses paginated as `{ items, total, page, page_size }`. All money/rate values returned as numbers; format in the UI.

---

## 8 — Frontend

**Auth & shell**
- `/login` (shared; routes by role). Provider portal under `(portal)`, admin under `(admin)`. Middleware checks JWT; redirect unauth to `/login`.
- Token in httpOnly cookie (preferred) or memory; Axios interceptor refreshes/redirects on 401.

**Provider portal**
- `/dashboard` — header strip (brand filter FMC/AV/Both · date range · **"Data as of HH:MM"**); KPI cards (Delivered, Valid %, Qualified, Converted, Conversion Rate, Scorecard grade); **funnel chart** (delivered→contacted→connected→qualified→converted with drop-off %); **trend chart** (leads + conversions, day/week toggle); **brand split** (only if provider has both); **quality panel** (invalid %, duplicate %).
- `/leads` — table: serial · name · phone · brand · source · current stage · created date · status; filters (brand, stage, date, search) + CSV export.
- `/payout` — *Phase 2* placeholder.

**Admin**
- `/admin/providers` — list + create; provider detail: edit, manage logins, **map sources** (pick from `GET /admin/crm-sources`).
- `/admin/leaderboard` — all providers by scorecard, sortable.
- `/admin/targets` — set targets/grade bands.
- `/admin/sync` — last run, status, "Run now".

**UX**: skeleton loaders, empty states, toast on mutations, debounced search, responsive. Charts via Recharts. Stage/grade color tokens consistent across the app.

---

## 9 — Security & DPDP

- Provider scoping enforced **server-side on every query** (filter by the caller's `source_ids`); add a test that Provider A cannot read Provider B's leads.
- Decide which fields are exposed to external providers. **Showing serial/name/phone/status of leads they supplied is acceptable** (they originated that PII). **Do NOT expose** AI-call transcripts, internal agent notes, `custom_fields`, or other leads' data.
- Read-only DB role means the MIS can never mutate CRM data — keep it that way (no write grants).
- Rate-limit `/auth/login`; bcrypt passwords; rotate the `mis_readonly` credentials if leaked.
- Log admin actions (create provider, map source, change targets).

---

## 10 — Environment variables

**Backend**
```
MIS_DATABASE_URL=          # the MIS's own Postgres
FMC_DB_URL=                # read-only conn string to FMC Supabase
AV_DB_URL=                 # read-only conn string to Admitverse Supabase
JWT_SECRET=
SYNC_INTERVAL_MINUTES=45
CORS_ORIGINS=["https://<mis-frontend>"]
```
**Frontend**
```
NEXT_PUBLIC_API_URL=       # MIS backend URL
```

---

## 11 — Build order

**Phase 1 — MVP (Layers 1–2, no money)**
1. MIS DB schema + Alembic.
2. Read-only `mis_readonly` users in both Supabase projects.
3. Sync worker: incremental pull + stage normalization + duplicate/invalid flagging + rollups (start with FMC, then add AV — proves the add-a-brand path).
4. Auth (admin + provider) + provider scoping.
5. Admin: create providers, create logins, map sources, run sync.
6. Provider dashboard (KPIs + funnel + trends + brand split + quality) + leads table + CSV export.
7. Scorecard + configurable targets.

**Phase 2 — Money & disputes**
8. Payout module (billing model per provider, settlement view) — once the billing model is decided.
9. Dispute/return workflow (invalid-lead categories, 30-day duplicate window, minimum-effort gate) + return-concentration red flag.

---

## 12 — Decisions still needed from the business (don't block Phase 1)
1. **Billing model** per provider (pay-per-lead / per-qualified / per-conversion / rev-share) → defines Layer-3 + payout view.
2. **Target values** (CPL, qualification, conversion, return ceiling) — set from FMC/AV historical data, not published benchmarks (all refuted).
3. **Dispute window & SLA** — your own DPDP-compliant terms.
4. **Freshness** — sync interval (30 / 45 / 60 min) and whether any field must be masked further.

*See `MIS_RESEARCH_REPORT.md` for the metric definitions, scorecard rationale, and the refuted-benchmarks warning.*
