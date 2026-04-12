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

## Pending Work

### Critical:
1. **Test filler sounds + barge-in** — just deployed (805538f), needs real call testing
2. **Test Qwen Turbo** — just switched, needs quality verification
3. **Prompt quality refinement** — new consultant-style prompt based on 5 real human agent transcripts. Key patterns: casual Hinglish, specific bank rates, no fillers like "Great!", explain WHY, give strategy not checklist.

### Deferred:
4. Sarvam streaming STT (HTTP 403) — revisit when Sarvam fixes their WS endpoint
5. ElevenLabs TTS integration — in dropdown but not wired
6. Cartesia TTS integration — in dropdown but not wired
7. Per-agent LLM provider routing — only OpenRouter wired
8. Voicemail detection, noise cancellation — DB fields exist, no backend logic
9. Backchanneling ("haan" while user talks) — medium effort
10. Micro-pauses in TTS speech — needs TTS-level SSML support
11. Emotional matching — needs sentiment detection

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
