-- Admitverse CRM — Complete Database Schema
-- Run this in Supabase SQL Editor

-- ============================================================
-- Extensions
-- ============================================================
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================
-- Enums
-- ============================================================
DO $$ BEGIN
    CREATE TYPE user_role AS ENUM ('admin', 'agent');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE lead_stage AS ENUM ('lead', 'called', 'connected', 'qualified_lead', 'won', 'lost');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE call_disposition AS ENUM ('dnp', 'connected', 'busy', 'switched_off', 'wrong_number', 'callback');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE task_type AS ENUM ('follow_up', 'call', 'meeting', 'document_collection', 'application', 'other');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE task_status AS ENUM ('pending', 'in_progress', 'completed', 'overdue');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE notification_type AS ENUM ('lead_assigned', 'task_created', 'task_overdue', 'dnp_warning', 'dnp_auto_lost', 'stage_changed', 'csv_import_complete', 'general');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE lead_source_type AS ENUM ('csv', 'meta_ads', 'manual', 'whatsapp');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE csv_import_status AS ENUM ('uploaded', 'previewing', 'processing', 'completed', 'failed');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ============================================================
-- 1. profiles — extends auth.users
-- ============================================================
CREATE TABLE IF NOT EXISTS public.profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email TEXT UNIQUE NOT NULL,
    full_name TEXT NOT NULL,
    phone TEXT,
    role user_role NOT NULL DEFAULT 'agent',
    is_active BOOLEAN NOT NULL DEFAULT true,
    vertical TEXT,
    avatar_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- 2. lead_sources
-- ============================================================
CREATE TABLE IF NOT EXISTS public.lead_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    source_type lead_source_type NOT NULL DEFAULT 'manual',
    meta_form_id TEXT,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- 3. leads — core entity
-- ============================================================
CREATE TABLE IF NOT EXISTS public.leads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Identity
    full_name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    alternate_phone TEXT,
    date_of_birth DATE,
    gender TEXT,
    city TEXT,
    state TEXT,
    country TEXT DEFAULT 'India',
    pincode TEXT,
    -- Education
    highest_qualification TEXT,
    stream TEXT,
    passing_year INTEGER,
    college_name TEXT,
    university TEXT,
    percentage NUMERIC(5,2),
    target_degree TEXT,
    target_intake TEXT,
    preferred_countries TEXT[],
    preferred_universities TEXT[],
    -- Pipeline
    current_stage lead_stage NOT NULL DEFAULT 'lead',
    assigned_agent_id UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    lead_source_id UUID REFERENCES public.lead_sources(id) ON DELETE SET NULL,
    call_attempt_count INTEGER NOT NULL DEFAULT 0,
    due_date TIMESTAMPTZ,
    connected_time TIMESTAMPTZ,
    won_time TIMESTAMPTZ,
    lost_time TIMESTAMPTZ,
    lost_reason TEXT,
    -- Meta
    custom_fields JSONB DEFAULT '{}',
    tags TEXT[] DEFAULT '{}',
    notes TEXT,
    -- VoIP extensibility
    last_call_provider TEXT,
    last_call_recording_url TEXT,
    -- Tracking
    created_by UUID REFERENCES public.profiles(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- 4. lead_stage_logs — audit trail
-- ============================================================
CREATE TABLE IF NOT EXISTS public.lead_stage_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id UUID NOT NULL REFERENCES public.leads(id) ON DELETE CASCADE,
    from_stage lead_stage,
    to_stage lead_stage NOT NULL,
    changed_by UUID NOT NULL REFERENCES public.profiles(id),
    conversation_notes TEXT,
    agent_agenda TEXT,
    due_date_set TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- 5. call_attempts
-- ============================================================
CREATE TABLE IF NOT EXISTS public.call_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id UUID NOT NULL REFERENCES public.leads(id) ON DELETE CASCADE,
    agent_id UUID NOT NULL REFERENCES public.profiles(id),
    attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1 AND attempt_number <= 6),
    disposition call_disposition NOT NULL,
    conversation_notes TEXT NOT NULL,
    agent_agenda TEXT NOT NULL,
    due_date_for_next TIMESTAMPTZ,
    -- VoIP extensibility
    call_provider TEXT,
    call_recording_url TEXT,
    external_call_id TEXT,
    call_duration_seconds INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- 6. tasks
