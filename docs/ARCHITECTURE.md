# Backend Architecture — Admitverse / FundMyCampus CRM

> **Where the backend lives:** this repository (`Amitsourav/BE-CRM`), all Python
> code under `app/`. Deployed to **Railway** (`https://be-crm-production.up.railway.app`),
> containerized via `Dockerfile` (`uvicorn app.main:app` on port 8000).
> ~21,000 lines across 122 Python files (23 ORM models, 136 HTTP/WS routes,
> 46 Alembic migrations, 148 tests). Last verified against `main` @ `13665d9` (2026-07-28).

A single FastAPI codebase serves **two brands** (FundMyCampus for education loans,
Admitverse for study-abroad admissions) from separate Railway + Vercel + Supabase
deployments. The active brand is selected by the `APP_NAME` env var and each
company's `slug` (`app/main.py:45`; FMC slug = `"default"`, AV slug = `"admitverse"`).

---

## 1. Stack & Entry Point

| Concern | Choice |
|---|---|
| Framework | FastAPI 0.115 |
| ORM / DB driver | SQLAlchemy 2.0 (async) + asyncpg |
| Database | Supabase PostgreSQL (Korea region), via pgbouncer transaction mode |
| Auth | Supabase JWT (ES256 / HS256) |
| Telephony | Plivo (bidirectional media WebSocket) |
| STT / TTS / LLM | Sarvam, Smallest, Deepgram, OpenAI, OpenRouter |
| Background jobs | APScheduler (in-process) |
| Error tracking | Sentry (optional, `SENTRY_DSN`) |
| PDF | ReportLab |

**Boot sequence** (`app/main.py`):
1. Init Sentry if `SENTRY_DSN` set.
2. `lifespan` → `_run_pending_migrations()` — fresh DB gets `Base.metadata.create_all` + `alembic stamp head`; existing DB gets `alembic upgrade head` (opt out with `AUTO_MIGRATE=false`).
3. `start_scheduler()` — background workers begin.
4. Mount `app/api/v1/router.py` under `/api/v1`; CORS, timing middleware, rate-limit + exception handlers attached.
5. `GET /health` liveness probe.

### Directory map

```
app/
├── main.py               # FastAPI app, lifespan, auto-migrate, CORS
├── config.py             # Pydantic Settings (all env vars), lru_cached
├── dependencies.py       # get_current_user / role deps, 30s profile cache
├── core/                 # auth, tenant, permissions, rate-limit, middleware, exceptions
├── db/                   # async engine + pgbouncer patch, fresh-DB bootstrap, supabase clients
├── utils/                # csv_parser, loan_parser, budget_parser, date_helpers, hmac_verify, pagination
├── models/               # 23 SQLAlchemy ORM models
├── schemas/              # Pydantic request/response DTOs
├── api/v1/               # 16 routers (HTTP + WebSocket)
├── services/             # 21 business-logic services
│   └── voice_engine/     # 16 real-time voice files (STT/LLM/TTS pipeline)
└── workers/              # APScheduler + campaign dialer + Meta retry
```

---

## 2. Core / Platform Layer

### Configuration — `app/config.py`
Pydantic `BaseSettings`, `@lru_cache`d singleton via `get_settings()`. Loads `.env`.
Groups: Supabase (incl. private `supabase_storage_bucket`, default `invoices`), Meta Lead Ads (incl. FMC→AV forwarding `av_backend_url` + `internal_meta_secret`, and `website_lead_secret` for the admitverse.com form ingest — falls back to `internal_meta_secret` when unset), voice providers (OpenRouter/Sarvam/Deepgram/OpenAI/Smallest), Plivo/Exotel telephony, `voice_stream_secret`, and limits (`max_call_attempts=6`, `csv_max_rows=5000`, `max_concurrent_calls=50`).
`async_database_url` strips `?pgbouncer=true` and rewrites `postgresql://` → `postgresql+asyncpg://` (`config.py:87-95`).

