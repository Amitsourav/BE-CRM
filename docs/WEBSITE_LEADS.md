# Website Leads — integration guide

How a form fill on a marketing website becomes a lead in the CRM.

```
  website form
      │  POST /api/v1/internal/website/ingest   (X-Internal-Secret)
      ▼
  website_submissions   status = new        ← nothing else happens yet
      │
      │  human opens the Website Leads panel
      ├── Convert ──► Lead (+ per-form LeadSource)   status = converted
      ├── Spam    ──► dismissed                      status = spam
      └── (auto)  ──► matched an existing lead       status = duplicate
```

**A submission is not a lead.** It sits in the inbox until someone converts
it. That's deliberate: these forms are public, so they collect junk.

Each brand posts to **its own** backend — same code, two deployments:

| Brand | Website | Backend to POST to |
|---|---|---|
| FundMyCampus | fundmycampus.com | `https://be-crm-production.up.railway.app` |
| Admitverse | admitverse.com | the AV Railway URL (`pretty-insight` service) |

---

## 1. Environment variables

**On each CRM backend (Railway):**

```
WEBSITE_LEAD_SECRET=<long random string — DIFFERENT per brand>
```

Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
If unset, the endpoint falls back to `INTERNAL_META_SECRET`; setting a
dedicated value is better so rotating one doesn't break the other.

**On each website (Vercel):**

```
CRM_API_URL=<that brand's backend URL>
CRM_WEBSITE_LEAD_SECRET=<the same secret as above>
```

Do **not** use `NEXT_PUBLIC_` — the secret must stay server-side. Every
call below goes through a Next.js route handler, never the browser.

---

## 2. The endpoint

```
POST {CRM_API_URL}/api/v1/internal/website/ingest
Headers: Content-Type: application/json
         X-Internal-Secret: {CRM_WEBSITE_LEAD_SECRET}
```

Body — only `form_key` plus one of `email`/`phone` are required:

```jsonc
{
  "form_key":   "av_contact",           // REQUIRED, stable machine id
  "form_name":  "AV — Contact Form",    // shown in the panel; names the LeadSource
  "full_name":  "Rahul Sharma",
  "email":      "rahul@example.com",    // email OR phone required
  "phone":      "9876543210",           // any format; normalized to +91…
  "message":    "I want to study in Germany",
  "source":     "website",
  "page":       "/contact",             // which page the form was on
  "tag":        "germany-dmat",
  "external_id": "sub_abc123",          // your id — makes retries idempotent
  "extra_fields": {                     // anything else, kept verbatim
    "interested_country": "Germany",
    "study_level": "Masters",
    "utm_source": "google"
  }
}
```

Responses:

| Code | Meaning |
|---|---|
| `201` | Stored. `{"status":"ok","submission_id":"…","matched_lead_id":null}` |
| `201` | `status:"duplicate_submission"` — same `external_id` already stored; you retried, nothing was double-created |
| `403` | Bad/missing `X-Internal-Secret` |
| `422` | No `email` and no `phone` |
| `429` | Rate limit — 60 requests/minute per IP |

`matched_lead_id` is non-null when someone with that email/phone is
already a lead. Purely informational; the submission is still stored.

---

## 3. Next.js snippet (both sites)

Create one shared helper, then call it from each form's route handler.

`src/lib/crm.ts`:

```ts
type CrmLead = {
  form_key: string
  form_name?: string
  full_name?: string
  email?: string
  phone?: string
  message?: string
  page?: string
  tag?: string
  external_id?: string
  extra_fields?: Record<string, unknown>
}

/** Send a form fill to the CRM. Returns false if CRM isn't configured. */
export async function sendLeadToCrm(lead: CrmLead): Promise<boolean> {
  const url = process.env.CRM_API_URL
  const secret = process.env.CRM_WEBSITE_LEAD_SECRET
  if (!url || !secret) return false          // local dev — skip silently

  const res = await fetch(`${url.replace(/\/$/, '')}/api/v1/internal/website/ingest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Internal-Secret': secret },
    cache: 'no-store',
    body: JSON.stringify({ source: 'website', ...lead }),
  })
  if (!res.ok) throw new Error(`CRM ${res.status}: ${await res.text()}`)
  return true
}
```

A route handler, e.g. `src/app/api/contact/route.ts`:

```ts
import { NextRequest, NextResponse } from 'next/server'
import { sendLeadToCrm } from '@/lib/crm'

