# Admitverse CRM (FundMyCampus) — Backend

## Project Overview
AI-powered CRM for FundMyCampus, an education loan consultancy that helps Indian students get loans for studying in India AND abroad. The core feature is an **AI voice agent** that makes outbound calls to leads, speaks in natural Hinglish, and provides education loan consultation.

**Stack:** FastAPI + async SQLAlchemy + Supabase PostgreSQL + Plivo telephony + multiple STT/TTS/LLM providers via OpenRouter.

## Deployment
- **Backend:** Railway at `https://be-crm-production.up.railway.app`
- **Frontend:** Vercel at `https://be-crm.vercel.app`
- **Database:** Supabase PostgreSQL (Korea region)
- **GitHub:** `https://github.com/Amitsourav/BE-CRM.git`

## Current Voice Agent Config (Agent: "Priya - FundMyCampus Counselor")
- **STT:** Sarvam AI, saaras:v3, language_code=en-IN
- **LLM:** GPT-4.1 Mini via OpenRouter (~800-1000ms TTFB)
- **TTS:** Smallest AI, lightning-v3.1, voice=sana, speed=1.2
- **Language style:** "natural" (LLM decides language from prompt, no code-level detection)
- **Telephony:** Plivo, +918031136711
- **Filler sounds:** "Right.", "Okay.", "Sure.", "Yeah." (short) / "Right, so.", "Okay, so." (long) — at 90% of agent speed
- **Barge-in:** 36 frames (~720ms) with slow-decay (not hard reset)
- **Total turn latency:** ~2-3s with filler, best turns ~1.7s

## Voice Engine Architecture

### Pipeline flow (per turn):
```
User speaks → silence detection (500ms endpointing)
→ Sarvam STT (~400ms)
→ filler sound plays instantly (short: "Right." / long: "Okay, so.")
→ LLM streaming via OpenRouter (~800ms first token)
→ TTS per sentence via Smallest v3.1 (~350ms)
→ mulaw → Plivo playAudio
```

### Key voice engine files:
- `app/api/v1/voice.py` — WebSocket handler, Plivo webhooks, barge-in, silence watchdog, filler playback
- `app/services/voice_engine/pipeline.py` — STT→filler→LLM→TTS streaming pipeline
- `app/services/voice_engine/llm_service.py` — OpenRouter LLM (batch + streaming), language policy
- `app/services/voice_engine/sarvam_stt.py` — Sarvam STT (batch only, streaming disabled due to 403)
- `app/services/voice_engine/sarvam_tts.py` — Sarvam TTS (batch + streaming, 29 bulbul:v3 voices)
- `app/services/voice_engine/smallest_tts.py` — Smallest AI TTS (v1 legacy API + v3.1 new API at api.smallest.ai)
- `app/services/voice_engine/deepgram_stt.py` — Deepgram STT (nova-3, keyterm support)
- `app/services/voice_engine/filler_sounds.py` — Pre-generated filler sounds, short/long, speed-matched, no-repeat
- `app/services/voice_engine/call_state.py` — Per-call state, history capped at 20 entries, welcome context
- `app/services/voice_engine/http_clients.py` — Persistent httpx clients with HTTP/2 enabled
- `app/services/voice_engine/stt_router.py` — Routes STT by agent.stt_provider
- `app/services/voice_engine/language_detector.py` — Hindi/English detection (150+ word sets + English-in-Devanagari)
- `app/services/voice_engine/audio_utils.py` — WAV/mulaw conversion, silence detection
- `app/schemas/ai_agent.py` — PROVIDER_OPTIONS (all dropdown catalogs), agent schemas
- `app/services/pricing_service.py` — Per-minute cost calculation per provider

## Latency Optimizations Done (Apr 8-14, 2026)
Started at 7-8s welcome + 4-5s per turn. Now at ~2s welcome + ~2-3s per turn.

1. Smallest TTS v3.1 instead of Sarvam (1500ms → 350ms)
2. LLM warmup during ring time (eliminates 5s cold start)
3. Pre-gen welcome audio + pre-encode to mulaw+base64 (instant welcome)
4. Streaming LLM → TTS pipeline (sentence-by-sentence)
5. Filler sounds (short/long, speed-matched, no "Hmm")
6. Persistent HTTP clients with HTTP/2 (connection reuse)
7. Non-blocking DB writes in WS handler
8. Agent cache on CallState (skip duplicate DB lookups)
9. Early-flush first LLM clause on comma
10. Remove redundant persona_rule/length_rule injection (~300 tokens saved per request)
11. Cap conversation history at 20 entries (prevents unbounded growth)
12. Pre-seed agent cache from /voice/outbound

