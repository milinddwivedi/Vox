-- =============================================================================
-- VOX Local AI Assistant — Commercial Database Schema (Supabase PostgreSQL)
-- Migration: 20261115_commercial_schema.sql
-- =============================================================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. PLANS TABLE
CREATE TABLE IF NOT EXISTS public.plans (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    price_inr_monthly INTEGER NOT NULL,
    price_inr_annual INTEGER NOT NULL,
    limits JSONB NOT NULL,
    features JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Seed Default Plans (VOX Free & VOX Pro)
INSERT INTO public.plans (id, name, price_inr_monthly, price_inr_annual, limits, features)
VALUES 
(
    'free',
    'VOX Free',
    0,
    0,
    '{
        "dictation_words_per_week": 3000,
        "meeting_duration_sec_per_week": 10800,
        "meeting_count_per_week": 5,
        "max_meeting_duration_sec": 5400,
        "ask_vox_queries_per_week": 10,
        "allow_meeting_overrun": true
    }'::jsonb,
    '{
        "voice_typing": true,
        "offline_whisper": true,
        "meeting_recorder": true,
        "ai_mom_summaries": true,
        "ask_vox_search": true,
        "local_storage": true
    }'::jsonb
),
(
    'pro',
    'VOX Pro',
    199,
    1599,
    '{
        "dictation_words_per_week": null,
        "meeting_duration_sec_per_week": null,
        "meeting_count_per_week": null,
        "max_meeting_duration_sec": null,
        "ask_vox_queries_per_week": null,
        "allow_meeting_overrun": true
    }'::jsonb,
    '{
        "voice_typing": true,
        "unlimited_words": true,
        "offline_whisper": true,
        "meeting_recorder": true,
        "unlimited_meetings": true,
        "ai_mom_summaries": true,
        "unlimited_ask_vox": true,
        "local_storage": true,
        "priority_support": true
    }'::jsonb
)
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name,
    price_inr_monthly = EXCLUDED.price_inr_monthly,
    price_inr_annual = EXCLUDED.price_inr_annual,
    limits = EXCLUDED.limits,
    features = EXCLUDED.features;

-- 2. PROFILES TABLE (Mirrors auth.users)
CREATE TABLE IF NOT EXISTS public.profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email TEXT,
    full_name TEXT,
    avatar_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. SUBSCRIPTIONS TABLE
CREATE TABLE IF NOT EXISTS public.subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    plan_id TEXT NOT NULL REFERENCES public.plans(id) DEFAULT 'free',
    billing_interval TEXT CHECK (billing_interval IN ('monthly', 'annual')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'past_due', 'canceled', 'trialing', 'paused')),
    current_period_start TIMESTAMPTZ DEFAULT NOW(),
    current_period_end TIMESTAMPTZ,
    cancel_at_period_end BOOLEAN DEFAULT FALSE,
    razorpay_subscription_id TEXT,
    razorpay_customer_id TEXT,
    razorpay_payment_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_user_id ON public.subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON public.subscriptions(status);

-- 4. ENTITLEMENTS TABLE
CREATE TABLE IF NOT EXISTS public.entitlements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE UNIQUE,
    plan_id TEXT NOT NULL REFERENCES public.plans(id) DEFAULT 'free',
    is_pro BOOLEAN NOT NULL DEFAULT FALSE,
    limits JSONB NOT NULL,
    token_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. USAGE PERIODS TABLE (Aggregated weekly ledger by Monday 00:00 UTC)
CREATE TABLE IF NOT EXISTS public.usage_periods (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    period_key TEXT NOT NULL, -- e.g. "2026-11-09_utc"
    dictation_words_committed INTEGER NOT NULL DEFAULT 0,
    meeting_duration_sec DOUBLE PRECISION NOT NULL DEFAULT 0,
    meeting_count INTEGER NOT NULL DEFAULT 0,
    ask_vox_queries INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, period_key)
);

CREATE INDEX IF NOT EXISTS idx_usage_periods_user_period ON public.usage_periods(user_id, period_key);

-- 6. USAGE EVENTS TABLE (Idempotent event sync queue)
CREATE TABLE IF NOT EXISTS public.usage_events (
    id UUID PRIMARY KEY, -- Client UUID
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    device_id TEXT,
    event_type TEXT NOT NULL,
    period_key TEXT NOT NULL,
    quantity DOUBLE PRECISION NOT NULL,
    client_timestamp TIMESTAMPTZ,
    received_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_usage_events_user ON public.usage_events(user_id);

-- 7. DEVICES TABLE
CREATE TABLE IF NOT EXISTS public.devices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    device_id TEXT NOT NULL UNIQUE,
    device_name TEXT,
    os_version TEXT,
    app_version TEXT,
    last_seen_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 8. PAYMENT EVENTS AUDIT TABLE (Webhooks ledger)
CREATE TABLE IF NOT EXISTS public.payment_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    processed_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- ROW LEVEL SECURITY (RLS) POLICIES
-- =============================================================================

ALTER TABLE public.plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.entitlements ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.usage_periods ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.usage_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.payment_events ENABLE ROW LEVEL SECURITY;

-- Plans are publicly readable
CREATE POLICY "Plans are readable by all users" ON public.plans
    FOR SELECT USING (true);

-- Profiles: users can select/update their own profile
CREATE POLICY "Users can view own profile" ON public.profiles
    FOR SELECT USING (auth.uid() = id);
CREATE POLICY "Users can update own profile" ON public.profiles
    FOR UPDATE USING (auth.uid() = id);

-- Subscriptions: users can view own subscriptions
CREATE POLICY "Users can view own subscriptions" ON public.subscriptions
    FOR SELECT USING (auth.uid() = user_id);

-- Entitlements: users can view own entitlements
CREATE POLICY "Users can view own entitlements" ON public.entitlements
    FOR SELECT USING (auth.uid() = user_id);

-- Usage Periods: users can view own usage
CREATE POLICY "Users can view own usage periods" ON public.usage_periods
    FOR SELECT USING (auth.uid() = user_id);

-- Usage Events: users can insert their own usage events
CREATE POLICY "Users can insert own usage events" ON public.usage_events
    FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users can view own usage events" ON public.usage_events
    FOR SELECT USING (auth.uid() = user_id);

-- Devices: users can view and update their registered devices
CREATE POLICY "Users can manage own devices" ON public.devices
    FOR ALL USING (auth.uid() = user_id);

-- =============================================================================
-- AUTOMATIC PROFILE & FREE ENTITLEMENT PROVISIONING TRIGGER
-- =============================================================================

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
DECLARE
    free_limits JSONB;
BEGIN
    SELECT limits INTO free_limits FROM public.plans WHERE id = 'free';

    -- 1. Create profile
    INSERT INTO public.profiles (id, email, full_name, avatar_url)
    VALUES (
        NEW.id,
        NEW.email,
        COALESCE(NEW.raw_user_meta_data->>'full_name', NEW.raw_user_meta_data->>'name', ''),
        COALESCE(NEW.raw_user_meta_data->>'avatar_url', NEW.raw_user_meta_data->>'picture', '')
    );

    -- 2. Create Free entitlement
    INSERT INTO public.entitlements (user_id, plan_id, is_pro, limits, token_version)
    VALUES (
        NEW.id,
        'free',
        FALSE,
        free_limits,
        1
    );

    -- 3. Create initial Free subscription record
    INSERT INTO public.subscriptions (user_id, plan_id, status)
    VALUES (
        NEW.id,
        'free',
        'active'
    );

    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Trigger on user creation in auth.users
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();
