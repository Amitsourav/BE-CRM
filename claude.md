# Admitverse CRM (FundMyCampus) — Backend

## Project Overview
AI-powered CRM for FundMyCampus, an education loan consultancy that helps Indian students get loans for studying abroad. The core feature is an **AI voice agent** that makes outbound calls to leads, speaks in natural Hinglish, and collects loan requirements.

**Stack:** FastAPI + async SQLAlchemy + Supabase PostgreSQL + Plivo telephony + multiple STT/TTS/LLM providers via OpenRouter.

## Deployment
- **Backend:** Railway at `https://be-crm-production.up.railway.app`
- **Frontend:** Vercel at `https://be-crm.vercel.app`
- **Database:** Supabase PostgreSQL (Korea region)
- **GitHub:** `https://github.com/Amitsourav/BE-CRM.git`

## Voice Agent Architecture

### Pipeline flow (per turn):
```
User speaks → silence detection (500ms) → Sarvam STT (~400ms)
→ filler sound plays instantly ("Hmm...", "Achha...")
→ LLM streaming via OpenRouter (~500ms first token)
→ TTS per sentence → mulaw → Plivo playAudio
```

### Current production config (Agent: "Priya - FundMyCampus Counselor"):
- **STT:** Sarvam AI, saaras:v3, language_code=en-IN
- **LLM:** Qwen Turbo via OpenRouter (~500ms TTFB, cheapest)
- **TTS:** Smallest AI, lightning-v3.1, voice=sana
- **Language style:** "natural" (LLM decides language from prompt, no code-level detection)
- **Telephony:** Plivo, +918031136711
- **Total turn latency:** ~1.5-2.5s

### Key voice engine files:
- `app/api/v1/voice.py` — WebSocket handler, Plivo webhooks, barge-in, silence watchdog
- `app/services/voice_engine/pipeline.py` — STT→filler→LLM→TTS streaming pipeline
- `app/services/voice_engine/llm_service.py` — OpenRouter LLM (batch + streaming), language policy
- `app/services/voice_engine/sarvam_stt.py` — Sarvam STT (batch, streaming disabled due to 403)
- `app/services/voice_engine/sarvam_tts.py` — Sarvam TTS (batch + streaming, 29 bulbul:v3 voices)
- `app/services/voice_engine/smallest_tts.py` — Smallest AI TTS (v1 legacy + v3.1 new API)
- `app/services/voice_engine/deepgram_stt.py` — Deepgram STT (nova-3, keyterm support)
- `app/services/voice_engine/filler_sounds.py` — Pre-generated filler sounds ("Hmm...", "Achha...")
- `app/services/voice_engine/call_state.py` — Per-call state (history, welcome audio cache)
- `app/services/voice_engine/http_clients.py` — Persistent httpx clients per provider
- `app/services/voice_engine/stt_router.py` — Routes STT by agent.stt_provider
- `app/services/voice_engine/language_detector.py` — Hindi/English detection (150+ word sets)
- `app/services/voice_engine/audio_utils.py` — WAV/mulaw conversion, silence detection
- `app/services/voice_engine/stream_token.py` — HMAC tokens for WebSocket auth
- `app/schemas/ai_agent.py` — PROVIDER_OPTIONS (all dropdown catalogs), agent schemas
- `app/services/pricing_service.py` — Per-minute cost calculation per provider

## Latency Optimizations Done (Apr 8-11, 2026)
Started at 7-8s welcome + 4-5s per turn. Now at ~2s welcome + ~1.5-2.5s per turn.

What was done (in order of impact):
1. Smallest TTS instead of Sarvam (1500ms → 350ms)
2. LLM warmup during ring time (eliminates 5s cold start)
3. Pre-gen welcome audio + pre-encode to mulaw+base64 (instant welcome)
4. Streaming LLM → TTS pipeline (sentence-by-sentence)
5. Filler sounds ("Hmm...", "Achha...") to eliminate dead air
6. Persistent HTTP clients (connection reuse)
7. Non-blocking DB writes in WS handler
8. Agent cache on CallState (skip duplicate DB lookups)
9. Early-flush first LLM clause on comma
10. Qwen Turbo LLM (~500ms vs GPT-4.1 Mini ~878ms)

