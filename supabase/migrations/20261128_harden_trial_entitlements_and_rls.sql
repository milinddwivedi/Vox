-- Harden 10-day Trial Window, Company Pro Email Check, and RLS Security Policies

-- 1. Add immutable 10-day trial columns to public.entitlements
ALTER TABLE public.entitlements
  ADD COLUMN IF NOT EXISTS trial_started_at timestamptz,
  ADD COLUMN IF NOT EXISTS trial_ends_at timestamptz;

UPDATE public.entitlements
SET
  trial_started_at = COALESCE(trial_started_at, created_at, NOW()),
  trial_ends_at = COALESCE(trial_ends_at, COALESCE(trial_started_at, created_at, NOW()) + INTERVAL '10 days')
WHERE trial_started_at IS NULL OR trial_ends_at IS NULL;

ALTER TABLE public.entitlements
  ALTER COLUMN trial_started_at SET DEFAULT NOW(),
  ALTER COLUMN trial_ends_at SET DEFAULT (NOW() + INTERVAL '10 days');

-- 2. Prevent tampering/extending trial_started_at or trial_ends_at after initialization
CREATE OR REPLACE FUNCTION public.preserve_trial_window_on_entitlements()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
  IF OLD.trial_started_at IS NOT NULL THEN
    NEW.trial_started_at := OLD.trial_started_at;
  END IF;
  IF OLD.trial_ends_at IS NOT NULL THEN
    NEW.trial_ends_at := OLD.trial_ends_at;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_preserve_trial_window ON public.entitlements;
CREATE TRIGGER trg_preserve_trial_window
  BEFORE UPDATE ON public.entitlements
  FOR EACH ROW
  EXECUTE FUNCTION public.preserve_trial_window_on_entitlements();

-- 3. Harden is_company_pro_email (remove wildcards, lock search_path, revoke anon/authenticated RPC)
CREATE OR REPLACE FUNCTION public.is_company_pro_email(check_email text)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  v_email text;
  v_domain text;
  v_matched boolean := false;
BEGIN
  IF check_email IS NULL OR btrim(check_email) = '' THEN
    RETURN false;
  END IF;

  v_email := lower(btrim(check_email));
  v_domain := split_part(v_email, '@', 2);

  IF v_email IN (
    'milinddwivedi0@gmail.com',
    'amar.vohra3@gmail.com',
    'hemanthwfx@gmail.com',
    'rumjhum.singh003@gmail.com'
  ) OR v_domain = 'worldfashionexchange.com' THEN
    RETURN true;
  END IF;

  SELECT EXISTS (
    SELECT 1
    FROM public.company_entitlement_rules r
    WHERE r.is_active = true
      AND (
        (r.rule_type = 'email' AND lower(btrim(r.rule_value)) = v_email)
        OR (r.rule_type = 'domain' AND lower(btrim(r.rule_value)) = v_domain)
      )
  ) INTO v_matched;

  RETURN COALESCE(v_matched, false);
END;
$$;

