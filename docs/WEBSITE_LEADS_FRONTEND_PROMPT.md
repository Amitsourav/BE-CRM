# Build the Website Leads panel (CRM-UI)

> Hand this whole file to the frontend agent/developer. Backend is live on both brands; nothing here needs backend work.

## 1. Context

Lead forms on our two marketing websites now POST into the CRM. Submissions land in a **review inbox** (`website_submissions`), NOT directly in the pipeline — these forms are public and collect junk alongside real enquiries.

A counsellor triages each one:

```
new  ──Convert──►  becomes a real Lead   (status: converted)
     ──Spam────►   dismissed             (status: spam)
     (automatic)   matched existing lead (status: duplicate)
```

**Right now this data is invisible** — the API works but there is no screen. That's what you're building.

Same codebase serves both brands from separate deployments:

| Brand | CRM frontend | CRM API |
|---|---|---|
| FundMyCampus | crm-ui-pearl.vercel.app | `https://be-crm-production.up.railway.app` |
| Admitverse | avcrm-alpha.vercel.app | `https://pretty-insight-production.up.railway.app` |

Use the existing `NEXT_PUBLIC_API_URL` — do not hardcode.

## 2. Auth & access

- Existing Supabase JWT (`Authorization: Bearer <token>`), same as every other CRM call.
- **Manager+ only** (`admin` or `manager`). Pre-counsellors get 403.
- Hide the nav item entirely for pre-counsellors.

## 3. API reference

All paths prefixed `/api/v1`.

### List
```
GET /website-leads?status=new&form_key=av_contact&q=rahul&page=1&page_size=25
```
| Param | Notes |
|---|---|
| `status` | `new` (default) \| `converted` \| `duplicate` \| `spam`. Pass empty string for all |
| `form_key` | optional, exact match |
| `q` | optional, searches name / email / phone |
| `page`, `page_size` | page_size max 100 |

Response:
```jsonc
{
  "items": [ WebsiteSubmission, ... ],
  "total": 42, "page": 1, "page_size": 25, "total_pages": 2
}
```

`WebsiteSubmission`:
```jsonc
{
  "id": "uuid",
  "form_key": "av_contact",
  "form_name": "AV — Contact Form",
  "source": "website",
  "page": "/contact",
  "tag": "germany-dmat",
  "full_name": "Rahul Sharma",
  "email": "rahul@example.com",
  "phone": "+919876543210",
  "message": "I want to study in Germany",
  "payload": { },              // everything else the form sent — see §6
  "external_id": "sub_abc123",
  "status": "new",
  "lead_id": null,             // set when converted OR matched as duplicate
  "reviewed_by": null,
  "reviewed_at": null,
  "created_at": "2026-07-28T13:26:03Z"
}
```

⚠️ `lead_id` is non-null on a `new` row when the ingest already matched an existing lead by email/phone. **Show an "Already in CRM" badge** in that case — converting will 409.

### Counts (tab badges)
```
GET /website-leads/count
→ { "new": 12, "converted": 30, "duplicate": 3, "spam": 5, "total": 50 }
```

### Forms (filter dropdown)
```
GET /website-leads/forms
→ [ { "form_key": "av_contact", "form_name": "AV — Contact Form", "total": 20, "new": 4 }, ... ]
```

### Single
```
GET /website-leads/{id}   → WebsiteSubmission
```

### Convert → creates a Lead
```
POST /website-leads/{id}/convert
```
Body — **all optional**, omit to use what the form sent:
```jsonc
{
  "assigned_agent_id": "uuid",   // counsellor
  "pre_counsellor_id": "uuid",
  "lead_source_id": "uuid",      // override the auto per-form source
  "full_name": "Corrected Name", // fix typos BEFORE creating the lead
  "email": "fixed@example.com",
  "phone": "9876543210",
  "notes": "Extra context"
}
```
- `201` → returns the created `Lead` object. Navigate to `/leads/{id}`.
- `409` → **an active lead already has this email/phone.** The submission is auto-marked `duplicate` and linked. Show "Already in CRM" with a button to open the existing lead — **not** a red error toast. The `detail` string contains `lead_id=<uuid>`.
- `400` → already converted, or the submission has no email/phone.

### Spam / Reopen
```
POST /website-leads/{id}/spam     → WebsiteSubmission
POST /website-leads/{id}/reopen   → WebsiteSubmission  (undo spam/duplicate)
```