## What DIDN'T work (don't retry):
- Qwen Turbo — fast (500ms) but can't follow complex prompts (repeats intro, ignores answers)
- Qwen3-14b — empty responses, 3s+ TTFB
- GPT-4.1 Nano — too dumb for consultant prompt
- Smallest Lightning v2 for Hindi — terrible pronunciation
- Smallest with Sarvam voice names (kavya) — hangs on unknown voices
- Sarvam STT hi-IN mode — translates English to Hindi
- Sarvam STT en-IN mode — translates Hindi to English (but works for LLM natural mode)
- Deepgram STT — correct transcription but 3x slower than Sarvam (~1200ms vs 400ms)
- Sarvam TTS streaming sub-chunks — no improvement for short sentences
- "Hmm" as TTS filler — TTS pronounces it as "H-M-M", sounds robotic
- tts_speed parameter — Plivo may not respect different playback speeds

## Important API Notes

### Smallest TTS:
- TWO separate APIs: legacy (waves-api.smallest.ai) for v1/v2, new (api.smallest.ai) for v3.1
- Voice catalogs are COMPLETELY DIFFERENT per model version (zero overlap)
- v3.1 returns raw PCM (not WAV) — must wrap in WAV header before pipeline
- Smallest HANGS on unknown voice names instead of erroring
- v3.1 Hindi female voices: maithili, advika, aisha, ishani, yuvika, sana, divya, avni, kavya, zoya, aanya, sameera, sunidhi, srishti, sakshi, chinmayi

### Sarvam TTS (bulbul:v3):
- Safe fallback voice: "simran"
- v1/v2 voices (meera, pavithra, etc.) return HTTP 400 on v3

### Sarvam STT:
- saaras:v3 is recommended (saarika:v2.5 is being deprecated)
- Streaming STT returns HTTP 403 — bypassed with SARVAM_TRY_STREAMING=False

### Deepgram STT:
- Nova-3 uses "keyterm" not "keywords" parameter

## Current System Prompt Structure
Top rules (at very top, LLM follows these most reliably):
1. No filler words at start of reply (thinking sound plays automatically)
2. Max ONE filler per reply, mid-sentence only
3. No name overuse (once in entire conversation)
4. No Hindi fillers at start (achha, dekho, matlab)
5. No re-introduction on "hello" after 2+ exchanges
6. If user hasn't answered after 2 attempts, try different approach
7. No bullet points/lists (phone call, not text)
8. If user says "not interested", accept immediately and close

Then: consultant persona, conversation flow (interest check → college → rates → strategy → close), bank rate table (Tier 1/2/3), explanations with WHY, objection handling, rules.

## Human Agent Patterns (from 5 real FundMyCampus transcripts)
- Casual Hinglish: "1 kaam kar sakte ho — SBI mein apply karo, hum PNB karwa dete hain"
- Specific rates from memory: "Axis 7%, SBI 7.2%, PNB 7.95%"
- "1 baar GPT pe calculate karke dekho" (tells user to verify independently)
- No "Great!", "Sure!", "Wonderful!" — uses "dekho", "matlab", "theek hai"
- Multi-bank strategy: SBI for best rate (15 days), PNB/Axis for speed (4-5 days)
- Aggregator model: "200 cases dete hain bank ko, bank humein pay karta hai"

## Recently Shipped (Apr 23–25, 2026 — branch fix/crm-audit-items merged to main)

13 CRM correctness fixes + reports accuracy + campaign 115% bug. See merge
commit `8531df0`. Highlights:
- Campaign worker: `_active_count` crash fixed; naive datetimes replaced
  with tz-aware now_utc; missing AI agent now pauses + notifies; atomic
  SQL increments on counters; dispatch exception path no longer leaves
  leads stuck in 'calling'; soft-delete filter on dial queue.
- Post-call pipeline: tenant derived from call row; sentiment-driven stage
  transitions wired (lead→called, called+positive→connected,
  connected+positive→qualified_lead) with LeadStageLog audit entries;
  narrow excepts.
- CSV import: per-tenant pg_advisory_lock blocks duplicate-phone race.
- Soft-delete filter added across call_service, stage_machine, task_service,
  campaign_service, report_service (all 9 lead queries).
- Reports: `started_at IS NOT NULL` for connected count (Connected card
  jumped from 1 → 536 in prod after deploy); conversion rate now
  `won/(won+lost)`; tenant filter on agent reports' joined tables;
  trends won/lost charts now exclude soft-deleted.
- Campaigns: progress_pct = connected/total capped at 100% (was showing
  115%); `attempts` exposed as separate field.
- `add_business_days` actually skips Sat/Sun (was a calendar-day function).
- Date helpers: `start_of_today`/`end_of_today` default to IST.
- New migration: `lead_stage_logs.changed_by` index.

## Pending Work

### CRM — backend (still open from CRM_AUDIT_REPORT.md)

