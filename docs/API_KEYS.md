# API Keys — machine credentials

Long-lived, revocable credentials for integrations that call the CRM
24/7 and cannot hold a human's password.

Added 2026-08-05. Implementation: `app/models/api_key.py`,
`app/core/api_key.py`, `app/dependencies.py`, `app/core/middleware.py`,
`app/api/v1/api_keys.py`, migration `g7b8c9d0e1f2`.

---

## What a key is

A key **acts as a service-account profile** rather than carrying
permissions of its own. Two consequences worth understanding:

- **Scope follows the profile's role.** A key bound to a `role="admin"`
  profile has admin scope (including unscoped lead search); one bound to
  a `pre_counsellor` sees only that account's own leads.
- **Writes are attributable.** Every audit column in the schema
  (`leads.created_by`, `lead_remarks.author_id`,
  `lead_stage_logs.changed_by`, `activity_logs.user_id`) FKs to
  `profiles.id`, so the integration's writes show up under the service
  account's name — distinguishable from any human's, with no schema
  change.

Only the SHA-256 of a key is stored. The plaintext is returned once, at
creation, and cannot be recovered.

---

## Using one

Send it in the `X-API-Key` header. It works on **every authenticated
endpoint** — the key is resolved inside `get_current_user`, which every
route already depends on, so nothing is wired per-endpoint and new
routes inherit it automatically.

```bash
curl https://be-crm-production.up.railway.app/api/v1/leads \
     -H "X-API-Key: crmk_live_…"
```

If both `X-API-Key` and `Authorization: Bearer` are sent, **the API key
wins** — a caller sending a key is asking to act as the machine
principal, and silently preferring a co-present human token would
attribute the write to the wrong identity.

### Two hard limits

**1. API keys cannot DELETE.** Every `DELETE` request carrying an
`X-API-Key` header is rejected with 403 before routing, before auth, and
before any DB work (`ApiKeyDeleteGuardMiddleware`). This is global, so
DELETE routes that don't exist yet are covered the moment they're added.

```json
{"detail": "API keys are not permitted to delete. This credential is restricted to read and write operations."}
```

Note this covers soft deletes too — `DELETE /leads/{id}` and
`DELETE /users/{id}` are both soft in this codebase and both blocked. A
future destructive operation exposed under a non-DELETE verb would need
its own check.

**2. API keys cannot manage API keys.** `/api-keys/*` requires a human
admin login. A leaked key that could mint further keys, or lift its own
expiry, would escalate rather than stay containable.

---

## Minting a key

Admin only, human login only.

**Step 1 — create the service account** (once per integration):

```bash
POST /api/v1/auth/register
{"email": "whatsapp-ingest@fundmycampus.com",
 "password": "…", "full_name": "WhatsApp Ingest Service", "role": "admin"}
```

Give it a name that reads clearly in an audit trail — it is what will
appear against every write the integration makes.

**Step 2 — find its profile id:** `GET /api/v1/users?role=admin`

**Step 3 — mint:**

```bash
POST /api/v1/api-keys
{"profile_id": "c6124358-…", "name": "WhatsApp group ingest (prod)"}
```

```json
{
  "id": "78b937be-…",
  "name": "WhatsApp group ingest (prod)",
  "key_prefix": "crmk_live_FA2e1l",
  "profile_email": "whatsapp-ingest@fundmycampus.com",
  "profile_role": "admin",
  "is_active": true,
  "api_key": "crmk_live_FA2e1l51VYp5_…"
}
```

`api_key` appears in this response and nowhere else, ever. Store it
before closing the connection.

Optional `expires_at` (ISO 8601) sets a hard expiry; omit it and the key
lives until revoked.

---

## Listing and revoking

```
GET  /api/v1/api-keys?include_revoked=false     # secrets never returned
POST /api/v1/api-keys/{key_id}/revoke
```

Revocation takes effect on the key's **next request** — there is no
cached credential to wait out. It is idempotent.

Revoke is a `POST`, not a `DELETE`: the row is kept so the audit trail
can still explain which credential made which writes. (And keys can't
issue DELETEs anyway.)

**Revoking a key and disabling its account are independent.** Revoke the
key and the service account survives; deactivate the account
(`DELETE /users/{id}`, which is a soft deactivate) and every key bound to
it stops working at once. Neither touches any human's access.

---

## Rotation

There is no in-place rotation. Mint the replacement, deploy it, then
revoke the old one — overlapping so there is no gap:

1. `POST /api-keys` → new key
2. update the integration's env var, redeploy
3. confirm `last_used_at` on the new key is advancing
   (`GET /api-keys`)
4. `POST /api-keys/{old_id}/revoke`

`last_used_at` is throttled to at most one write per key per minute
per process, to keep an UPDATE off the hot path of every request. Treat
it as approximate — it answers "is this key still in use", not "exactly
when was its last call".

---

## Failure modes

Every rejection returns the same `401 {"detail": "Invalid API key"}`, so
a caller cannot tell which check failed. The specific reason is logged
server-side under `API_KEY_AUTH_FAILED`:

| Logged reason | Meaning |
|---|---|
| `unknown_key` | No such key |
| `revoked` | `is_active=false` or `revoked_at` set |
| `expired` | Past `expires_at` |
| `missing_profile` | Service account row gone |
| `inactive_service_account` | Profile `is_active=false` |
| `company_mismatch` | Profile moved tenants since the key was minted — refused as a cross-tenant write |

A malformed value (one not starting `crmk_`) is rejected before the
database is touched.

Successful auth logs `API_KEY_AUTH ok key_id=… name=… profile_id=…
company_id=…`; a blocked delete logs `API_KEY_DELETE_BLOCKED path=…`
under the `audit` logger.

---

## What did not change

- JWT auth is untouched — same verification path (`decode_jwt`, extracted
  from `verify_jwt` so both credential types share it), same 30-second
  profile cache, same behaviour for every existing client.
- No endpoint's permissions changed. A key is subject to exactly the
  role checks its profile would face on a human login, plus the two
  extra restrictions above.

One behavioural note: a request with **no credentials at all** now
returns **401** `{"detail": "Not authenticated"}` where it previously
returned **403** (FastAPI's `HTTPBearer` auto-error). 401 is the correct
code for absent credentials, and clients treating 401 as "log in again"
are unaffected — but anything branching specifically on 403-means-no-token
should be checked.
