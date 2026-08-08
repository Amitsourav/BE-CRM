# Admitverse Frontend Handoff + QA Checklist

The backend for Admitverse (study-abroad) parity is **live** on both tenants
(deployed 2026-06-17, migrations applied). This doc tells the frontend team
what to build/change, and gives a checklist to verify each piece works.

**Golden rule:** the backend uses ONE shared lead schema for both brands. The
**frontend decides what to render based on the company brand.**
- **Admitverse** → company slug `"admitverse"`
- **FMC (FundMyCampus)** → company slug `"default"`

Detect the brand once (from the logged-in user's company) and gate the
AV-specific UI on it. **FMC screens must not change.**

---

## 1. Lead form — remove "Loan Details" on Admitverse  🔴 (current bug)

Today the AV lead edit/detail form shows FMC's **Loan Details** block (Loan
Amount, Bank Name, Bank Status, Docs Required/Submitted). Those are meaningless
for study-abroad.

**On Admitverse, hide the entire "Loan Details" section** and replace with:
- **Budget** — free-text field bound to `budget` (e.g. "£18,000", "50 lakh"). Backend parses it into `budget_amount` + `budget_currency` automatically.
- **University Applications** — see §3 (the per-university manager).
- **Documents** — the study-abroad checklist from `GET /leads/docs/checklist` (see §5).

✅ **Verify:** Open an AV lead → no Loan Amount / Bank Name / Bank Status fields. Open an FMC lead → Loan Details still present, unchanged.

---

## 2. Kanban cards — new AV fields

The card payload (`/leads/by-stage`) now includes these fields on every lead:
```
budget, budget_amount, budget_currency
primary_university, application_status
application_count, top_applications[]   // [{id, university_name, program, application_status}]
```

**On AV cards, render:** budget, the primary university + its application status,
and an "+N applications" badge (like FMC's "+N banks"). Hide the FMC bank/loan
chips on AV.

✅ **Verify:** An AV lead with 2+ applications shows the primary university, its
status, and a count badge. FMC cards still show bank/loan chips.

---

## 3. University Applications manager (the big new feature)

A lead applies to multiple universities; each tracked separately (analog of
FMC's multi-bank). Build an "Applications" card/section on the AV lead detail.

**Endpoints:**
| Action | Call |
|---|---|
| List | `GET /leads/{id}/applications` |
| Add | `POST /leads/{id}/applications` |
| Update | `PATCH /leads/{id}/applications/{entry_id}` |
| Delete | `DELETE /leads/{id}/applications/{entry_id}` |
| University autocomplete | `GET /leads/universities` (free text allowed) |

**Add fields:** `university_name` (required, autocomplete), `program`, `intake`, `country`, `application_status`, `notes`.

**Status dropdown (`application_status`):**
`applied → shortlisted → offer_received → conditional_offer → unconditional_offer → deposit_paid → cas_received → visa_applied → visa_approved → enrolled` (+ `rejected`, `withdrawn`)

**Offer/admission details** (`application_ref`, `offer_date`, `tuition_fee`, `scholarship_amount`, `deposit_amount`, `deposit_paid_date`, `cas_number`, `visa_status`):
- Only editable once status is `offer_received` or later. Backend returns **400** otherwise → FE should disable/hide these inputs until the status reaches an offer.

✅ **Verify:** Add 2 universities to an AV lead → both appear. Set one to
`offer_received` → offer-detail fields become editable. Try entering offer
details while status is `applied` → blocked (FE disabled, or backend 400 shown
gracefully). Adding the same university twice → friendly "already exists" error.

---

## 4. Kanban filters — AV filter set

`/leads/by-stage` accepts these AV-only filters (FMC's loan/bank/dnp filters are
ignored on AV):
- `application_status` — dropdown of the statuses above
- `university` — text match on primary university
- `budget_min`, `budget_max` (+ `budget_currency`, default `INR`)
- `sort_by`: add `budget_asc` / `budget_desc`

**On AV, the filter bar should show** application-status, university, and budget
filters — NOT loan amount / bank / DNP filters.

✅ **Verify:** AV filter bar shows application/university/budget filters; applying
them changes the board. FMC filter bar unchanged (loan/bank/DNP).

---

## 5. Document checklist — study-abroad list

`GET /leads/docs/checklist` is now brand-aware. On AV it returns the
study-abroad docs (passport, academic transcripts, degree, IELTS/TOEFL, SOP,
LOR, CV, financial). **FE should render the checklist dynamically from this
endpoint** — do not hardcode the loan docs.

✅ **Verify:** AV doc checklist shows passport/IELTS/SOP… (8 items). FMC shows the
loan docs (aadhaar/PAN/ITR…, 6 items).

---

## 6. Dropdowns that go empty / change on AV

- `GET /leads/banks` → returns `[]` on AV. **Hide any bank dropdown on AV.**
- `GET /leads/universities` → AV suggestion list (FMC returns `[]`).
- `GET /leads/lost-reasons` → returns `[]` on AV → render a **free-text** "lost reason" field (FMC shows its locked 21-value dropdown).

✅ **Verify:** AV "Move to Lost" modal = free text box; FMC = dropdown. No bank
dropdown anywhere on AV.

---

## 6b. DNP attempt counter on AV cards

The backend now increments `dnp_count` for AV leads too (when a lead moves into
`dnp_pre_qualified` or `dnp_post_qualified`), same as FMC's `dnp` stage. The
warning-at-5 / auto-lose-at-6 behavior also already applies to AV.

**On AV cards/detail, render the `dnp_count` badge** ("DNP-3") just like FMC does.

✅ **Verify:** Move an AV lead to a DNP stage 3× → card shows "DNP-3". A DNP lead
at 6 attempts auto-moves to Lost (with a notification).

---

## 7. Pipeline columns (no change needed)

The 17 AV stages already existed and are unchanged. Just confirm the board still
renders all of them. (Reports were fixed backend-side; nothing for FE here.)

---

## Master QA checklist (tick to confirm FE is complete)

- [ ] AV lead form: no Loan/Bank/Docs-loan section; Budget field present.
- [ ] FMC lead form: Loan Details unchanged.
- [ ] AV cards show budget + primary university + application status + "+N applications" badge.
- [ ] Applications manager: list / add / edit / delete all work on AV.
- [ ] University autocomplete works and allows free text.
- [ ] Application status dropdown has all 12 values in order.
- [ ] Offer-detail fields locked until status ≥ offer_received.
- [ ] AV Kanban filters = application_status / university / budget (no loan/bank/DNP).
- [ ] budget_asc / budget_desc sort works on AV.
- [ ] AV doc checklist = study-abroad docs (from API, not hardcoded).
- [ ] AV "Lost" reason = free text; FMC = dropdown.
- [ ] No bank dropdown anywhere on AV.
- [ ] FMC fully unchanged across all of the above.

---

## Backend reference (already deployed)
- AV backend: `https://pretty-insight-production.up.railway.app`
- FMC backend: `https://be-crm-production.up.railway.app`
- All endpoints under `/api/v1`. New/changed: `/leads/{id}/applications` (CRUD),
  `/leads/universities`, `/leads/by-stage` (AV filters), `/leads/docs/checklist`
  (brand-aware), `/leads/banks` (`[]` on AV).