🔴 **Critical**
1. **Meta webhook cross-tenant leak** — `app/services/meta_webhook_service.py`
   creates leads without `company_id`, duplicate check is global across all
   tenants, LeadSource and round-robin agent picked from any tenant. Per-tenant
   webhook URL OR form_id→company mapping needed. **Most dangerous unfixed bug.**

🟠 **High**
2. JWKS cache never expires — Supabase key rotation breaks all logins until
   manual redeploy (`app/core/security.py`). Add 15-min TTL + KID-miss refetch.
3. Plivo webhook signature silent-fails on URL mismatch — should HARD-fail
   in production (`app/api/v1/voice.py`).
4. Zero cross-tenant attack tests in `tests/`. Add `tests/test_tenant_isolation.py`.

🟡 **Medium**
5. Rate limits cover only 4 of ~80 routes (only auth + voice). Apply
   `@limiter.limit(...)` globally to CRUD writes (~20/min) and CSV import (~2/min).
6. No DB unique constraint on `(company_id, phone)` for leads. Manual
   create has no dedup. Add a partial unique index, catch IntegrityError
   in service layer.
7. Task `overdue` auto-flip job — verify `check_overdue_tasks()` actually
   runs and updates the status column.
8. Campaign denormalized stats — counters are now atomic but no nightly
   reconciliation. Add a job that recomputes from source of truth.
9. Post-call sentiment failure has no retry / DLQ. Failed parses silently
   leave lead in old stage. Add 3x retry + `sentiment_analysis_failures` table.
10. No idempotency keys on POST routes. Rapid double-click creates two
    leads / two call attempts.
11. Reports sentiment widget hides 73% of calls (the ones without
    transcripts). Add a 4th bucket: "No transcript / failed".

🟢 **Low**
12. Dashboard `total_agents` only counts TELECALLER role — manager-handlers
    invisible. Rename or expand.
13. Task compliance is `completed/total` not "% on time". Either rename
    or compare `completed_at` vs `due_date`.
14. Trends `calls_made` mixes manual + AI + retries — add separate series
    or split by `call_type`.

### CRM — frontend (separate team's surface area, from audit report)

🔴 Critical (frontend team should action)
- Supabase anon key committed in `.env.local` — rotate + audit RLS.
- Auth tokens in localStorage — XSS theft risk. Move to httpOnly cookies.
- Client-side JWT decode in `auth-store.ts:61-80` — UI trust bypass. Delete fallback.

🟠 High
- `middleware.ts` only checks cookie presence, not validity. Validate token edge-side.
- No CSRF protection — must ship alongside cookie-based auth.

🟡 Medium
- No CSP / security headers in `next.config.ts`.
- `localStorage.clear()` on logout is over-broad.
- 17 `any` types in `CRM-UI/src/`.
- No request deduplication on 401 retry.

### Voice / AI agent (original "step 2" — not yet started)

Step 2 of the original plan: full audit of `app/api/v1/voice.py` and
`app/services/voice_engine/**` mirroring the CRM audit pass. Not yet done.

Voice testing still needed:
1. Filler speed matching (just pushed — 90% of agent speed) — verify against real call
2. "No" detection on first question (STT sometimes garbles it)
3. FundMyCampus does loans for India AND abroad (prompt needs update)

Voice deferred features:
4. Backchanneling ("haan" while user talks)
5. Micro-pauses in TTS speech
6. Speed variation (slow for important info, fast for casual)
7. Emotional matching
8. ElevenLabs/Cartesia TTS integration (in dropdown but not wired)
9. Per-agent LLM provider routing
10. Voicemail detection, noise cancellation

### Process / infrastructure

15. **Staging environment** — same repo, `staging` branch, separate Railway
    service, separate Supabase project. Stop deploying straight to prod.
16. CI workflow — GitHub Actions running pytest on every PR. None today
    (PR `Checks` tab was empty). Local pytest hangs on Supabase Korea
    connection issues, so CI is the only reliable way to run the 134-test
    suite.
17. Apply Alembic migration on prod DB: `alembic upgrade head` for the
    new `idx_lead_stage_logs_changed_by` index.

### Missing features (per CRM_AUDIT_REPORT.md §12)

Decide MVP-cut vs ship-blocker:
- WhatsApp integration
- Email (task reminders, lead-assigned notifications)
- SMS reminders
- Recurring tasks
- Lead merge
- Ownership transfer on agent deactivation
- GDPR/DPDP "forget me" endpoint
- File upload virus/content validation

## Environment Variables (Railway)
PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN, PLIVO_PHONE_NUMBER, SARVAM_API_KEY, SMALLEST_API_KEY, DEEPGRAM_API_KEY, OPENROUTER_API_KEY, VOICE_STREAM_SECRET, BACKEND_URL, APP_ENV=development

## Test Suite
- 134 tests across 11 files (CRM workflows, not voice engine)
- Uses real Supabase DB with transaction rollback
- Run: `.venv/bin/python -m pytest`
