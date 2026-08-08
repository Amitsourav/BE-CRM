# Caller-Number Spam-Detection Metrics

**Purpose:** feed a formula that flags when the outbound caller number is being
throttled / spam-flagged by carriers.
**Source:** production `call_attempts` (3,371 calls, 2026-05-07 → 2026-06-15).
**Companion CSVs (in `exports/`):** `metric_daily_trend.csv`,
`metric_duration_buckets.csv`, `metric_rest_periods.csv`, `metric_hour_of_day.csv`.

> ⚠️ **Data-integrity caveat that matters for your formula.** Our DB appears to log
> only *connected* calls — unanswered / NO_ANSWER / BUSY dials are not recorded.
> Spam-flagging shows up first as **rising no-answer + faster declines**, so the single
> most predictive signal is under-captured here. For a production-grade formula, pull
> **Plivo CDRs** (they log ring duration, NO_ANSWER, BUSY, hangup cause per dial).
> Everything below is computed from what we *do* have.

---

## Metric coverage — 8 of 9 (rotation history N/A: only one number, no rotation)

| Metric | Available | Spam relevance |
|---|---|---|
| Call volume | ✅ | velocity / daily load |
| Answer rate | ⚠️ connected>10s only | **drops when flagged** |
| Rejection rate | ⚠️ under-captured | **rises when flagged** |
| Call duration | ✅ | short-call spike = declines |
| Rest periods | ✅ (single number) | **tight cadence triggers filters** |
| Time of day | ✅ | off-hours load looks robotic |
| Unique leads | ✅ | repeat-dial ratio |
| Number age | ✅ observed span | new numbers flagged faster |
| Rotation history | ❌ | no rotation exists |

---

## 1. Duration distribution  → `metric_duration_buckets.csv`
| Bucket | Calls | % |
|---|---|---|
| 0s (no answer?) | 19 | 0.56% |
| 1–3s | 7 | 0.21% |
| 4–5s | 40 | 1.19% |
| 6–10s | 38 | 1.13% |
| 11–20s | 541 | 16.05% |
| 21–30s | 815 | 24.18% |
| 31–60s | 1,524 | 45.21% |
| 61–120s | 112 | 3.32% |
| 121–300s | 270 | 8.01% |
| 300s+ | 5 | 0.15% |

**KEY spam signal — short-call rate:** ≤5s = **1.39%**, ≤10s = **2.52%** (currently low = healthy). A rising trend here is the leading flag indicator.

## 2. Rest periods / inter-call cadence  → `metric_rest_periods.csv`
| Gap between consecutive calls | Count | % |
|---|---|---|
| **<5s** | 1,976 | **58.64%** |
| 5–15s | 12 | 0.36% |
| 15–30s | 1,142 | 33.89% |
| 30–60s | 215 | 6.38% |
| 1–5min | 7 | 0.21% |
| 5–30min | 8 | 0.24% |
| 30min+ | 10 | 0.30% |

Median gap = **3s**; avg 1,004s (skewed by idle days between campaigns). **When actively
dialing, ~59% of calls fire <5s apart — a very tight cadence, the #1 carrier spam trigger.**

## 3. Velocity (burst rate)
- Max calls in one clock-**hour**: **299**
- Max calls in one clock-**minute**: **9**

## 4. Daily trend (the core artifact)  → `metric_daily_trend.csv`
Columns: `date_ist, calls, unique_leads, answer_rate_pct_gt10s, short_call_rate_pct_le5s,
short_call_rate_pct_le10s, failed, avg_duration_s, median_duration_s, peak_hour_ist,
sentiment_neg_pct_of_analyzed`

| Date | Calls | Leads | Ans% | ≤5s% | AvgDur |
|---|---|---|---|---|---|
| 2026-05-08 | 932 | 924 | 98.6% | 0.4% | 41.0 |
| 2026-05-14 | 1,593 | 1,540 | 94.8% | 2.5% | 39.5 |
| 2026-05-30 | 829 | 823 | 99.2% | 0.2% | 39.3 |
| *(+ 7 low-volume days)* | | | | | |

Only **10 active calling days**. No sustained decline in answer rate yet → number does
not appear flagged in this window. The formula should watch for answer% trending **down**
while ≤5s% and failed trend **up** day-over-day.

## 5. Time of day  → `metric_hour_of_day.csv`
Active 11:00–20:00 IST, peak 15:00–16:00. Near-zero off-hours calling (good — off-hours
robotic patterns raise suspicion).

## 6. Number age / activity span
- First call 2026-05-07 · last call 2026-06-15 · **39-day observed span**, 10 calling days.
- ⚠️ Provisioning date not stored — this is *observed-activity* age, not true number age.

---

## Recommendations to make the spam formula robust
1. **Log the from-number** on every `call_attempt` (add `from_number` column) — enables
   true per-number metrics once you rotate numbers.
2. **Ingest Plivo CDRs** (ring duration, NO_ANSWER, BUSY, hangup cause) — supplies the
   real answer/rejection signal our DB drops. This is the biggest accuracy win.
3. **Record number provisioning date** for true "number age."
4. Formula inputs that ARE reliable today: daily volume, cadence/rest-period distribution,
   velocity (calls/min, calls/hour), duration distribution, time-of-day concentration.