REVOKE ALL ON FUNCTION public.is_company_pro_email(text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.is_company_pro_email(text) TO service_role;

-- 4. Update handle_new_user with immutable 10-day trial window & unlimited typing/transcription
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  v_plan text := 'free';
  v_is_pro boolean := false;
  v_limits jsonb := '{"allow_meeting_overrun":true,"meeting_count_per_week":null,"ask_vox_queries_per_week":null,"dictation_words_per_week":null,"max_meeting_duration_sec":null,"meeting_duration_sec_per_week":null,"allow_ai_summary_and_ask":true,"trial_days":10}'::jsonb;
  v_trial_start timestamptz := COALESCE(NEW.created_at, NOW());
  v_trial_end timestamptz := COALESCE(NEW.created_at, NOW()) + INTERVAL '10 days';
BEGIN
  IF public.is_company_pro_email(NEW.email) THEN
    v_plan := 'pro';
    v_is_pro := true;
    v_limits := '{"allow_meeting_overrun":true,"meeting_count_per_week":null,"ask_vox_queries_per_week":null,"dictation_words_per_week":null,"max_meeting_duration_sec":null,"meeting_duration_sec_per_week":null,"allow_ai_summary_and_ask":true,"trial_days":10}'::jsonb;
  END IF;

  INSERT INTO public.profiles (id, email, full_name, avatar_url, created_at, updated_at)
  VALUES (
    NEW.id,
    NEW.email,
    COALESCE(NEW.raw_user_meta_data->>'full_name', NEW.raw_user_meta_data->>'name', split_part(NEW.email, '@', 1)),
    COALESCE(NEW.raw_user_meta_data->>'avatar_url', NEW.raw_user_meta_data->>'picture', ''),
    NOW(),
    NOW()
  )
  ON CONFLICT (id) DO UPDATE
  SET email = EXCLUDED.email,
      full_name = EXCLUDED.full_name,
      avatar_url = EXCLUDED.avatar_url,
      updated_at = NOW();

  INSERT INTO public.subscriptions (user_id, plan_id, status, current_period_start, current_period_end, created_at, updated_at)
  VALUES (
    NEW.id,
    v_plan,
    'active',
    NOW(),
    CASE WHEN v_plan = 'pro' THEN NOW() + INTERVAL '100 years' ELSE NULL END,
    NOW(),
    NOW()
  )
  ON CONFLICT (user_id) DO UPDATE
  SET plan_id = CASE
        WHEN public.subscriptions.plan_id IN ('pro', 'pro_monthly', 'pro_annual') AND public.subscriptions.status = 'active'
          THEN public.subscriptions.plan_id
        ELSE EXCLUDED.plan_id
      END,
      status = 'active',
      current_period_end = COALESCE(public.subscriptions.current_period_end, EXCLUDED.current_period_end),
      updated_at = NOW();

  INSERT INTO public.entitlements (user_id, plan_id, is_pro, limits, token_version, trial_started_at, trial_ends_at, created_at, updated_at)
  VALUES (
    NEW.id,
    v_plan,
    v_is_pro,
    v_limits,
    1,
    v_trial_start,
    v_trial_end,
    NOW(),
    NOW()
  )
  ON CONFLICT (user_id) DO UPDATE
  SET plan_id = CASE
        WHEN public.entitlements.is_pro THEN public.entitlements.plan_id
        ELSE EXCLUDED.plan_id
      END,
      is_pro = (public.entitlements.is_pro OR EXCLUDED.is_pro),
      limits = EXCLUDED.limits,
      trial_started_at = COALESCE(public.entitlements.trial_started_at, EXCLUDED.trial_started_at),
      trial_ends_at = COALESCE(public.entitlements.trial_ends_at, EXCLUDED.trial_ends_at),
      updated_at = NOW();

  RETURN NEW;
END;
$$;

-- 5. Update free plan catalog metadata so limits match unlimited dictation & transcription
UPDATE public.plans
SET limits = '{"allow_meeting_overrun":true,"meeting_count_per_week":null,"ask_vox_queries_per_week":null,"dictation_words_per_week":null,"max_meeting_duration_sec":null,"meeting_duration_sec_per_week":null,"trial_days":10}'::jsonb
WHERE id = 'free';

-- 6. Harden RLS policies on profiles, devices, device_records, usage_periods, company_entitlement_rules
DROP POLICY IF EXISTS "Users can update own profile" ON public.profiles;
CREATE POLICY "Users can update own profile"
  ON public.profiles
  FOR UPDATE
  TO authenticated
  USING ((SELECT auth.uid()) = id)
  WITH CHECK ((SELECT auth.uid()) = id);

DROP POLICY IF EXISTS "Users can manage own devices" ON public.devices;
CREATE POLICY "Users can manage own devices"
  ON public.devices
  FOR ALL
  TO authenticated
  USING ((SELECT auth.uid()) = user_id)
  WITH CHECK ((SELECT auth.uid()) = user_id);

DROP POLICY IF EXISTS "Users can manage own device records" ON public.device_records;
CREATE POLICY "Users can manage own device records"
  ON public.device_records
  FOR ALL
  TO authenticated
  USING ((SELECT auth.uid()) = user_id)
  WITH CHECK ((SELECT auth.uid()) = user_id);

DROP POLICY IF EXISTS "Users can update own usage periods" ON public.usage_periods;
CREATE POLICY "Users can update own usage periods"
  ON public.usage_periods
  FOR UPDATE
  TO authenticated
  USING ((SELECT auth.uid()) = user_id)
  WITH CHECK ((SELECT auth.uid()) = user_id);

DROP POLICY IF EXISTS "Only service role can manage company entitlement rules" ON public.company_entitlement_rules;
CREATE POLICY "Only service role can manage company entitlement rules"
  ON public.company_entitlement_rules
  FOR ALL
  TO service_role
  USING (true)
  WITH CHECK (true);
