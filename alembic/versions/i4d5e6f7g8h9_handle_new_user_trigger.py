"""trigger to auto-create profiles row from auth.users

Revision ID: i4d5e6f7g8h9
Revises: h3c4d5e6f7g8
Create Date: 2026-05-05

Backend's /auth/register now creates the profiles row, but the frontend
can also create users by talking to Supabase Auth directly (browser →
Supabase JS client → auth.users). When that happens, the backend never
runs and the user has no profiles row → invisible in dashboards / agent
lists / task assignment.

FMC's legacy Supabase masks the issue with a `handle_new_user()` trigger
that fires AFTER INSERT on auth.users and seeds a profiles row. This
migration ports that trigger to any tenant Supabase that doesn't yet
have it (Admitverse, future brands). The trigger is idempotent —
ON CONFLICT DO NOTHING — so backend's own profile insert (which uses
ON CONFLICT DO UPDATE) overrides the trigger's defaults with the
admin-supplied values. Both paths work.

Single-tenant assumption: each Supabase project has exactly one row in
`companies`. The trigger picks that as the default company. If the
project ever has multiple companies, this needs revisiting.
"""
from alembic import op


revision = "i4d5e6f7g8h9"
down_revision = "h3c4d5e6f7g8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.handle_new_user()
        RETURNS TRIGGER AS $$
        DECLARE
          v_company_id UUID;
          v_full_name TEXT;
          v_role TEXT;
          v_role_safe user_role;
          v_phone TEXT;
          v_vertical TEXT;
        BEGIN
          -- Each tenant Supabase has a single companies row (multi-tenant
          -- is handled by separate Supabase projects, not multiple rows
          -- per project). Pick the only company.
          SELECT id INTO v_company_id FROM public.companies LIMIT 1;
          IF v_company_id IS NULL THEN
            -- No company yet (DB initialized but seed_admin not run).
            -- Skip — backend code will create the profiles row later.
            RETURN NEW;
          END IF;

          v_full_name := COALESCE(NEW.raw_user_meta_data->>'full_name', NEW.email);
          v_role := COALESCE(NEW.raw_user_meta_data->>'role', 'telecaller');
          v_phone := NEW.raw_user_meta_data->>'phone';
          v_vertical := NEW.raw_user_meta_data->>'vertical';

          -- Safe enum cast — fall back to telecaller if the metadata
          -- contained a typo / capitalisation / unknown value. Without
          -- this guard the trigger fails and the auth.users INSERT is
          -- rolled back, so the user can't even sign up.
          BEGIN
            v_role_safe := v_role::user_role;
          EXCEPTION WHEN invalid_text_representation THEN
            v_role_safe := 'telecaller'::user_role;
          END;

          INSERT INTO public.profiles
            (id, company_id, email, full_name, role, phone, vertical, is_active)
          VALUES
            (NEW.id, v_company_id, NEW.email, v_full_name,
             v_role_safe, v_phone, v_vertical, true)
          ON CONFLICT (id) DO NOTHING;

          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;
        """
    )

    # Drop any old version of the trigger first so the migration is
    # idempotent across deploys / replays.
    op.execute("DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users")
    op.execute(
        """
        CREATE TRIGGER on_auth_user_created
        AFTER INSERT ON auth.users
        FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users")
    op.execute("DROP FUNCTION IF EXISTS public.handle_new_user()")