### Assignee dropdown
Reuse the existing `GET /users` list.

## 4. Screens

### 4.1 Nav
Add **"Website Leads"** to the sidebar with a badge showing `count.new`. Badge hidden when 0. Poll counts every 60s or refresh on window focus.

### 4.2 List page

**Tabs:** New (default) · Converted · Duplicate · Spam — each with its count.

**Filters:** form dropdown (from `/forms`, showing `form_name (new count)`), search box (debounce 400ms → `q`), both in the query string so the view is shareable.

**Table columns:**
| Column | Content |
|---|---|
| Name | `full_name` or "—". Badge "Already in CRM" if `lead_id && status === 'new'` |
| Contact | email + phone stacked; click-to-copy |
| Form | `form_name` as a coloured chip, `page` beneath in small grey |
| Message | `message` truncated to ~60 chars, full text on hover |
| Received | relative ("2h ago"), exact on hover |
| Actions | **Convert** (primary) · **Spam** (ghost) — on `new` only |

Row click → detail drawer.

**States:** skeleton rows while loading; empty state per tab ("No new website leads — they'll appear here automatically when someone fills a form on the site"); error state with retry.

### 4.3 Detail drawer

- Header: name, form chip, received time, status
- Contact block: email, phone, both click-to-copy, `tel:`/`mailto:` links
- Message in full
- **All `payload` keys** rendered as a definition list, snake_case → Title Case (see §6)
- **If `payload.admin_url` exists** → prominent button **"Open in FMC admin →"** (new tab). This is a loan application; the full record lives there
- Footer: Convert (primary) · Spam · Reopen (on spam/duplicate only)
- If `lead_id` set → "View linked lead →"

### 4.4 Convert modal

Pre-filled from the submission, all editable:
- Name, Email, Phone (fix typos here — cheaper than editing the lead afterwards)
- Counsellor (assignee dropdown, optional)
- Pre-counsellor (optional)
- Notes (optional)

On success → toast "Lead created" + link to the lead. On 409 → replace the modal body with "This person is already a lead" + button to open it. Optimistically remove the row from the New tab and refresh counts.

## 5. Form keys → labels

Trust `form_name` from the API; this is only for chip colours / grouping.

**Admitverse:** `av_contact`, `av_homepage`, `av_mobile_popup`, `av_gdpi`, `av_gdpi_hero`, `av_germany`, `av_dmat_mock`

**FundMyCampus:** `fmc_contact`, `fmc_hero_modal`, `fmc_eligibility`, `fmc_landing`, `fmc_signup`, `fmc_loan_application`

Suggested emphasis — `fmc_loan_application` and `av_dmat_mock` are the highest-intent; give them a distinct colour.

## 6. `payload` keys you'll see

**Admitverse:** `interested_country`, `study_level`, `subject`, `preferred_time`, `package`, `intake`, `referred_by`, `session_id`, `submitted_at`, `website_lead_id`

**FundMyCampus:** `loan_type`, `loan_status`, `landing_source`, `referral_code`, `fmc_user_id`, and for loan applications: `loan_amount`, `target_country`, `target_college`, `course_name`, `has_collateral`, `fmc_loan_id`, **`admin_url`**

**Both:** `phone_raw` — present when the visitor typed something unparseable in the phone field. **Display it prominently**; it means the phone column is empty and this is the only contact number.

Render unknown keys generically — forms add fields without frontend changes.

## 7. Acceptance criteria

- [ ] Sidebar item + live "new" badge
- [ ] 4 tabs with counts, form filter, search, pagination
- [ ] Convert creates a lead and navigates to it
- [ ] 409 renders as "already in CRM", never as a generic error
- [ ] Spam and Reopen work; converted rows cannot be spammed
- [ ] `admin_url` renders as a button (FMC loan applications)
- [ ] `phone_raw` surfaced when present
- [ ] Hidden entirely for pre-counsellors
- [ ] Works unchanged on both brand deployments
- [ ] Mobile: table collapses to cards

## 8. Do not

- Do not auto-convert anything. A human decides — that's the entire point of the inbox.
- Do not hide `duplicate` rows; the counsellor needs to see repeat interest.
- Do not build a create/edit form for submissions. They are inbound-only, read + triage.