### Authentication — `app/core/security.py`
`verify_jwt()` FastAPI dependency:
- Reads unverified header for `alg`.
- **Asymmetric (ES256/RS256):** fetches Supabase JWKS (`_jwks_cache`, never expires), matches by `kid`, validates `audience="authenticated"`.
- **Symmetric (HS256):** verifies with `supabase_jwt_secret`.
- Requires `sub` claim; all failures → `UnauthorizedError` (401).
- ⚠️ Known gap: JWKS cache never expires — Supabase key rotation would break logins (see CLAUDE.md pending work #5).

### Users & RBAC — `app/dependencies.py`, `app/core/permissions.py`
- **30-second in-memory profile cache** (`dependencies.py:16-42`) — critical because Supabase Korea can be 2-20s/query. Max 1000 entries.
- `get_current_user()` → validated `Profile`; rejects `is_active=false`.
- Role hierarchy: **admin > manager > pre_counsellor**. Deps: `get_current_admin`, `get_current_manager`, `get_current_telecaller`; factory `require_role(*roles)`.

### Multi-tenancy — `app/core/tenant.py`
`get_current_company_id()` reads `company_id` from the authenticated `Profile`. Every table carries `company_id`; services filter all queries by it. (Note: Meta webhook ingest is the one cross-tenant seam — see §7.)

### Rate limiting / timing / exceptions
- `app/core/rate_limit.py` — shared slowapi `Limiter` keyed by client IP; per-route `@limiter.limit(...)` (currently only ~4 routes — pending broader coverage).
- `app/core/middleware.py` — `TimingMiddleware`, adds `X-Response-Time`, warns on >1s requests.
- `app/core/exceptions.py` / `exception_handlers.py` — typed HTTPExceptions (`NotFoundError`, `ForbiddenError`, `InvalidTransitionError`, …) + structured Pydantic validation errors.

### Database session — `app/db/session.py`
- **pgbouncer prepared-statement fix:** monkey-patches `asyncpg…Connection._get_unique_id` to append a UUID so `__asyncpg_stmt_N__` names can't collide across pooled backends (same patch mirrored in `alembic/env.py`).
- Engine: `pool_size=10, max_overflow=20, pool_pre_ping=True, statement_cache_size=0, pool_recycle=300`.
- `get_db()` yields a context-managed `AsyncSession` (`expire_on_commit=False`).

### Fresh-DB bootstrap — `app/db/bootstrap.py`
`is_fresh_db()` checks for `alembic_version`. If absent: `CREATE EXTENSION pgcrypto`, create 8 ENUM types via `DO $$ … EXCEPTION duplicate_object $$` (models use `create_type=False`), then `Base.metadata.create_all`. Solves the empty-baseline-migration problem when spinning up a new Supabase project.

### Utilities — `app/utils/`
- **`csv_parser.py`** — ~60 header aliases, multi-encoding (UTF-8/CP1252/Latin-1), fuzzy column mapping (`SequenceMatcher ≥0.7`), Indian phone normalization → `+91…`.
- **`loan_parser.py`** — free-form → `Decimal` in lakhs ("1.5cr" → 150, "500000" → 5). Never raises.
- **`budget_parser.py`** — free-form → `(amount, currency)` with symbol/keyword currency detection (Admitverse).
- **`date_helpers.py`** — IST (UTC+5:30), business-day arithmetic, start/end-of-day.
- **`hmac_verify.py`** — timing-safe Meta webhook signature check.
- **`pagination.py`** — async count (ORDER BY stripped) + offset/limit, `page_size` capped at 100.

---

## 3. Data Model Layer — `app/models/`

Base conventions (`base.py`): UUID PKs (`gen_random_uuid()`), `TimestampMixin` (`created_at`/`updated_at`, tz-aware UTC), most FKs `CASCADE` (soft links `SET NULL`).

### Entity map
```
Company (tenant root; slug drives brand rules)
├─ Profile (users/agents: admin|manager|pre_counsellor)
├─ Lead ─┬─ LeadStageLog   (immutable stage audit; clock_timestamp() default)
│        ├─ LeadBank        (FMC: per-bank application + sanction details)
│        ├─ LeadApplication (AV: per-university application + offer/visa)
│        ├─ LeadRemark      (timestamped comments w/ author role snapshot)
│        ├─ CallAttempt     (manual + AI calls; transcript/sentiment/cost)
│        └─ Task
├─ LeadSource (csv|meta_ads|manual|whatsapp)
├─ CSVImport (bulk import tracking + per-row errors)
├─ AIAgent (voice config, ~120 cols across 12 sections)
├─ Campaign ── CampaignLead (retry tracking, priority, last_call ref)
├─ Task / Notification / ActivityLog
├─ Invoice / InvoiceSettings (1:1) / InvoiceCounter (per-FY sequence)
└─ MetaFormRouting / MetaWebhookEvent (Meta gateway; not company-scoped)
```

### Key tables
- **Lead** — the fat entity. Identity + education + pipeline (`current_stage`, `assigned_agent_id`, `pre_counsellor_id`, `lead_source_id`, `csv_import_id`), `custom_fields` JSONB, soft-delete (`is_deleted`/`deleted_at`), `serial_no` (per-company). **FMC tile:** `loan_amount`/`loan_amount_lakh`, `bank_name`/`bank_status`, `dnp_count`, `submitted_docs`. **AV tile:** `budget`/`budget_amount`/`budget_currency`, `primary_university`, `application_status`.
- **CallAttempt** — unified manual + AI log. AI fields: `bolna_call_id`/`external_call_id`, `call_status`, `transcript`, `summary`, `sentiment`+`sentiment_score`, `cost`, `started_at`/`ended_at`. Indexed on status/type/sentiment/started_at.
- **AIAgent** — 60 columns in 12 config sections: identity, prompt (welcome/final/silence in en+hi, `post_call_analysis_prompt`), LLM, STT, TTS (incl. **dual per-language TTS**), language style, call timing (endpointing/barge-in), telephony (caller ID, call-hours), audio quality, webhook. Soft-deleted via `deleted_at`.
- **Campaign / CampaignLead** — schedule (daily window, skip weekends, tz), retry (`max_retries`, `retry_gap_hours`), concurrency (`max_concurrent_calls`), denormalized stats. CampaignLead tracks `attempt_count`, `next_retry_at`, `priority`, `last_call_id`.
- **Invoice** — GST-compliant snapshot: sequence per FY (`InvoiceCounter` UPSERT), immutable customer block, `line_items` JSONB, CGST/SGST/IGST split, status `draft|issued|paid|void`, `pdf_storage_path`.
- **MetaWebhookEvent** — durable queue (`pending|processing|done|failed|dropped`) with exponential-backoff retry; `leadgen_id` UNIQUE for idempotency across Meta's 36h redelivery.

### ENUM types
`user_role`, `lead_stage` (23 values spanning both pipelines), `call_disposition`, `task_type`, `task_status`, `notification_type`, `lead_source_type`, `csv_import_status`, `campaign_status`, `campaign_lead_status`, plus FMC `bank_status`/`pf_status` and AV `application_status`/`visa_status`. Canonical values + transition rules live in `app/core/constants.py` (also `FMC_BANKS`, `LOST_REASONS`, doc checklists, Indian state codes).

---

## 4. API Layer — `app/api/v1/` (prefix `/api/v1`)

| Router | Prefix | Highlights |
|---|---|---|
| **auth** | `/auth` | login (5/min), admin register, refresh, reset/update password |
| **companies** | `/companies` | admin-only tenant CRUD |
| **users** | `/users` | `/me`, list w/ lead counts, admin update/deactivate, `/{id}/stats` |
| **leads** | `/leads` | CRUD, **`/by-stage` Kanban** (all columns in 1 call, 15s cache), search, per-lead banks/applications/remarks/timeline/calls/tasks, assign/reassign/bulk-assign/distribute-by-range, brand dropdowns (lost-reasons, banks, universities, docs checklist), Meta routing CRUD. Admin-only `lead_segment` slice on both list + Kanban: `campaign` (enrolled in some campaign = AI-called) \| **`normal`** (never enrolled — human-worked only) \| `unassigned` \| `counsellor` \| `pre_counsellor` |
| **lead_stages** | — | `POST /leads/{id}/stage` (StageMachine), stage history |
| **call_attempts** | — | log manual call, `POST /calls/initiate` (AI via Bolna), list/filter, status poll, post-data |
| **tasks** | `/tasks` | CRUD, `/count` badge, today/overdue/completed-today, complete |
| **notifications** | `/notifications` | list, unread-count (5s timeout→0, never 500), mark read/all |
| **csv_import** | `/csv` | template, upload (mgr+), preview (mapping suggestions), process (dedup), status, history |
| **webhooks** | `/webhooks` + `/internal` | Meta verify+receive, Bolna call events, `POST /internal/meta/ingest` (FMC→AV forward, `X-Internal-Secret`, dedups on `leadgen_id` then phone), `POST /internal/website/ingest` (admitverse.com forms / dMAT funnel — `page`/`tag`/`intake`/`referred_by` land in `custom_fields`, dedups on email then phone) |
| **reports** | `/reports` | dashboard, pipeline, agents, sources, trends, per-user daily activity, task compliance, call stats |
| **agents** | `/agents` | AIAgent CRUD + clone + test-chat |
| **voice** | `/voice` | outbound initiate, Plivo answer/hangup webhooks, **WebSocket `/stream/{call_id}`** (see §6) |
| **activity_logs** | `/activity-logs` | audit trail w/ old/new value diffs |
| **campaigns** | `/campaigns` | CRUD, start/pause/stop, assign-leads (+bulk by filter), per-campaign CSV upload |
| **invoices** | `/invoices` | settings + logo/signature upload, create (reserve #→tax→PDF), list/filter, prefill-from-lead, signed download, regenerate-pdf, status transition |

---

## 5. Service Layer — `app/services/`

- **`lead_service.py`** — core CRUD + assignment + search; **Kanban `list_leads_by_stage`** with 15s per-(company,user,filter) cache, invalidated on any lead write; role-scoped visibility; auto-reserves serial numbers; auto-creates callback tasks.
- **`stage_machine.py`** — brand-aware transitions. **Both** brands allow free movement (any non-terminal stage → any other; terminals are FMC `disbursed`/`lost`, AV `enrolled`/`lost`); the tables are generated in `constants.py`, not hand-written. Gates on every transition: `lost_reason` required for LOST (FMC validates against the locked 21-value `LOST_REASONS`; AV accepts free text), a follow-up `due_date` required for every non-terminal move, notes only where `*_STAGES_REQUIRING_NOTES` says (currently empty for both brands), admin-only reopen from LOST. Side effects: stamps `connected_time`/`won_time`/`lost_time`, increments `dnp_count` on `dnp` (FMC) **and** `dnp_pre_qualified`/`dnp_post_qualified` (AV), mirrors AV `enrolled` → `won_time`, creates an idempotent callback Task, auto-closes past-due CALL tasks + recomputes `lead.due_date`, writes `LeadStageLog`.
- **`call_service.py`** — manual call logging + AI initiation. DNP tracking: warn at 5 attempts, auto-LOST at `max_call_attempts=6`.
- **`post_call_service.py`** — async post-call pipeline: summary + sentiment via OpenRouter (`gpt-4o-mini`), then `auto_update_lead_status` (sentiment + brand rules). Idempotent per `call_id`.
- **`campaign_service.py`** — draft→active→paused→stopped lifecycle; filter-driven bulk enrollment (skips no-phone + already-enrolled); cost estimation.
- **`csv_import_service.py`** — advisory-lock-serialized processing; parse→batch-dedup-by-phone→bulk-insert→error report.
- **`report_service.py`** — batched multi-metric queries, per-brand won-stage (FMC=DISBURSED, AV=ENROLLED), restricted-user scoping.
- **`invoice_service.py` / `invoice_tax.py` / `invoice_pdf.py`** — atomic per-FY numbering (`INSERT … ON CONFLICT … RETURNING next_number - 1`; a *fresh* (company, FY) sequence starts at the admin-configurable `invoice_settings.invoice_start_number`, default 20, so early invoices don't read as `001` — an in-progress FY just keeps incrementing); GST split (same-state CGST+SGST vs IGST); ReportLab PDF (Indian money formatting, amount-in-words) uploaded best-effort to Supabase Storage (invoice commits even if PDF fails; `regenerate-pdf` to retry).
- **`pricing_service.py`** — per-minute cost table (STT/TTS/LLM/telephony) → per-agent breakdown + `savings_vs_bolna_pct`; campaign estimate = agent$/min × 3min × leads × ₹83.
- Others: `auth_service`, `task_service`, `company_service`, `notification_service`, `ai_agent_service`, `meta_webhook_service`, `supabase_storage`, `bolna_service`, `language_detector`.

---

## 6. Voice / AI Agent Engine — `app/services/voice_engine/` + `app/api/v1/voice.py`

Real-time bidirectional AI phone calls over Plivo. Target latency: ~2s welcome, ~1.5-2.5s/turn.

### Call lifecycle
1. **Outbound initiate** (`voice.py`) — normalize phone (E.164/IN), enforce call hours (IST), create `CallAttempt`, create in-memory `CallState` (concurrency-capped at 50). Spawn 3 background warmups during ring: **welcome-audio pre-gen** (TTS→mulaw→base64 for instant playback), **LLM warmup** (1-token ping avoids 3-6s cold start), **STT connection warmup**. Then Plivo REST dial.
2. **Answer webhook** — verify Plivo HMAC signature, return Plivo XML opening a bidirectional media WebSocket authenticated by a single-use **HMAC stream token** (`stream_token.py`, 15-min TTL, `call_id`-bound, constant-time verify).
3. **WebSocket `/stream/{call_id}`** — the turn loop:
   - **Silence watchdog** (2s): plays "are you there?" at half-timeout, farewell + hangup at full timeout; pauses while agent speaks.
   - **Barge-in:** ~24 frames (~480ms) of non-silence while speaking → `clearAudio`, stop, re-capture. Slow decay avoids false resets on natural pauses.
   - **Turn-end:** `silence_frames ≥ endpointing threshold AND speech_frames ≥ min AND buffer ≥ 200ms` → convert mulaw→WAV → `process_audio_streaming()`.

### Streaming pipeline — `pipeline.py`
```
STT (~300ms) → filler sound ("Okay, so…") → LLM streaming → sentence batching → TTS per sentence → mulaw → Plivo playAudio
```
- **STT** — routed by `stt_router.py`; Sarvam default (locked `en-IN` to dodge Hindi↔English translation bug), Deepgram `multi`/`nova-3`, OpenAI Whisper. `TURN_TIMING` logged per turn.
- **Filler sounds** — pre-generated per config, short (≤3-word input) vs long, slowed to 0.8-1.1×.
- **LLM** — `llm_service.py` streams OpenRouter `/api/v1/chat/completions`. Language policy: `natural | hinglish | primary_only | mirror_hinglish | mirror_user` (short <4-word inputs reuse prior turn's language). Injects NAME-HANDLING block; history trimmed to 10 turns (welcome preserved). **Early-flush** first clause on punctuation for faster first audio. `[END_CALL]` tag ends call.
- **TTS** — `smallest_tts.py` (v3.1, 100-300ms, raw PCM wrapped to WAV) preferred for English; `sarvam_tts.py` (bulbul:v3, 800-1500ms, `simran` safe fallback) for Hindi. **Dual-TTS** picks provider/voice per detected language.
- **Audio** — `audio_utils.py` (mulaw↔WAV via `audioop`, RMS silence detection, sentence-aware `split_for_tts`). `http_clients.py` keeps persistent pooled `httpx.AsyncClient` per provider (saves ~100-200ms/provider/turn). `retry.py` generic async retry (STT 1×, LLM/TTS 2×, with fallbacks).

### Post-call — `voice.py` hangup webhook → `_save_summary_background`
LLM analysis (custom `post_call_analysis_prompt` or generic) → sentiment/interest/summary/`user_name`/brand fields → update `CallAttempt` (summary, sentiment, cost) + `Lead` (notes block, `custom_fields.ai_last_call`, learn real name over placeholder) → `_auto_update_lead_stage` → campaign `handle_call_completed` → `ActivityLog`.

`_auto_update_lead_stage` is brand-aware:
- **Admitverse** — walks `CREATED → CONTACTED → CONNECTED → QUALIFIED` one step at a time, never backward; `connected → qualified` additionally gated on transcript ≥500 chars **and** ≥3 user turns (stops "User: Can you speak?" transcripts from qualifying).
- **FMC** — delegates to `_fmc_auto_advance`, one transition per call, only from the early stages in `_FMC_AUTO_ADVANCE_STAGES` (anything at Processing or beyond is human loan paperwork and is never mutated):

  | Call outcome | Result |
  |---|---|
  | no pickup at CREATED/CONTACTED | → `dnp` |
  | no pickup at DNP, `call_attempt_count` ≥ 12 | → `lost` (auto-churn, auto lost_reason) |
  | negative sentiment **or** decline keywords in summary | → `lost` |
  | positive/intent keywords **+** future-intent keywords | → `opportunity` |
  | positive/intent keywords, no future signal | → `qualified` (+ next-day follow-up Task & notification) |
  | neutral on a real conversation | no-op — counsellor reviews |

  Keyword matching over `call.summary` (decline / future / intent sets) backstops the LLM's neutral bias. `changed_by` falls back explicit call agent → assigned agent → creator → any active admin/manager, so leads with no owner still get logged.

> Providers not yet wired: ElevenLabs/Cartesia TTS, per-agent non-OpenRouter LLM, Sarvam streaming STT (403), voicemail/noise-cancellation backends. See CLAUDE.md "Voice deferred features."

---

## 7. Background Workers & Integrations — `app/workers/`

**APScheduler** (`scheduler.py`, `AsyncIOScheduler`) — started in lifespan:
| Job | Interval | Action |
|---|---|---|
| `check_overdue_tasks` | 15 min | flip PENDING/IN_PROGRESS past-due → OVERDUE, notify assignees |
| `daily_task_rollover` | midnight UTC | placeholder |
| `cleanup_voice_call_state` | 10 min | drop in-memory call states >30 min old |
| `run_campaign_worker` | 30 s | dispatch campaign calls (singleton) |
| `run_meta_retry_worker` | 20 s | drain Meta webhook queue (singleton) |

**Campaign dialer** (`campaign_worker.py`) — per active campaign: calling-hours check, stuck-call recovery (>5min in `calling`→failed), **DB-based concurrency slots** (survives deploys), agent/creator validation (auto-pause + notify if missing), lead selection (pending or retriable failed, by priority), dispatch via `plivo_handler.make_call`. Atomic campaign stat increments; compensating updates on failure; `handle_call_completed` rolls up cost + schedules retries; `_check_completion` auto-completes.

**Meta Lead Ads** (`webhooks.py` + `meta_webhook_service.py` + `meta_retry.py`):
- Webhook verifies HMAC (hard-fail in prod), **always returns 200**, enqueues each leadgen change to `meta_webhook_events` (UPSERT on `leadgen_id`).
- Retry worker: `SELECT … FOR UPDATE SKIP LOCKED`, backoff `[1,5,15,60,180,360]` min, max 6 attempts.
- Per-event: fetch lead from Graph API, resolve `MetaFormRouting` by `form_id` → **target `fmc`** (local ingest w/ leadgen_id + phone dedup) or **target `av`** (POST to `av_backend_url/api/v1/internal/meta/ingest` with `X-Internal-Secret`). FMC is the single Meta gateway for both brands.
- On the receiving side, `company_id` is resolved from the forwarded `source_id`'s `LeadSource`, falling back to "first admin on this DB" — safe on the single-tenant AV deployment, and the same fallback the website ingest uses.
- ⚠️ Cross-tenant caution noted in CLAUDE.md #4 (company_id resolution on ingest).

**Supabase Storage** (`supabase_storage.py`) — admin-client uploads (invoices, logos, signatures) with `upsert=true` + no-cache; short-lived signed URLs (default 300s); `download_bytes` for embedding assets into PDFs.

**Bolna** (`bolna_service.py`, `webhooks.py /bolna`) — alternative AI-call platform; call events update status/transcript/recording and trigger the post-call pipeline.

---

## 8. Migrations, Scripts, Tests

**Alembic** (`alembic/`, 46 migrations) — async engine w/ `NullPool` + the same asyncpg UUID-name patch; `env.py` imports all models for autogenerate. Auto-run on Railway startup (§1). Notable recent: budget/loan numeric columns, lead_applications, unique `(company_id, phone)` per tenant, Meta routing/events, invoices, Admitverse pipeline enum, lead serial numbers.

**Operational scripts** (`scripts/`) — `seed_admin` (idempotent admin+company), `dedupe_leads` (merge duplicate phones; dry-run default), `audit_last_campaign` / `audit_pipeline_integrity` / `inspect_*`, `backfill_call_analysis` / `backfill_loan_amount_lakh`, `promote_handoff_leads` / `demote_false_qualified_leads`, `export_fmc_full_report`, `rename_company`, `patch_priya_prompt`, `benchmark`, `setup_supabase.sql`.

**Tests** (`tests/`, 148 test functions / 14 files) — real Supabase DB with transaction rollback. Run: `.venv/bin/python -m pytest`. Covers CRM workflows (auth, leads, stages, tasks, calls, CSV, reports, webhooks, notifications, applications) — not the voice engine.

---

## 9. Cross-Cutting Patterns

- **Multi-tenant everywhere** — `company_id` on every row; `get_current_company_id()` scopes all queries. Meta ingest is the one gateway seam.
- **Brand awareness** — `company.slug` selects pipeline stages, valid transitions, lost reasons, doc checklists, won-stage, and loan-vs-application tiles at runtime.
- **Soft delete** — `Lead.is_deleted`, `AIAgent.deleted_at`; queries filter deleted rows.
- **Latency engineering** — 30s profile cache, 15s Kanban cache, pooled HTTP clients, ring-time warmups, streaming LLM→TTS, filler sounds (all to fight Supabase-Korea and provider latency).
- **Durable async** — Meta events queued + retried; post-call analysis + campaign updates run in background tasks that never block webhooks.
- **Graceful degradation** — retries with fallbacks, best-effort PDF upload, unread-count returns 0 on timeout, migrations log-loud but don't crash boot.

---

## 10. Ingest Surfaces (how a lead gets in)

| Path | Auth | Dedup | Notes |
|---|---|---|---|
| `POST /leads` | JWT | phone, then email (400 on hit) | phone normalized to `+91…` before the check |
| `POST /csv/{id}/process` | JWT (mgr+) | batch by phone | advisory-lock serialized |
| `POST /webhooks/meta` | Meta HMAC | `leadgen_id` UNIQUE | queued → retry worker → FMC local or AV forward |
| `POST /internal/meta/ingest` | `X-Internal-Secret` | `leadgen_id`, then phone | AV side of the FMC gateway |
| `POST /internal/website/ingest` | `X-Internal-Secret` (`WEBSITE_LEAD_SECRET` → `INTERNAL_META_SECRET`), 60/min per IP | `external_id` replay guard | **Does not create a Lead** — stores a `website_submissions` row for human review. See §11 |
| Campaign CSV upload | JWT | per-campaign | phone-only rows allowed (name → `"Lead"`) |

All paths funnel through `LeadService.create_lead`, which normalizes the phone, rejects per-tenant phone/email duplicates, applies an auto-own rule (pre-counsellor creator → `pre_counsellor_id` = self; manager → `assigned_agent_id` = self; admin untouched), sets the brand initial stage (`CREATED`), mirrors `loan_amount`→`loan_amount_lakh` and `budget`→`budget_amount`+`budget_currency`, defaults `docs_required` to 8 on Admitverse vs 6 on FMC, reserves the per-tenant `serial_no`, writes the opening `LeadStageLog`, and queues a callback Task when a `due_date` came in.
Migration `r5o6p7q8r9s0` adds the matching per-tenant unique indexes on phone/email as the DB-level backstop.

---

## 11. Website Leads inbox — `website_submissions`

Marketing-site form fills land in a review queue rather than the pipeline,
because those forms are public and collect junk alongside real enquiries.

```
form → POST /internal/website/ingest → website_submissions (status='new')
                                              ↓ Manager+ triages
        convert → Lead + per-form LeadSource      status='converted'
        spam    → dismissed                       status='spam'
        (auto)  → matched an existing lead        status='duplicate'
```

- **Model** `app/models/website_submission.py` — form identity (`form_key`/`form_name`/`page`/`tag`), the person (`full_name`/`email`/`phone`/`message`), the full body in `payload` JSONB so forms can add fields without a backend deploy, `external_id` with a partial unique index for website retry idempotency, and triage state (`status`, `lead_id`, `reviewed_by`, `reviewed_at`).
- **Service** `app/services/website_submission_service.py` — `resolve_ingest_company()` (explicit slug → the single company → first-admin fallback with a loud warning), `ingest()` (normalizes phone/email, replays on `external_id`, flags a matching existing lead), plus `convert` / `mark_spam` / `reopen` and the panel reads.
- **API** `app/api/v1/website_leads.py` — hosts *both* the public `ingest_router` and the Manager+ `/website-leads` panel. ⚠️ This module intentionally omits `from __future__ import annotations`: slowapi's `@limiter.limit` hides the signature from FastAPI, and with postponed annotations the body model silently degrades to a query param (every request 422s). Same reason `auth.py` omits it.
- **Conversion** creates the Lead through `LeadService.create_lead` with `creator_role=None` (so the reviewer isn't auto-assigned the lead), auto-creating one `LeadSource` per form with the new `website` source type — so the existing sources report breaks down conversion per form. A 409 means an active lead already holds that email/phone; the submission is marked `duplicate` and linked to it.
- **Migration** `e5f6a7b8c9d0` — creates the table and adds `'website'` to the `lead_source_type` enum (in an `autocommit_block`, since `ALTER TYPE … ADD VALUE` can't run in a transaction).

Integration contract, per-form `form_key` catalog, env vars and Next.js snippets: **`docs/WEBSITE_LEADS.md`**.

---
*Generated from a full read of `app/` (core, models, schemas, API, services, voice engine, workers, migrations, scripts).
Re-verified 2026-07-28 against `main` @ `13665d9` plus the uncommitted `invoice_start_number` work.*
