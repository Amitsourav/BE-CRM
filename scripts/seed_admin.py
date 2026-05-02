"""Seed the first admin user + company on a fresh Supabase project.

Reads env from a .env-style file and:
  1. Creates a Supabase Auth user (auto-confirmed) via the Admin API
     — or finds the existing one if the email is already registered
  2. Inserts the company row (if missing) into `companies`
  3. Inserts the profile row (if missing) into `profiles` linked to the
     auth user with role='admin'

Idempotent: safe to run twice with the same email. The FMC project has
a DB trigger that auto-creates a profile from the auth user; this script
also handles the case where the trigger doesn't exist yet (new tenants),
so it works on both setups.

Usage:
    .venv/bin/python -m scripts.seed_admin \\
        --email you@admitverse.com \\
        --password "yourstrongpass" \\
        --company-name "Admitverse" \\
        --full-name "Admin" \\
        --env-file .env.admitverse

If --env-file is omitted, .env is used.
If --email/--password are omitted, you'll be prompted interactively.

Required vars in the env file:
    SUPABASE_URL                 — https://xxxxxxxx.supabase.co
    SUPABASE_SERVICE_ROLE_KEY    — admin key (NOT the anon key)
    SUPABASE_DB_URL              — postgres connection string
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from getpass import getpass
from pathlib import Path

import httpx
from dotenv import load_dotenv


def _load_env(env_file: str) -> None:
    path = Path(env_file)
    if not path.exists():
        sys.exit(f"❌ env file not found: {env_file}")
    load_dotenv(path, override=True)


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.exit(f"❌ missing required env var: {name}")
    return val


async def _create_or_get_auth_user(email: str, password: str, full_name: str) -> str:
    """Create the user via Supabase Admin API. Returns the user UUID.
    If the user already exists, returns the existing UUID."""
    base = _require("SUPABASE_URL").rstrip("/")
    service_key = _require("SUPABASE_SERVICE_ROLE_KEY")
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{base}/auth/v1/admin/users",
            headers=headers,
            json={
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {"full_name": full_name},
            },
        )
        if resp.status_code in (200, 201):
            user_id = resp.json()["id"]
            print(f"  ✓ created auth user {email} → {user_id}")
            return user_id

        # Email already registered — find and return existing user id
        if resp.status_code == 422 and "already" in resp.text.lower():
            print(f"  ℹ auth user {email} already exists, looking up id…")
            list_resp = await client.get(
                f"{base}/auth/v1/admin/users",
                headers=headers,
                params={"email": email},
            )
            list_resp.raise_for_status()
            users = list_resp.json().get("users", [])
            for u in users:
                if u.get("email", "").lower() == email.lower():
                    print(f"  ✓ found existing user → {u['id']}")
                    return u["id"]
            sys.exit(f"❌ user {email} reported as existing but admin list didn't return them")

        sys.exit(f"❌ create user failed: HTTP {resp.status_code}\n{resp.text}")


async def _seed_company_and_profile(
    user_id: str,
    email: str,
    full_name: str,
    company_name: str,
) -> None:
    """Connect to the DB and insert/upsert company + profile."""
    # Patch asyncpg statement names — required for Supabase pgbouncer.
    import asyncpg.connection as _ac
    def _unique_id(self, prefix):
        return f"__asyncpg_{prefix}_{uuid.uuid4().hex}__"
    _ac.Connection._get_unique_id = _unique_id

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    db_url = _require("SUPABASE_DB_URL")
    if "?" in db_url:
        db_url = db_url.split("?")[0]
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(
        db_url,
        connect_args={
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
        },
    )

    # URL-safe slug for the company (used as a unique key in companies).
    # Lowercased, alphanumerics + hyphens only.
    import re
    company_slug = re.sub(r"[^a-z0-9]+", "-", company_name.lower()).strip("-")

    try:
        async with engine.begin() as conn:
            # 1. Company — keyed by slug because that's the unique column
            row = (await conn.execute(
                text("SELECT id FROM companies WHERE slug = :s"),
                {"s": company_slug},
            )).first()
            if row:
                company_id = row[0]
                print(f"  ℹ company '{company_name}' (slug={company_slug}) already exists → {company_id}")
            else:
                company_id = (await conn.execute(
                    text("INSERT INTO companies (name, slug) VALUES (:n, :s) RETURNING id"),
                    {"n": company_name, "s": company_slug},
                )).scalar_one()
                print(f"  ✓ created company '{company_name}' (slug={company_slug}) → {company_id}")

            # 2. Profile linked to the auth user
            existing = (await conn.execute(
                text("SELECT id, role FROM profiles WHERE id = :id"),
                {"id": user_id},
            )).first()
            if existing:
                # Trigger may have created it (FMC case); make sure the
                # role is admin and the company link is right.
                await conn.execute(
                    text(
                        "UPDATE profiles SET role = 'admin', is_active = true, "
                        "company_id = :cid, full_name = COALESCE(NULLIF(full_name, ''), :name) "
                        "WHERE id = :id"
                    ),
                    {"cid": company_id, "name": full_name, "id": user_id},
                )
                print(f"  ✓ ensured role=admin + company link for existing profile")
            else:
                await conn.execute(
                    text(
                        "INSERT INTO profiles (id, company_id, email, full_name, role, is_active) "
                        "VALUES (:id, :cid, :email, :name, 'admin', true)"
                    ),
                    {
                        "id": user_id,
                        "cid": company_id,
                        "email": email,
                        "name": full_name,
                    },
                )
                print(f"  ✓ created admin profile for {email}")
    finally:
        await engine.dispose()


def _prompt(label: str, secret: bool = False) -> str:
    return getpass(f"{label}: ") if secret else input(f"{label}: ").strip()


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--email", help="Admin email (also Supabase login)")
    parser.add_argument("--password", help="Admin password")
    parser.add_argument("--full-name", default="Admin", help="Display name (default: Admin)")
    parser.add_argument("--company-name", default="Admitverse", help="Company row name (default: Admitverse)")
    parser.add_argument("--env-file", default=".env", help="Path to env file (default: .env)")
    args = parser.parse_args()

    print(f"\n→ Loading env from {args.env_file}")
    _load_env(args.env_file)
    print(f"  SUPABASE_URL = {os.environ.get('SUPABASE_URL', '(unset)')[:50]}…")
    print()

    email = args.email or _prompt("Admin email")
    password = args.password or _prompt("Admin password", secret=True)
    if not email or not password:
        sys.exit("❌ email and password are required")

    print(f"→ Step 1: create/lookup auth user {email}")
    user_id = await _create_or_get_auth_user(email, password, args.full_name)
    print()

    print(f"→ Step 2: seed company '{args.company_name}' + admin profile")
    await _seed_company_and_profile(
        user_id=user_id,
        email=email,
        full_name=args.full_name,
        company_name=args.company_name,
    )
    print()

    print("✅ Done. You can now log in with:")
    print(f"     email:    {email}")
    print(f"     user_id:  {user_id}")


if __name__ == "__main__":
    asyncio.run(main())
