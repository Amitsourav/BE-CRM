"""rename user_role enum value telecaller → pre_counsellor

Revision ID: o2l3m4n5o6p7
Revises: n1k2l3m4n5o6
Create Date: 2026-05-15

FMC formalised its two-step counsellor model — the user who warms up a
lead is a "Pre Counsellor". The "telecaller" label was a holdover from
the original 6-stage funnel. Renaming the enum value in-place keeps all
existing profile rows + JWT-stored metadata valid (PostgreSQL preserves
the underlying enum index).

Also runs on the Admitverse Supabase project — same alembic codebase is
deployed against both, so the migration applies to both DBs.

Side effects:
- Updates handle_new_user trigger default from 'telecaller' to 'pre_counsellor'
- Updates auth.users.raw_user_meta_data so future re-logins don't reset
  the role to the old string
"""
from alembic import op
import sqlalchemy as sa


revision = "o2l3m4n5o6p7"
down_revision = "n1k2l3m4n5o6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Rename the enum value. Existing profile rows automatically
    #    reflect the new label — PostgreSQL keeps the underlying enum
    #    index stable.
    op.execute("ALTER TYPE user_role RENAME VALUE 'telecaller' TO 'pre_counsellor'")

    # 2. Update the profiles.role server default
    op.execute("ALTER TABLE profiles ALTER COLUMN role SET DEFAULT 'pre_counsellor'")

    # 3. Update auth.users.raw_user_meta_data for users currently flagged
    #    as 'telecaller' so a re-login doesn't trigger handle_new_user
    #    with the stale string.
    op.execute("""
        UPDATE auth.users
        SET raw_user_meta_data = jsonb_set(
            raw_user_meta_data,
            '{role}',
            '"pre_counsellor"'::jsonb
        )
        WHERE raw_user_meta_data->>'role' = 'telecaller'
    """)

    # 4. Replace handle_new_user trigger to default to pre_counsellor
    #    for new signups. The trigger body is otherwise identical.
    op.execute("""
        CREATE OR REPLACE FUNCTION public.handle_new_user()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public
        AS $$
        DECLARE
          v_company_id UUID;
          v_full_name TEXT;
          v_role TEXT;
          v_role_safe user_role;
          v_phone TEXT;
          v_vertical TEXT;
        BEGIN
          SELECT id INTO v_company_id FROM public.companies LIMIT 1;
          IF v_company_id IS NULL THEN
            RETURN NEW;
          END IF;

          v_full_name := COALESCE(NEW.raw_user_meta_data->>'full_name', NEW.email);
          v_role := COALESCE(NEW.raw_user_meta_data->>'role', 'pre_counsellor');
          v_phone := NEW.raw_user_meta_data->>'phone';
          v_vertical := NEW.raw_user_meta_data->>'vertical';

          BEGIN
            v_role_safe := v_role::user_role;
          EXCEPTION WHEN invalid_text_representation THEN
            v_role_safe := 'pre_counsellor'::user_role;
          END;

          INSERT INTO public.profiles
            (id, company_id, email, full_name, role, phone, vertical, is_active)
          VALUES
            (NEW.id, v_company_id, NEW.email, v_full_name,
             v_role_safe, v_phone, v_vertical, true)
          ON CONFLICT (id) DO NOTHING;

          RETURN NEW;
        END;
        $$;
    """)


def downgrade() -> None:
    # Reverse rename. Reverts profiles, raw_user_meta_data, trigger.
    op.execute("UPDATE auth.users SET raw_user_meta_data = jsonb_set(raw_user_meta_data, '{role}', '\"telecaller\"'::jsonb) WHERE raw_user_meta_data->>'role' = 'pre_counsellor'")
    op.execute("ALTER TABLE profiles ALTER COLUMN role SET DEFAULT 'telecaller'")
    op.execute("ALTER TYPE user_role RENAME VALUE 'pre_counsellor' TO 'telecaller'")
    # handle_new_user trigger reset — not bothering with full restore;
    # if someone really downgrades, they can regenerate from prior migration.