export async function POST(request: NextRequest) {
  const body = await request.json()

  if (!body.email && !body.phone) {
    return NextResponse.json({ error: 'Email or phone is required' }, { status: 400 })
  }

  try {
    await sendLeadToCrm({
      form_key: 'av_contact',
      form_name: 'AV — Contact Form',
      full_name: body.name,
      email: body.email,
      phone: body.phone,
      message: body.message,
      page: '/contact',
      external_id: `contact_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`,
      extra_fields: {
        interested_country: body.interestedCountry,
        study_level: body.studyLevel,
        preferred_time: body.preferredTime,
      },
    })
  } catch (err) {
    console.error('[contact] CRM forward failed:', err)
    return NextResponse.json({ error: 'Could not save your details.' }, { status: 500 })
  }

  return NextResponse.json({ success: true })
}
```

For the **static FMC landing page**, there's no server to hide the secret
on, so don't call the CRM from the browser. Either add a tiny Vercel
serverless function that proxies to the CRM, or keep posting to
`Fundmycampus_BE` and forward from there.

---

## 4. Suggested `form_key` values

Keep them stable — they're what the panel filters and reports group by.

**Admitverse**

| Form | `form_key` | `form_name` |
|---|---|---|
| Contact page | `av_contact` | AV — Contact Form |
| dMAT free mock | `av_dmat_mock` | AV — dMAT Mock Signup |
| Cost calculator | `av_cost_calculator` | AV — Cost Calculator |
| CV review | `av_cv_review` | AV — CV Review |
| SOP review | `av_sop_review` | AV — SOP Review |
| SGPA→CGPA tool | `av_sgpa_tool` | AV — SGPA Converter |
| GDPI prep page | `av_gdpi` | AV — GDPI Preparation |
| Germany universities | `av_germany_unis` | AV — Germany Universities |
| AI matching | `av_ai_matching` | AV — AI Course Match |

**FundMyCampus**

| Form | `form_key` | `form_name` |
|---|---|---|
| Contact page | `fmc_contact` | FMC — Contact Form |
| Hero lead modal | `fmc_hero_modal` | FMC — Homepage Modal |
| Eligibility checker | `fmc_eligibility` | FMC — Eligibility Checker |
| Landing page | `fmc_landing` | FMC — Landing Page |

Each becomes a `LeadSource` of type `website` on first conversion, so the
existing sources report breaks down conversion **per form**.

---

## 5. Panel API (for whoever builds the screen)

All Manager+ (admin or manager), all tenant-scoped.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/website-leads?status=new&form_key=&q=&page=1&page_size=25` | Inbox list, newest first. `status=` empty for all |
| `GET` | `/api/v1/website-leads/count` | `{new, converted, duplicate, spam, total}` — tab badges |
| `GET` | `/api/v1/website-leads/forms` | Distinct forms + totals — filter dropdown |
| `GET` | `/api/v1/website-leads/{id}` | One submission incl. raw `payload` |
| `POST` | `/api/v1/website-leads/{id}/convert` | → creates the Lead, returns `LeadOut` (201) |
| `POST` | `/api/v1/website-leads/{id}/spam` | Dismiss |
| `POST` | `/api/v1/website-leads/{id}/reopen` | Undo spam/duplicate |

`convert` accepts an optional body to fix data or assign on the way in:

```jsonc
{
  "assigned_agent_id": "uuid",   // counsellor
  "pre_counsellor_id": "uuid",
  "lead_source_id":    "uuid",   // override the auto per-form source
  "full_name": "Corrected Name", // fix a typo before creating the lead
  "email": "…", "phone": "…",
  "notes": "Extra context"
}
```

**409 on convert** means an active lead already has that email/phone. The
submission is marked `duplicate` and linked to the existing lead, so the
UI should show "already in CRM → open lead" rather than an error toast.

On conversion the lead gets `custom_fields`:
`website_form`, `website_form_name`, `website_source`, `website_page`,
`website_tag`, `website_submission_id`, plus every key from `extra_fields`.
The form's `message` becomes the lead's `notes`.

---

## 6. Deploy order

1. Deploy this backend to **both** Railway services. Migration
   `e5f6a7b8c9d0` runs automatically on boot and creates the table +
   adds the `website` source type.
2. Set `WEBSITE_LEAD_SECRET` on both services.
3. Set `CRM_API_URL` + `CRM_WEBSITE_LEAD_SECRET` on both Vercel sites.
4. Point the forms at the endpoint, one form at a time.
5. Watch the panel: `GET /api/v1/website-leads/count`.

Smoke test:

```bash
curl -sS -X POST "$CRM_API_URL/api/v1/internal/website/ingest" \
  -H "Content-Type: application/json" \
  -H "X-Internal-Secret: $CRM_WEBSITE_LEAD_SECRET" \
  -d '{"form_key":"smoke_test","full_name":"Smoke Test","email":"smoke@test.com"}'
```

Expect `201 {"status":"ok","submission_id":"…"}`, then mark it spam in the
panel.