What DIDN'T work (don't retry):
- Smallest Lightning v2 for Hindi — terrible pronunciation
- Smallest with Sarvam voice names (kavya) — hangs on unknown voices
- Sarvam STT hi-IN mode — translates English to Hindi
- Sarvam STT en-IN mode — translates Hindi to English
- Sarvam TTS streaming sub-chunks — no improvement for short sentences
- OpenAI direct bypass — user wants OpenRouter only

## Important Sarvam/Smallest API Notes

### Sarvam TTS (bulbul:v3):
- Confirmed voices: simran, priya, neha, pooja, ritu, kavya, ishita, shreya, tanya, roopa, shruti, suhani, kavitha, rupali (female); rahul, aditya, ashutosh, rohan, amit, dev, shubh, ratan, varun, manan, sumit, kabir, vijay, mohit, sunny (male)
- Voices from v1/v2 (meera, pavithra, maitreyi, diya, anushka, etc.) return HTTP 400 on v3
- Safe fallback voice: "simran"
- Returns WAV format

### Smallest TTS:
- TWO separate APIs: legacy (waves-api.smallest.ai) for v1/v2, new (api.smallest.ai) for v3.1
- Voice catalogs are COMPLETELY DIFFERENT per model version (zero overlap)
- v3.1 returns raw PCM (not WAV) — must wrap in WAV header
- Smallest HANGS on unknown voice names instead of erroring
- v3.1 Hindi voices: maithili, advika, aisha, ishani, yuvika, sana, divya, avni, kavya, zoya, aanya, sameera, sunidhi, srishti, sakshi, chinmayi (female)
- v3.1 Male Hindi: devansh, neel, arjun, vivaan, gaurav, hitesh, vaibhav, kunal, siddharth, mohit, mihir, aarush, parth

### Sarvam STT:
- saaras:v3 is recommended (saarika:v2.5 is being deprecated)
- en-IN mode: correctly transcribes English but translates Hindi to English
- hi-IN mode: correctly transcribes Hindi but translates English to Hindi
- Streaming STT returns HTTP 403 — bypassed with SARVAM_TRY_STREAMING=False

### Deepgram STT:
- Nova-3 uses "keyterm" not "keywords" parameter
- "multi" language mode transcribes both Hindi and English correctly
- Slower than Sarvam (~1200ms vs ~400ms)

