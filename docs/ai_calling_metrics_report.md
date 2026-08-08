# AI Calling Agent — Metrics Report

**Source:** production Supabase DB (`call_attempts` + `campaigns` + `campaign_leads`)
**Window:** 2026-05-07 → 2026-06-15 (40 days)
**Pulled:** 2026-07-01 · read-only

---

## Scorecard — 8 of 9 metrics available

| Metric | Status | Value |
|---|---|---|
| Call volume | ✅ | **3,371 calls** (~84/day) |
| Answer rate | ⚠️ partial | **96.8%** connected — *internal >10s definition, not true pickup* |
| Rejection rate | ⚠️ partial | **3.0%** failed |
| Call duration | ✅ | avg **40.2s**, median **31s** |
| Rest periods | ✅ | median gap **3s**, **58.6%** of calls <5s apart |
| Time of day | ✅ | active 11:00–20:00 IST, peak 15:00–16:00 |
| Unique leads contacted | ✅ | **3,275** |
| Number age | ✅ | **39-day** observed span (2026-05-07 → 06-15) |
| Rotation history | ❌ | no number rotation exists (single static caller ID) |

---

## 1. Call Volume
- **3,371** total calls — 3,355 AI-campaign (99.5%) + 16 test/manual
- **~84 calls/day** across the 40-day span
- **3,275 unique leads** contacted (avg **1.03** attempts/lead — effectively one-shot dialing)

## 2. Answer Rate / Rejection Rate  *(from campaign counters)*
| Campaign | Status | Leads | Called | Connected | Failed |
|---|---|---|---|---|---|
| gmat 4500 | paused | 4,494 | 2,538 | 96.1% | 3.6% |
| 28th May domestic | completed | 823 | 831 | 98.9% | 1.0% |
| Shivam Reassignment | paused | 554 | 0 | — | — |
| **Total** | | **5,871** | **3,369** | **96.8%** | **3.0%** |

> ⚠️ **"Connected" = call lasted >10s, NOT a telephony pickup.** No-answer/busy/rejected
> dials appear not to be recorded (only ~19 of 3,371 rows have zero duration), so the
> true pickup-vs-dial rate is unknown without Plivo CDRs. Treat 96.8% as a
> *conversation-quality* rate, not "how many people picked up."

**Failure reasons (only 8 hard errors):** 6× stuck-in-calling (recovered),
1× destination region barred, 1× Plivo timeout.

## 3. Call Duration (rows with duration > 0)
- avg **40.2s** · median **31s** · min 3s · max **613s (10.2 min)**
- total talk time: **2,245 minutes**
- **96.9%** of calls lasted >10s

## 4. Time of Day (IST)
| Hour | Calls | | Hour | Calls |
|---|---|---|---|---|
| 09:00 | 2 | | 15:00 | **541** (peak) |
| 10:00 | 15 | | 16:00 | 528 |
| 11:00 | 226 | | 17:00 | 378 |
| 12:00 | 278 | | 18:00 | 295 |
| 13:00 | 270 | | 19:00 | 265 |
| 14:00 | 272 | | 20:00 | 299 |

Effectively no calls before 11:00 or after 20:00. Peak load 15:00–16:00.

## 5. Reach & Retry
- Unique leads: **3,275** · unique phone numbers: **3,275**
- Attempts/lead: 0×=2,592 (uncalled backlog), 1×=3,220, 2×=45, 3×=14
- Lead outcomes: 55.3% completed · 44.1% pending (uncalled) · 0.5% failed

## 6. Cost (under-recorded)
- Recorded on only 941 of 3,371 rows (~28%) → **$19.44** (avg $0.021/call)
- Campaign denormalized cost: **$18.96**

## 7. Sentiment (transcripts captured on only 28%)
- Analyzed: 941 rows — 89.1% neutral · 9.7% negative · 1.3% positive
- **72% of calls have no transcript/sentiment** (known capture gap)

---

## 8. Rest Periods / Inter-call Cadence  *(single caller number)*
Gap between consecutive calls (the number's dialing cadence):

| Gap | Count | % |
|---|---|---|
| **<5s** | 1,976 | **58.6%** |
| 5–15s | 12 | 0.4% |
| 15–30s | 1,142 | 33.9% |
| 30–60s | 215 | 6.4% |
| 1–5min | 7 | 0.2% |
| 5–30min | 8 | 0.2% |
| 30min+ | 10 | 0.3% |

- Median gap **3s**, avg 1,004s (skewed by idle days between campaigns).
- Burst velocity: up to **9 calls/min**, **299 calls/hour**.
- ⚠️ ~59% of calls fire <5s apart — a tight cadence, the #1 carrier spam trigger.

## 9. Number Age / Activity Span
- First call **2026-05-07**, last call **2026-06-15** → **39-day observed span**, 10 active calling days.
- ⚠️ Provisioning date of the number is not stored — this is *observed-activity* age, not true number age.
- Rotation history: **N/A** — one static caller ID; no rotation events to record.

---

## Notable findings (beyond the requested metrics)
1. **Uncalled backlog:** 2,592 enrolled leads (44%) never dialed — gmat 4500 paused with
   ~1,956 leads unqueued; "Shivam Reassignment" (554 leads) never run.
2. **Retries barely fire** — max 3 attempts; effectively a one-shot dialer.
3. **Transcript capture gap** — 72% of connected calls have no transcript, so sentiment
   and AI summary cover only ~28% of calls.
4. **Answer-rate integrity gap** — unanswered dials appear not to be logged; needs
   Plivo CDR reconciliation for a trustworthy pickup rate.

## Data-quality caveats & the 1 metric still missing
- **Rotation history** — the only fully unavailable metric: there is one static caller ID
  and no rotation events to log. Requires building a caller-number pool first.
- **Answer / rejection rate** are partial — computed as connected>10s, not true telephony
  pickup. Unanswered / NO_ANSWER / BUSY dials are not logged in our DB. For a trustworthy
  pickup rate, ingest **production Plivo CDRs** (local `.env` holds a test Plivo account
  with 0 CDRs).
- **Number age** is *observed-activity* span, not true provisioning age (not stored).
- To harden all of the above: log `from_number` per `call_attempt`, ingest Plivo CDRs,
  and record each number's provisioning date.