-- ============================================================
CREATE TABLE IF NOT EXISTS public.tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id UUID REFERENCES public.leads(id) ON DELETE CASCADE,
    assigned_to UUID NOT NULL REFERENCES public.profiles(id),
    created_by UUID NOT NULL REFERENCES public.profiles(id),
    task_type task_type NOT NULL DEFAULT 'follow_up',
    title TEXT NOT NULL,
    description TEXT,
    status task_status NOT NULL DEFAULT 'pending',
    due_date TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    completion_notes TEXT,
    stage_log_id UUID REFERENCES public.lead_stage_logs(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- 7. notifications
-- ============================================================
CREATE TABLE IF NOT EXISTS public.notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    type notification_type NOT NULL DEFAULT 'general',
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    is_read BOOLEAN NOT NULL DEFAULT false,
    lead_id UUID REFERENCES public.leads(id) ON DELETE SET NULL,
    task_id UUID REFERENCES public.tasks(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- 8. csv_imports
-- ============================================================
CREATE TABLE IF NOT EXISTS public.csv_imports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    uploaded_by UUID NOT NULL REFERENCES public.profiles(id),
    file_name TEXT NOT NULL,
    status csv_import_status NOT NULL DEFAULT 'uploaded',
    total_rows INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    duplicate_count INTEGER DEFAULT 0,
    error_details JSONB DEFAULT '[]',
    column_mapping JSONB DEFAULT '{}',
    raw_headers TEXT[] DEFAULT '{}',
    lead_source_id UUID REFERENCES public.lead_sources(id),
    assigned_agent_id UUID REFERENCES public.profiles(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- 9. activity_logs
-- ============================================================
CREATE TABLE IF NOT EXISTS public.activity_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id UUID,
    old_values JSONB,
    new_values JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- Indexes
-- ============================================================
-- Leads composite indexes
CREATE INDEX IF NOT EXISTS idx_leads_agent_stage ON public.leads(assigned_agent_id, current_stage);
CREATE INDEX IF NOT EXISTS idx_leads_agent_due ON public.leads(assigned_agent_id, due_date);
CREATE INDEX IF NOT EXISTS idx_leads_stage ON public.leads(current_stage);
CREATE INDEX IF NOT EXISTS idx_leads_source ON public.leads(lead_source_id);
CREATE INDEX IF NOT EXISTS idx_leads_created ON public.leads(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_leads_email ON public.leads(email);
CREATE INDEX IF NOT EXISTS idx_leads_phone ON public.leads(phone);

-- Trigram indexes for fuzzy search
CREATE INDEX IF NOT EXISTS idx_leads_name_trgm ON public.leads USING gin(full_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_leads_phone_trgm ON public.leads USING gin(phone gin_trgm_ops);

-- Stage logs
CREATE INDEX IF NOT EXISTS idx_stage_logs_lead ON public.lead_stage_logs(lead_id, created_at DESC);

-- Call attempts
CREATE INDEX IF NOT EXISTS idx_calls_lead ON public.call_attempts(lead_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_calls_agent ON public.call_attempts(agent_id);

-- Tasks
CREATE INDEX IF NOT EXISTS idx_tasks_assigned ON public.tasks(assigned_to, status, due_date);
CREATE INDEX IF NOT EXISTS idx_tasks_lead ON public.tasks(lead_id);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON public.tasks(due_date) WHERE status IN ('pending', 'in_progress');

-- Notifications
CREATE INDEX IF NOT EXISTS idx_notif_user ON public.notifications(user_id, is_read, created_at DESC);

-- CSV Imports
CREATE INDEX IF NOT EXISTS idx_csv_user ON public.csv_imports(uploaded_by, created_at DESC);

-- Activity logs
CREATE INDEX IF NOT EXISTS idx_activity_entity ON public.activity_logs(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_activity_actor ON public.activity_logs(actor_id, created_at DESC);

-- ============================================================
-- Triggers: auto-update updated_at
-- ============================================================
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$ BEGIN
    CREATE TRIGGER set_updated_at BEFORE UPDATE ON public.profiles FOR EACH ROW EXECUTE FUNCTION update_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TRIGGER set_updated_at BEFORE UPDATE ON public.lead_sources FOR EACH ROW EXECUTE FUNCTION update_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TRIGGER set_updated_at BEFORE UPDATE ON public.leads FOR EACH ROW EXECUTE FUNCTION update_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TRIGGER set_updated_at BEFORE UPDATE ON public.tasks FOR EACH ROW EXECUTE FUNCTION update_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TRIGGER set_updated_at BEFORE UPDATE ON public.csv_imports FOR EACH ROW EXECUTE FUNCTION update_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ============================================================
-- Trigger: auto-create profile on auth.users signup
-- ============================================================
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.profiles (id, email, full_name, role)
    VALUES (
        NEW.id,
        NEW.email,
        COALESCE(NEW.raw_user_meta_data->>'full_name', split_part(NEW.email, '@', 1)),
        COALESCE((NEW.raw_user_meta_data->>'role')::user_role, 'agent')
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_new_user();

-- ============================================================
-- Helper function for RLS
-- ============================================================
CREATE OR REPLACE FUNCTION public.get_my_role()
RETURNS user_role AS $$
    SELECT role FROM public.profiles WHERE id = auth.uid();
$$ LANGUAGE sql SECURITY DEFINER STABLE;

-- ============================================================
-- RLS Policies
-- ============================================================
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lead_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lead_stage_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.call_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.csv_imports ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.activity_logs ENABLE ROW LEVEL SECURITY;

-- Profiles
CREATE POLICY profiles_admin ON public.profiles FOR ALL USING (get_my_role() = 'admin');
CREATE POLICY profiles_self ON public.profiles FOR SELECT USING (id = auth.uid());
CREATE POLICY profiles_self_update ON public.profiles FOR UPDATE USING (id = auth.uid());

-- Lead Sources — readable by all, managed by admin
CREATE POLICY lead_sources_read ON public.lead_sources FOR SELECT USING (true);
CREATE POLICY lead_sources_admin ON public.lead_sources FOR ALL USING (get_my_role() = 'admin');

-- Leads
CREATE POLICY leads_admin ON public.leads FOR ALL USING (get_my_role() = 'admin');
CREATE POLICY leads_agent ON public.leads FOR SELECT USING (assigned_agent_id = auth.uid());
CREATE POLICY leads_agent_update ON public.leads FOR UPDATE USING (assigned_agent_id = auth.uid());
CREATE POLICY leads_agent_insert ON public.leads FOR INSERT WITH CHECK (true);

-- Stage Logs
CREATE POLICY stage_logs_admin ON public.lead_stage_logs FOR ALL USING (get_my_role() = 'admin');
CREATE POLICY stage_logs_agent ON public.lead_stage_logs FOR SELECT USING (
    lead_id IN (SELECT id FROM public.leads WHERE assigned_agent_id = auth.uid())
);
CREATE POLICY stage_logs_agent_insert ON public.lead_stage_logs FOR INSERT WITH CHECK (changed_by = auth.uid());

-- Call Attempts
CREATE POLICY calls_admin ON public.call_attempts FOR ALL USING (get_my_role() = 'admin');
CREATE POLICY calls_agent ON public.call_attempts FOR SELECT USING (agent_id = auth.uid());
CREATE POLICY calls_agent_insert ON public.call_attempts FOR INSERT WITH CHECK (agent_id = auth.uid());

-- Tasks
CREATE POLICY tasks_admin ON public.tasks FOR ALL USING (get_my_role() = 'admin');
CREATE POLICY tasks_agent ON public.tasks FOR SELECT USING (assigned_to = auth.uid());
CREATE POLICY tasks_agent_update ON public.tasks FOR UPDATE USING (assigned_to = auth.uid());
CREATE POLICY tasks_agent_insert ON public.tasks FOR INSERT WITH CHECK (true);

-- Notifications
CREATE POLICY notif_own ON public.notifications FOR SELECT USING (user_id = auth.uid());
CREATE POLICY notif_own_update ON public.notifications FOR UPDATE USING (user_id = auth.uid());
CREATE POLICY notif_insert ON public.notifications FOR INSERT WITH CHECK (true);

-- CSV Imports
CREATE POLICY csv_admin ON public.csv_imports FOR ALL USING (get_my_role() = 'admin');
CREATE POLICY csv_own ON public.csv_imports FOR SELECT USING (uploaded_by = auth.uid());
CREATE POLICY csv_own_insert ON public.csv_imports FOR INSERT WITH CHECK (uploaded_by = auth.uid());
CREATE POLICY csv_own_update ON public.csv_imports FOR UPDATE USING (uploaded_by = auth.uid());

-- Activity Logs — admin only
CREATE POLICY activity_admin ON public.activity_logs FOR ALL USING (get_my_role() = 'admin');

-- ============================================================
-- Enable Realtime on key tables
-- ============================================================
ALTER PUBLICATION supabase_realtime ADD TABLE public.notifications;
ALTER PUBLICATION supabase_realtime ADD TABLE public.tasks;
ALTER PUBLICATION supabase_realtime ADD TABLE public.leads;

-- ============================================================
-- Seed default lead sources
-- ============================================================
INSERT INTO public.lead_sources (name, source_type) VALUES
    ('Manual Entry', 'manual'),
    ('CSV Import', 'csv'),
    ('Meta Ads', 'meta_ads'),
    ('WhatsApp', 'whatsapp')
ON CONFLICT (name) DO NOTHING;