## Voice Agent Features Implemented
- Per-agent configurable: STT/TTS/LLM provider, model, voice, speed, language
- Barge-in detection (user can interrupt agent mid-speech, ~160ms detection)
- Silence hangdog (plays "are you there?" then hangs up, pauses during agent speech)
- Welcome audio pre-gen + pre-encode during ring time
- LLM warmup ping during ring time
- Streaming LLM → TTS (sentence-by-sentence audio delivery)
- Filler sounds during LLM thinking time
- Language switching: "natural" mode (LLM decides from prompt), mirror_hinglish, hinglish, primary_only
- English-in-Devanagari detection (handles Sarvam's transliteration)
- Call hours restriction, per-agent caller ID, final/silence messages
- PROVIDER_OPTIONS: single source of truth for all dashboard dropdowns

## SESSION HANDOFF — 2026-08-12 (read this first when resuming)

Everything below is verified against production. The 2026-05-04 handoff
that used to sit here is kept further down as history — treat it as
out of date; most of its "pending" list has shipped.

### Shipped this session (all on `main`, all deployed)

```
d4e70cd fix(agents): duplicate 'smallest' key silently discarded a voice list
43f7748 fix(voice): FMC auto-qualify raised NameError; drop 167 lines of dead code
7713698 feat(voice): Admitverse auto-advances to qualified / dnp / lost after a call
465610c feat(leads): split AI campaign leads onto their own pipeline board
abeae84 feat(banks): lender list moves to the database, admin-managed, no deploy
f45bf37 feat(banks): add Poonawalla to the canonical list
26ab987 fix(banks): brand-gate every bank-share endpoint, not just the writes
88b5186 feat(banks): record "lead shared with bank" + per-bank conversation + grid
9c24f5b docs: WhatsApp -> CRM ingest handoff for the bot repo
29966df feat(auth): API_KEYS_ENABLED - lock machine credentials to one deployment
e8f8c84 docs: loan_amount must be a bare number in lakhs on FMC
49aa456 fix(leads): phone dedup ignores formatting, not just the incoming value
683a98c fix(leads): case-insensitive email duplicate check on create
342e7c9 feat(auth): API keys for machine clients + lead identity fixes
```

**1. API keys** (`docs/API_KEYS.md`). `X-API-Key: crmk_live_…`, resolved
inside `get_current_user` so it works on every authenticated route. A key
acts as a service-account *profile*, which is what makes its writes
attributable. Keys can never DELETE (global middleware) and can never
mint keys. `API_KEYS_ENABLED=false` disables the whole surface per
deployment.

**2. Lead identity fixes.** Duplicate-create 400 now returns
`existing_lead_id`. Phone dedup compares the last 10 digits on create,
update AND CSV import (it used to compare a normalised incoming value
against a raw stored column, so `+9170…` sailed past an existing
`70…`). Email dedup is case-insensitive. `update_lead` normalises the
phone, which it never did.

**3. Bank shares + grid.** Extended `lead_banks` (NOT a parallel table)
with `shared_at` / `shared_by` / `source` / `wa_group_id`, plus a new
`lead_bank_messages` table for the per-(lead,bank) WhatsApp conversation.
`GET /leads/bank-share-grid` renders the whole grid in 3 queries.

**4. Lender list is now a DB table** (`banks`), admin-managed via
`POST /leads/banks` — no deploy needed to add a lender. Seeded from
`FMC_BANKS`, which is now only the seed + fallback. 20 entries
(Poonawalla and GyanDhan added). **Fetch it from `GET /leads/banks`;
never hard-code.**

**5. AI vs normal pipeline.** `leads.pipeline` = `ai` | `normal`.
Campaign enrolment sets `ai`; `POST /leads/{id}/pipeline` hands a lead
to a counsellor and future campaigns then skip it. AI board stages are
short: created/contacted/dnp/qualified/lost. `GET /leads/by-stage?
pipeline=ai|normal` returns that board's `stages` array.
**Rule enforced in 3 places: a lead must never sit on a board with no
column for its stage.**

**6. Auto-stage after every AI call** — the thing that used to need
manual work:

| outcome | FMC | Admitverse |
|---|---|---|
| no pickup | `dnp` | `dnp_pre_qualified` |
| no pickup x12 | `lost` | `lost` |
| not interested | `lost` | `lost` |
| interested | `qualified` +task | `qualified` +task |
| interested, later | `opportunity` | `opportunity` |
| spoke, inconclusive | no-op | `connected` |
| at processing+ | untouched | untouched |

### Production state (verified 2026-08-12)

- **FMC** ~10.6k leads. 6,635 on the AI board / 4,009 normal.
  1,575 leads that were stranded at the dead legacy stage `lead`
  (no Kanban column, no valid transitions) were repaired to `created`.
- **Admitverse** 20,823 leads. Campaign
  `"11th Aug _ All Unique Data _ Sep'26 intake"` is **active**:
  19,688 leads, ~1,900 dialled, **68 converted (3.58%), $0.22 per
  qualified lead**. ~17,800 still pending.
- Sandbox tenant `integration-sandbox` exists in the FMC database for the
  WhatsApp bot to test against.

### ⚠️ Open items — nothing here is done

1. **Plivo balance ~$12** against ~17,800 pending AV calls (needs ~$95).
   When it empties the account suspends and returns 401 — every call
   fails, exactly like the Austria EL campaign did.
2. **`API_KEYS_ENABLED=false` not yet set** on the Admitverse Railway
   service.
3. **CSV import silently truncates at 5,000 rows.** A 19,688-row upload
   imported 5,000 with no error and looked successful. Should hard-fail.
   This cost a full day.
4. **Transcript loss, 64% of connected calls.** ROOT CAUSE FOUND:
   `CallState.get_full_transcript()` only emits completed user→agent
   pairs, so **the agent's own speech is never stored**. A 40-second call
   to voicemail produces an empty transcript and is indistinguishable
   from a call that never connected. And `_save_summary_background` only
   runs `if state.transcript_segments`, so those calls get no summary, no
   sentiment, no auto-qualification. **Fix: store agent turns too, then
   add voicemail detection.** Not done — it touches the live call
   pipeline mid-campaign.
5. **8% of Plivo calls: "Error Reaching Answer URL"** — separate, smaller
   problem (only ~35 calls show the fast-drop signature). Needs Railway
   logs to confirm; CLI auth is expired.
6. **68 converted AV leads are unassigned.** Nobody owns them.
7. Frontend specs written but NOT built: `docs/BANK_SHARE_GRID_FRONTEND_PROMPT.md`,
   `docs/AI_PIPELINE_FRONTEND_PROMPT.md`.
8. **CRM-UI loan-amount input traps non-numeric values** — a stored
   `"7.5 Lakh"` cannot be edited at all (every keystroke including
   backspace is silently rejected). 15 FMC leads still in that state.

### Credentials (on this machine, not in git)

```
~/fmc-live-credentials.txt        FMC live API key + service account
~/fmc-sandbox-credentials.txt     sandbox tenant
~/av-pre-import-snapshot.json     AV state before the 13k import
~/av-qualified-move-backup.json   the 46 leads moved to qualified
~/austria-el-pre-reset-backup.json
```

### Testing reality

DB-backed pytest against Supabase Korea is **effectively unusable** —
runs were killed at 10-40 minutes repeatedly. New tests are deliberately
DB-free: `tests/test_auto_stage.py` (37, 0.2s),
`tests/test_api_keys.py` (32, 0.1s). `tests/test_pipelines.py` (14) and
`tests/test_bank_shares.py` (38) are DB-backed and slow.
**`tests/conftest.py` still references `UserRole.TELECALLER`**, renamed in
May 2026 — this breaks ~102 tests and has done since then.

A `pyflakes` sweep now reports **zero undefined names** in `app/`. It
found the FMC `Task` NameError; worth re-running after any voice.py work.

## SESSION HANDOFF — 2026-05-04 (read this first when resuming)

### Where we are right now (mid-session, system may crash — saved 2026-05-04)

Multi-tenant rebuild in progress: FMC + Admitverse from one codebase, two
deployments each on Railway + Vercel. **See memory file
`multi_tenant_setup_2026_05.md` for the full URL/env-var map.**

**Phases status:**
- ✅ Phase 1 — Rebrand original "Admitverse" → "FundMyCampus"
- ✅ Phase 2 — Admitverse backend deployed (Railway `pretty-insight`,
  new Supabase `mhlfbkjpyrabpeoyqsbs`, all 13 env vars set, 14 tables
  bootstrapped, admin `ashmita@admitverse.com` seeded with role=admin)
- 🟡 Phase 3 — Admitverse frontend deployed at `avcrm-alpha.vercel.app`,
  login confirmed — **but FMC frontend at `crm-ui-pearl.vercel.app` has
  wrong NEXT_PUBLIC_APP_NAME ("Admitverse CRM"), needs Vercel UI fix**
- 🟡 Phase 4 — Brand chooser at `/welcome` route deployed (commits
  ccfa1cc + fc42ad9). Chooser FMC link points to dead `be-crm.vercel.app`
  — needs update to `crm-ui-pearl.vercel.app`
- ❌ Phase 5 — Per-brand pipeline customization. **User shared 17-stage
  Admitverse pipeline; I asked 11 clarifying questions; awaiting answers.
  See `admitverse_pipeline_proposal.md` memory.**

**Bootstrap of fresh Supabase project — 4 tricky bits already solved:**
1. Empty alembic baseline d2bd1aba9cb6 doesn't actually create tables —
   we now detect a fresh DB and `Base.metadata.create_all` instead, then
   `alembic stamp head`. See `app/db/bootstrap.py`.
2. asyncpg `__asyncpg_stmt_N__` collisions on pgbouncer transaction-mode
   — patched `Connection._get_unique_id` to UUID names in
   `app/db/session.py` and `alembic/env.py`.
3. `companies.slug` is NOT NULL UNIQUE — auto-derive from name in
   `scripts/seed_admin.py`.
4. 8 ENUM types pre-created via `DO $$ CREATE TYPE … EXCEPTION
   duplicate_object $$;` because models use `create_type=False`.

**Latest commits on main:**
- `aed90aa` — seed_admin.py slug fix
- `ed90445` — bootstrap fresh DB with ENUMs + 13 tables (Admitverse)
- `5213de3` — await migration bootstrap
- `377dd80` — UUID-named asyncpg statements for pgbouncer
- `7ffd0a6` — alembic merge two heads + statement_cache_size
- `5eccf37` — auto-run alembic on Railway startup

CRM-UI repo:
- `fc42ad9` — allow /welcome chooser to bypass auth gate
- `ccfa1cc` — brand chooser landing page

### Original 2026-04-30 handoff continues below

### Recap of recent shipped work
Multiple deploys to `main` since Apr 25. All live in production now:

1. **CRM audit pack** (commit `85310f0`) — 13 correctness fixes, reports
   accuracy, campaign progress > 100% bug, soft-delete trends. Connected
   metric jumped 1 → 536 in dashboard.
2. **AI summary URL bug** (commit `0e2d20d`) — `/chat/completions` was
   posting to OpenRouter's marketing site. Fixed to `/api/v1/chat/completions`.
   This was THE root cause of every empty summary in production.
3. **Silent-failure surfacing** — `_analyze_call` now logs specific reason
   on each failure path (timeout, 429, JSON parse, etc.) and saves a marker
   string `[AI summary unavailable: <reason>]` instead of empty.
4. **AI summary precision upgrade** (commit `8761961`) — structured
   extraction now returns `loan_amount`, `college`, `study_location`,
   `course`, `intake`, `banks_tried`, `objections`, `next_action` plus the
   summary. Stored in `lead.custom_fields["ai_last_call"]`.
5. **Name handling pro flow** — agent asks for name when no name on file,
   remembers it via conversation history, post-call extracts name and
   writes back to `lead.full_name`. Welcome variants live; `_NAME_PLACEHOLDERS`
   set covers `Lead`/`there`/`you`/`sir`/etc.
6. **Stricter Qualified gate** — `_auto_update_lead_stage` now requires
   transcript ≥500 chars AND ≥3 user turns BEFORE allowing
   `connected → qualified_lead`. Prevents the 5 false-qualified leads we
   saw from "User: Can you speak?" transcripts.
7. **Campaign CSV upload** accepts phone-only rows (commit `0728f06`) —
   name column is now optional. Empty names get the placeholder `"Lead"`
   which the agent's no-name welcome resolves.
8. **Pipeline integrity audit + fixes** (commit `28e2953`):
   - `_auto_update_lead_stage` falls back to admin/manager when lead has
     no `assigned_agent_id` and no `created_by` (was silently skipping
     1,025 leads — 17%).
   - `LeadStageLog.created_at` server default changed `now()` →
     `clock_timestamp()` so multi-step transitions get distinct timestamps.
9. **Manual handoff** — 13 leads identified by deep transcript audit
   were promoted to `qualified_lead` (11 stage moves, 2 already qualified,
   8 placeholder names rewritten to real names: Diraj, Navaneethan, etc.).

### Most-recent campaign run (`whatsapp campigen`, Apr 28)
- 570 leads, 427 dialed, 100% pickup, 414 lasted >10s, $1.33 total spend
- Only 60 transcripts captured of 414 connected calls (~14%) — see
  Critical 1 below
- Sentiment dist: 78% neutral / 15% negative / 7% positive (skewed by
  short transcripts + the OLD vague prompt; should improve next campaign)

### What's live but NOT yet in DB schema ⚠️

Migration `d5e6f7a8b9c0_lead_stage_logs_clock_timestamp.py` was committed
and pushed but has not been applied to prod yet. The model now expects
`clock_timestamp()` as the column default; the actual DB still has `now()`.

**To apply:**
```bash
cd "/Users/asourav/Desktop/Companies website/BE-CRM"
.venv/bin/alembic upgrade head
```

Until this runs, multi-step auto-stage transitions will keep generating
collided timestamps in `lead_stage_logs.created_at`.

### Failed task: dedupe_leads --apply (Apr 29 → Apr 30)

The dedupe job to merge 441 duplicate phones (882 lead rows → 441) hung
on Supabase Korea connection drops from the local laptop (familiar pattern;
3+ hours wall, ~9s of CPU). Process was killed; **zero leads were deleted**
based on the silent stdout / 0-byte log.

User can verify state with:
```bash
cd "/Users/asourav/Desktop/Companies website/BE-CRM"
.venv/bin/python -m scripts._check_dedupe_progress
```

Should report `Active leads: 6036, Phones with duplicates: 441` if no
changes happened (confirming clean state). Then we either:
- (a) Build an admin endpoint `POST /admin/dedupe-leads` so dedupe runs
  on Railway servers (zero latency to Supabase Korea)
- (b) Run via `railway shell` after installing Railway CLI
- (c) Accept duplicates and clean later

The script `scripts/dedupe_leads.py` is correct and tested in dry-run.
Just doesn't survive the laptop → Korea network for a multi-hour batch.

### Permission setting reminder

`/Users/asourav/.claude/settings.local.json` should contain:
```json
{
  "permissions": {
    "allow": [
      "Bash(npx tsc:*)",
      "Bash(git push origin main)"
    ]
  }
}
```

This enables `git push origin main` from within the agent. Agent cannot
self-edit this file (self-modification block); user must add manually.

### Real underlying AI summary cause — RESOLVED

The mystery of "144 of 540 calls had sentiment" was the OpenRouter URL
bug (item 2 above). Calls were posting to the marketing site, getting
HTML back, parsing as JSON, failing silently, defaulting to neutral/low.
After the URL fix, fresh calls return real sentiment + structured
extraction. NOT a rate-limiting issue.

## Pending Work — by owner

### CRM backend — DB / data tasks

🔴 **Critical**
1. **Apply Alembic migration** — `alembic upgrade head` to apply
   `clock_timestamp()` server default for `lead_stage_logs.created_at`.
2. **Dedupe 441 duplicate leads** — script exists (`scripts/dedupe_leads.py`)
   but hangs from local. Need to run via Railway shell OR build an admin
   endpoint. Plan: build `POST /admin/dedupe-leads` route, hit once via
   curl, takes <30s server-side.
3. **Backfill 354 missing transcripts** — campaign-call connections that
   produced no transcript (86% of connected calls). Worth investigating
   Sarvam STT silent-fail OR hangup-flush ordering.
4. **Meta webhook cross-tenant leak** — `app/services/meta_webhook_service.py`
   creates leads without `company_id`, duplicate check is global, LeadSource
   and round-robin agent picked from any tenant. Per-tenant webhook URL
   OR form_id→company mapping needed.

### CRM backend — code tasks

🟠 **High**
5. JWKS cache never expires — Supabase key rotation breaks all logins
   (`app/core/security.py`). Add 15-min TTL + KID-miss refetch.
6. Plivo webhook signature silent-fails on URL mismatch — should HARD-fail
   in production (`app/api/v1/voice.py`).
7. Zero cross-tenant attack tests in `tests/`. Add `tests/test_tenant_isolation.py`.
8. **DNC table** — 8 phones explicitly asked not to be called during
   the Apr 28 campaign (legal risk under DPDP Act). Add `do_not_call`
   table; campaign worker auto-skip on dispatch. The 8 numbers:
   `+918668143799`, `+917017718614`, `+918688042499`, `+917003991306`
   (Somudeep), `+917732099920`, `+919718561322`, `+919778239899`,
   `+918508515030`.
9. **Voicemail detection** — Sarvam transcribes voicemail prompts
   verbatim ("Person you're trying to reach is not available..."). 10
   such calls in last campaign were counted as "Connected". Add a regex
   classifier; mark `call_status='voicemail'` so reports stop counting
   them.

🟡 **Medium**
10. Rate limits cover only 4 of ~80 routes. Apply `@limiter.limit(...)`
    globally to CRUD writes (~20/min) and CSV import (~2/min).
11. Add DB unique partial index `(company_id, phone) WHERE NOT is_deleted`
    on leads. Catch IntegrityError in CSV/manual create paths. Prevents
    future duplicates of the kind we just fought.
12. Task `overdue` auto-flip — verify `check_overdue_tasks()` actually
    runs and updates the status column.
13. Campaign denormalized stats — counters are now atomic but no nightly
    reconciliation job.
14. Sentiment failure has no retry / DLQ. Failed parses silently leave
    lead in old stage. (Partially mitigated by URL-bug fix.)
15. No idempotency keys on POST routes. Rapid double-click creates two
    leads.
16. Reports sentiment widget hides 73% of calls (no transcripts). Add
    "No transcript / failed" 4th bucket.

🟢 **Low**
17. Dashboard `total_agents` only counts TELECALLER role.
18. Task compliance is `completed/total` not "% on time".
19. Trends `calls_made` mixes manual + AI + retries — split by `call_type`.
20. `lead.notes` grows unbounded — every AI call appends a block forever.
21. `Campaign.total_leads` denormalised counter has known drift cases —
    one-time recompute job + nightly reconciliation. Currently shows
    inflated numbers when CSV is uploaded twice.

### CRM frontend — separate team's surface area

🔴 Critical
- Supabase anon key committed in `.env.local` — rotate + audit RLS.
- Auth tokens in localStorage — XSS theft risk. Move to httpOnly cookies.
- Client-side JWT decode in `auth-store.ts:61-80` — UI trust bypass.
  Delete fallback.

🟠 High
- `middleware.ts` only checks cookie presence, not validity.
- No CSRF protection — must ship alongside cookie-based auth.

🟡 Medium
- No CSP / security headers in `next.config.ts`.
- `localStorage.clear()` on logout is over-broad.
- 17 `any` types in `CRM-UI/src/`.
- Frontend should detect `[AI summary unavailable:` prefix and render a
  yellow "AI failed" badge with the reason.
- "Last Call Insights" widget on lead detail page reading
  `lead.custom_fields.ai_last_call` — show loan_amount, college,
  intake, next_action as a quick-glance card.

### Voice / AI agent (original "step 2" — audit not started)

Full audit of `app/api/v1/voice.py` and `app/services/voice_engine/**`
mirroring the CRM audit pass. Not yet done.

Voice testing still needed:
1. Filler speed matching (90% of agent speed) — verify against real call
2. "No" detection on first question (STT sometimes garbles it)
3. FundMyCampus does loans for India AND abroad (prompt needs update)
4. Test filler sounds + barge-in (just deployed 805538f)
5. Test Qwen Turbo quality

Voice deferred features:
6. Sarvam streaming STT (HTTP 403) — revisit when Sarvam fixes WS endpoint
7. ElevenLabs TTS integration — in dropdown but not wired
8. Cartesia TTS integration — in dropdown but not wired
9. Per-agent LLM provider routing — only OpenRouter wired
10. Voicemail detection, noise cancellation — DB fields exist, no backend logic
11. Backchanneling ("haan" while user talks) — medium effort
12. Micro-pauses in TTS speech — needs TTS-level SSML support
13. Emotional matching — needs sentiment detection

### Process / infrastructure

14. **Staging environment** — same repo, `staging` branch, separate Railway
    service, separate Supabase project. Stop deploying straight to prod.
15. CI workflow — GitHub Actions running pytest on every PR. None today.
    Local pytest hangs on Supabase Korea connection issues, so CI is the
    only reliable way to run the 134-test suite.
16. Apply Alembic migration on prod DB if not already applied:
    `alembic upgrade head` for the `idx_lead_stage_logs_changed_by` index.

### Missing features (decide MVP-cut vs ship-blocker)

- WhatsApp integration
- Email (task reminders, lead-assigned notifications)
- SMS reminders
- Recurring tasks
- Lead merge
- Ownership transfer on agent deactivation
- GDPR/DPDP "forget me" endpoint
- File upload virus/content validation

## Human Agent Patterns (from 5 real FundMyCampus transcripts)
- Opening: casual, direct, uses user's name first ("Hi Sanket, Ankit bol raha hoon")
- No "do you have a minute?" — jumps straight to business
- Heavy Hinglish: English for numbers/bank names/technical terms, Hindi for connectors
- Natural fillers: "dekho", "matlab", "theek hai", "achha" (NOT "Great!", "Sure!")
- Gives specific rates from memory ("Axis 7%, SBI 7.2%, PNB 7.95")
- Suggests multi-bank strategy ("SBI mein continue rakho, hum PNB mein kar dete hain")
- Explains WHY behind everything (why SBI slow, why no charge, why NBFC expensive)
- Uses social proof ("same case mere paas tha, kal hi Bangalore campus ka kiya")
- "1 kaam kar sakte ho" pattern for suggestions
- Validates before countering ("kharab nahi hai Credila, but aapka college government bank mein listed hai")
- Admits when doesn't know ("mujhe check karna padega")
- Ends with action ("main WhatsApp pe message kar deta hoon")

## Environment Variables (Railway)
Required for voice: PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN, PLIVO_PHONE_NUMBER, SARVAM_API_KEY, SMALLEST_API_KEY, DEEPGRAM_API_KEY, OPENROUTER_API_KEY, VOICE_STREAM_SECRET, BACKEND_URL, APP_ENV=development

## Test Suite
- 134 tests across 11 files (CRM workflows, not voice engine)
- Uses real Supabase DB with transaction rollback
- Run: `.venv/bin/python -m pytest`
