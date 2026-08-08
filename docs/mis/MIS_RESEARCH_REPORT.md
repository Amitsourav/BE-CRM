# Lead-Provider MIS — Research Report
*Metrics, scorecard, and dashboard design for a lead-vendor performance portal (FMC + Admitverse). Compiled 2026-06-13 from a fact-checked, multi-source deep-research pass (24 sources, 25 claims adversarially verified → 11 confirmed, 14 refuted).*

---

## TL;DR

A per-provider MIS scorecard should be built on **three metric layers**:
1. **Volume & validity** — delivered, valid vs invalid/junk, duplicate rate, return/dispute rate
2. **Funnel performance** — contact rate, qualification rate, conversion/disbursal rate, time-to-first-contact, time-to-qualify
3. **Economics** — CPL, cost-per-qualified-lead, CPA, ROI

The provider scorecard is a **weighted 1–5 composite** (rating × weight, summed, normalized to %). Invalid/junk handling uses **quickly-verifiable refund categories** + a **30-day same-category duplicate window**.

> ⚠️ **Critical finding:** *Every published "industry benchmark" number was REFUTED in verification* — CPL/CPQL figures, conversion-rate tiers, return-rate tiers, dispute-SLA timings, the "5-min response = 21× conversion" stat, the "score ≥70 = hot" cutoff. **Do not hard-code any of these.** The portal must use **configurable internal targets** that FMC/Admitverse set from their own historical data.

---

## 1. Confirmed findings (survived adversarial verification)

| # | Finding | Confidence | Vote |
|---|---|---|---|
| 1 | Organize provider metrics in 3 layers: **volume/validity, funnel, economics**. `CPL = total spend / new leads`; `ROI = (revenue − cost) / cost`. | High | 3-0 |
| 2 | Refund/credit invalid leads under **quickly-verifiable categories**: disconnected phone, undeliverable email, out-of-qualification-parameters (the 3 most defensible), plus wrong-person, out-of-service-area, duplicates, test/solicitation. | High | 3-0 |
| 3 | **Duplicate = same lead previously delivered for the same category within 30 days.** | High | 3-0 |
| 4 | "No response" disputes require a **documented minimum-effort gate** (e.g., 5+ contact attempts over 5+ days, mixed channels) before they're creditable. | Medium | 2-1 |
| 5 | Lead-quality score = weighted composite of **Engagement (40%) + Fit (30%) + Intent (30%)**; for consumer verticals, "Fit" = applicant **eligibility**, not firmographics. Weights are a *starting point*, tune from data. | Medium | 3-0 |
| 6 | Incoming lead data quality can be **graded A–F per lead** on phone validity, email deliverability, address verification, identity-match confidence; average per provider = a data-validity score. | Medium | 3-0 |
| 7 | Overall provider scorecard = **weighted 1–5 composite**: each criterion rated 1/3/5, × its % weight, summed, normalized /500 → %. *(Adopt the structure; choose your own criteria/weights — the specific example weights were refuted.)* | Medium | 3-0 |
| 8 | Monitor **disproportionate return-to-volume concentration** as a red flag (a source with a far higher share of returns than of volume → pause + investigate). Treat as a diagnostic ratio, not a fixed cutoff. | Medium | 2-1 |

## 2. Refuted claims — DO NOT use as fact

- ❌ Cross-industry CPQL $198; EdTech $198 ($125–285); B2B Financial Services $389 — **all refuted**.
- ❌ Fixed **10-day** dispute window; **4h/24h/48h** dispute-resolution SLA — refuted.
- ❌ Return-rate tiers (<5% premium / 5–10% / 10–20% / >20% terminate) — refuted.
- ❌ Conversion-rate benchmarks (8–12% insurance, 2–4% mortgage), contact-rate 45–75% — refuted.
- ❌ "Respond within 5 min → 21× conversion" — refuted.
- ❌ "Score ≥ 70 = high-quality" hard cutoff — refuted.
- ❌ Channel CPL ranges ($53 email → $110 PPC) and referral-vs-social MQL rates — refuted.
- ❌ Specific 5-criterion scorecard weights (Audience 30 / Traffic 25 / …) — refuted.

**Takeaway:** the *methodology* (definitions, formulas, dispute categories, scorecard structure) is sound and transferable; the *numbers* are not. Ship every threshold as a **configurable target field**.

---

## 3. Recommended metric set (adapted to your CRM)

### Layer 1 — Volume & Validity *(per provider · per brand · per period)*
| Metric | Definition |
|---|---|
| Leads Delivered | count of leads from the provider's mapped source(s) |
| Invalid / Junk | leads flagged invalid (bad/disconnected phone, undeliverable email, out-of-parameters) |
| Invalid Rate | invalid / delivered |
| Duplicate Leads | matches an existing lead (same phone) within a **30-day same-brand window** |
| Duplicate Rate | duplicates / delivered |
| **Valid Leads** | delivered − invalid − duplicate |
| Return / Dispute count & rate | *(Phase 2, needs dispute module)* |

