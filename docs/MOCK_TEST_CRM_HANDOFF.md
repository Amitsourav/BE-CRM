# Mock Test → Admitverse CRM (hand-off prompt)

> Give this whole file to the agent/dev working in the **MockBE** repo.
> No CRM-side changes are needed — the endpoint is already live.

## 1. What we're doing

When a mock-test student completes their profile (name + phone + course), create a lead in the Admitverse CRM so a counsellor can follow up.

The lead lands in the CRM's **Website Leads** review inbox as `status='new'`. A counsellor converts the real ones into pipeline leads. Nothing is auto-converted.

**Fire exactly once — on profile completion, not on login.**

```
student logs in with email            → send nothing
student saves name + phone + course   → send ONE lead   ✅
```

Rationale: an email-only student can't be called. We wait until there's a phone number, then send one complete lead. (Students who log in and never finish their profile therefore won't appear in the CRM — a known, accepted trade-off.)

## 2. Endpoint

```
POST https://pretty-insight-production.up.railway.app/api/v1/internal/website/ingest
Content-Type: application/json
X-Internal-Secret: <CRM_WEBSITE_LEAD_SECRET>
```

That host is the **Admitverse** CRM backend. Reuse the **same secret** already configured for the admitverse.com website (`WEBSITE_LEAD_SECRET` on the Railway `pretty-insight` service) — one secret per CRM deployment, and the mock test writes into the same Admitverse database.

Ask Amit for the value; do not invent a new one.

## 3. Environment variables (MockBE)

```
CRM_API_URL=https://pretty-insight-production.up.railway.app
CRM_WEBSITE_LEAD_SECRET=<the AV secret>
```

Server-side only. **Never** prefix with `NEXT_PUBLIC_`/`VITE_` or expose to the browser — anyone with this secret can inject leads into the CRM.

When either variable is missing, skip the call and log a warning. Local dev then behaves normally without a CRM.

## 4. Payload

```jsonc
{
  "form_key": "av_mock_test",              // REQUIRED, exactly this string
  "form_name": "AV — Mock Test Signup",
  "full_name": "Rahul Sharma",
  "email": "rahul@example.com",            // email OR phone required
  "phone": "9876543210",                   // any format; CRM normalizes to +91…
  "source": "mock_test",
  "page": "/profile",
  "external_id": "<student user id>",       // idempotency — see §6
  "extra_fields": {
    "mock_user_id": "<student user id>",
    "courses": ["MBBS", "Engineering"],     // whatever your course field holds
    "target_intake": "Winter 2027",
    "city": "Faridabad"
    // add anything else useful — unknown keys are stored as-is
  }
}
```

Only `form_key` plus one of `email`/`phone` are mandatory. Everything in `extra_fields` is preserved verbatim and shown to the counsellor, so include any course/intake/exam data you have.

### Responses

| Code | Meaning | Action |
|---|---|---|
| `201` `{"status":"ok","submission_id":"…"}` | Stored | Done |
| `201` `{"status":"duplicate_submission"}` | Same `external_id` already sent | Treat as success |
| `403` | Bad/missing secret | Log loudly — leads are being lost |
| `422` | No email and no phone | Don't send until you have one |
| `429` | Rate limit (60/min per IP) | Retry later |

`matched_lead_id` in the response is non-null when that person is already a lead in the CRM. Informational only — the submission is still stored.

## 5. Where the code goes

**MockBE, server-side**, in the profile-save handler — right after you've committed the student's profile to your own database.

Your database stays the system of record. The CRM call is an extra, and it must **never** break or delay the student's save:

- wrap it in try/catch and swallow errors (log them)
- do not `await` it on the critical path if your framework can defer (Cloudflare `waitUntil`, Next.js `after()`, or just fire-and-forget with a `.catch()`)
- if the CRM is down, the student still saves their profile successfully

```ts
// crm.ts
export async function sendMockLeadToCrm(lead: {
  full_name?: string | null
  email?: string | null
  phone?: string | null
  external_id: string
  extra_fields?: Record<string, unknown>
}): Promise<void> {
  const url = process.env.CRM_API_URL
  const secret = process.env.CRM_WEBSITE_LEAD_SECRET
  if (!url || !secret) {
    console.warn('[crm] not configured — skipping mock-test lead')
    return
  }
  if (!lead.email && !lead.phone) {
    console.warn('[crm] no email or phone — skipping')
    return
  }

  try {
    const res = await fetch(`${url.replace(/\/$/, '')}/api/v1/internal/website/ingest`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Internal-Secret': secret },
      body: JSON.stringify({
        form_key: 'av_mock_test',
        form_name: 'AV — Mock Test Signup',
        source: 'mock_test',
        page: '/profile',
        ...lead,
      }),
    })
    if (!res.ok) throw new Error(`CRM ${res.status}: ${await res.text()}`)
    console.log('[crm] mock-test lead sent')
  } catch (err) {
    // Never rethrow — the student's profile save must not fail because
    // the CRM is unreachable. Watch these logs; a run of them means
    // leads are silently missing.
    console.error('[crm] mock-test lead FAILED:', err)
  }
}
```

Call it once profile completion succeeds:

```ts
await saveProfile(userId, data)          // your existing logic

sendMockLeadToCrm({
  full_name: data.name,
  email: user.email,
  phone: data.phone,
  external_id: userId,
  extra_fields: {
    mock_user_id: userId,
    courses: data.courses,
    target_intake: data.intake,
  },
}).catch(() => {})                        // fire-and-forget
```

## 6. Idempotency — important

Use the **student's user id** as `external_id`. The CRM enforces one submission per `(company, external_id)`.

That gives you: a student editing their profile five times produces **one** CRM lead, not five. A retried request after a timeout produces one. You do not need to track "have I already sent this student" yourself — just always pass the user id.

## 7. Testing

```bash
curl -sS -X POST "$CRM_API_URL/api/v1/internal/website/ingest" \
  -H "Content-Type: application/json" \
  -H "X-Internal-Secret: $CRM_WEBSITE_LEAD_SECRET" \
  -d '{"form_key":"av_mock_test","form_name":"AV — Mock Test Signup",
       "full_name":"Test Student","email":"mocktest@example.com",
       "phone":"9000000011","external_id":"test-user-1",
       "extra_fields":{"courses":["MBBS"],"target_intake":"Winter 2027"}}'
```

Expect `201 {"status":"ok","submission_id":"…"}`. Run it twice — the second must return `duplicate_submission` and **not** create a second row.

Then confirm it appears in the Admitverse CRM → **Website Leads** → New tab, as "AV — Mock Test Signup". Mark the test row as spam afterwards.

## 8. Do not

- Do not call this from the browser — the secret must stay on the server.
- Do not fire on login/signup, only on profile completion (decision made 2026-07-30).
- Do not let a CRM failure fail the student's request.
- Do not create a second secret; reuse the Admitverse one.
- Do not send `null`/empty strings for phone — omit the field instead.