### Layer 2 — Funnel Performance
| Metric | Definition (canonical stages) |
|---|---|
| Contacted / Contact Rate | reached *Contacted* ÷ valid |
| Connected / Connect Rate | reached *Connected* ÷ valid |
| Qualified / Qualification Rate | reached *Qualified* ÷ valid |
| Converted (Won) / Conversion Rate | reached *Converted* (FMC disbursed / AV enrolled) ÷ valid — this is **lead-to-sale %** |
| DNP / Lost counts & rates | reached *DNP* / *Lost* ÷ delivered |
| Time-to-First-Contact | avg(contacted_at − created_at) |
| Time-to-Qualify | avg(qualified_at − created_at) |

### Layer 3 — Economics *(needs cost + revenue — Phase 2 / payout module)*
| Metric | Definition |
|---|---|
| CPL | total paid to provider ÷ delivered |
| Cost per Qualified Lead | total paid ÷ qualified |
| CPA | total paid ÷ converted |
| ROI | (revenue from converted − cost) ÷ cost |

> Layer 3 depends on (a) what you pay the provider (payout module) and (b) revenue per disbursal/enrolment (may not be in the CRM yet). Mark as Phase 2; until then show Layers 1–2.

---

## 4. Provider scorecard formula (recommended)

Use the verified **weighted 1–5 composite** structure with criteria chosen for your business. All ratings, weights, and grade bands are **configurable** (defaults below):

| Criterion | How rated 1 / 3 / 5 | Weight |
|---|---|---|
| Lead Validity | from (invalid + duplicate) rate — low rate = 5 | 25% |
| Qualification Rate | qualified ÷ valid | 30% |
| Conversion Rate | converted ÷ valid | 30% |
| Volume Reliability | consistency of delivery vs commitment | 10% |
| Return / Dispute Rate (inverse) | low disputes = 5 *(Phase 2)* | 5% |

```
Score% = Σ(rating_i × weight_i) ÷ 5 × 100
Grade  = A/B/C/D/F by company-set bands  (NOT a fixed ≥70 cutoff)
```

Optionally also compute a **per-lead A–F data-validity grade** (phone valid? email deliverable? in-parameters?) and average it per provider for an at-a-glance incoming-quality signal.

---

## 5. Suggested dashboard layout (provider-facing)

1. **Header strip** — provider name · brand filter (FMC / AV / Both) · date range · **"Data as of HH:MM"**.
2. **KPI cards** — Delivered · Valid % · Qualified · Converted · Conversion Rate · **Scorecard grade**.
3. **Funnel chart** — Delivered → Contacted → Connected → Qualified → Converted (with drop-off %).
4. **Trend chart** — leads delivered & conversions over time (daily / weekly toggle).
5. **Brand split** — FMC vs AV side-by-side (only if provider supplies both).
6. **Quality panel** — invalid rate, duplicate rate, *(disputes — Phase 2)*.
7. **Lead table** — serial no · name · phone · brand · source · current stage · created date · status; filters + CSV export.
8. **Payout / settlement panel** — *Phase 2*.

**Admin (internal) side:** manage providers + logins · map provider → sources (per brand) · set targets (CPL, qualification, return ceiling) · all-providers leaderboard.

---

## 6. Open questions you still need to decide
1. **India-specific targets** — no benchmarks survived; derive CPL / contact / qualification / conversion targets from FMC + Admitverse historical data.
2. **Billing model** — pay-per-lead, pay-per-qualified, pay-per-disbursal/admission, or revenue-share? Drives the Layer-3 metrics + the settlement view. *(No source mapped models→metrics; that mapping is inferred.)*
3. **Dispute window & SLA** — define your own DPDP-compliant, contractually-agreed terms (the 10-day / 4h-24h-48h figures were refuted).
4. **Data granularity & PII masking** — daily-batch vs near-real-time, and which fields (transcripts, agent notes) stay hidden from external providers under the DPDP Act.

---

## 7. Sources
**Confirmed-claim sources:** conXpros (lead-credit policy, primary), boberdoo (refund criteria), MotivatedSellers (returns policy, primary), Umbrex (lead-quality score), Trestle (A–F partner vetting), ReferralCandy (1–5 composite), leadgen-economy (return concentration), levelupleads (CPL/ROI definitions).
**Quality note:** strongest claims are definitional formulas + verbatim vendor policies. Scoring methods rest on single-vendor/blog sources — adopt the *structure*, not the specific weights. Nearly all corroborating vendors are **US** (home services / insurance / real estate / mortgage), **not** Indian edtech/fintech — the framework transfers, the numbers do not.
