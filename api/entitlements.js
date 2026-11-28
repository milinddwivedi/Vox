const { supabase, authenticateUser } = require('./_lib/supabase');
const { signEntitlement } = require('./_lib/token');

const FOUNDER_EMAILS = new Set([
  'milinddwivedi0@gmail.com',
  'amar.vohra3@gmail.com',
  'hemanthwfx@gmail.com',
  'rumjhum.singh003@gmail.com',
]);

const FOUNDER_IDS = new Set([
  '50d9a219-b06d-4fe9-acb3-7e32bf7cec2f',
  '09039fd3-2e18-4aaf-a93b-fdd5ef9f0cc9',
  '029cf21a-9fd2-45a3-af3c-70d0fdea5002',
  'ccb6a5b7-246f-4050-832e-bf520c077dc2',
]);

const TRIAL_DAYS = 10;
const TRIAL_MS = TRIAL_DAYS * 86400 * 1000;

async function isCompanyOrFounderPro(email, uid) {
  const em = (email || '').toLowerCase().trim();
  const id = (uid || '').toLowerCase().trim();
  const domain = em.includes('@') ? em.split('@')[1] : '';
  if (FOUNDER_EMAILS.has(em) || FOUNDER_IDS.has(id) || domain === 'worldfashionexchange.com') {
    return true;
  }
  if (em && supabase) {
    try {
      const { data: rules } = await supabase
        .from('company_entitlement_rules')
        .select('rule_type, rule_value')
        .eq('is_active', true);
      if (Array.isArray(rules)) {
        for (const r of rules) {
          const rt = String(r.rule_type || '').toLowerCase().trim();
          const rv = String(r.rule_value || '').toLowerCase().trim();
          if (!rv) continue;
          if (rt === 'email' && rv === em) return true;
          if (rt === 'domain' && rv === domain) return true;
        }
      }
    } catch (_) {}
  }
  return false;
}

module.exports = async function handler(req, res) {
  // Set CORS headers
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  if (req.method !== 'GET') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  try {
    // Strictly require verified Supabase Bearer JWT — never trust spoofable query/header identity
    const user = await authenticateUser(req);
    if (!user || !user.id) {
      return res.status(401).json({
        ok: false,
        error: 'AUTH_REQUIRED',
        message: 'Valid Authorization: Bearer <token> is required.',
      });
    }

    let plan = 'free';
    let isPro = false;
    const userId = user.id;
    const em = (user.email || '').toLowerCase().trim();
    const uid = (user.id || '').toLowerCase().trim();
    const isMasterAdmin = await isCompanyOrFounderPro(em, uid);

    let ent = null;
    try {
      const { data: entRow } = await supabase
        .from('entitlements')
        .select('*')
        .eq('user_id', user.id)
        .maybeSingle();
      ent = entRow || null;
    } catch (_) {}

    if (isMasterAdmin) {
      plan = 'pro_annual';
      isPro = true;
    } else {
      const { data: sub } = await supabase
        .from('subscriptions')
        .select('*')
        .eq('user_id', user.id)
        .eq('status', 'active')
        .order('created_at', { ascending: false })
        .limit(1)
        .maybeSingle();

      if (sub && (sub.plan_id === 'pro' || sub.plan_id === 'pro_monthly' || sub.plan_id === 'pro_annual')) {
        plan = sub.billing_interval === 'annual' ? 'pro_annual' : 'pro_monthly';
        isPro = true;
      } else if (ent && ent.is_pro) {
        plan = ent.plan_id || 'pro_monthly';
        isPro = true;
      }
    }

    const nowMs = Date.now();
    const now = Math.floor(nowMs / 1000);
    const exp = now + 7 * 86400; // 7 days offline continuity window
    const nowIso = new Date(nowMs).toISOString();
    const expIso = new Date(exp * 1000).toISOString();

    const trialStartMs = Date.parse((ent && ent.trial_started_at) || user.created_at || (ent && ent.created_at) || nowIso);
    const safeTrialStartMs = Number.isFinite(trialStartMs) ? trialStartMs : nowMs;
    const explicitTrialEndMs = ent && ent.trial_ends_at ? Date.parse(ent.trial_ends_at) : NaN;
    const safeTrialEndMs = Number.isFinite(explicitTrialEndMs) ? explicitTrialEndMs : (safeTrialStartMs + TRIAL_MS);
    const trialStartedIso = new Date(safeTrialStartMs).toISOString();
    const trialEndsIso = new Date(safeTrialEndMs).toISOString();

    const isTrialActive = !isPro && nowMs < safeTrialEndMs;
    const trialDaysRemaining = isPro ? 0 : Math.max(0, Math.ceil((safeTrialEndMs - nowMs) / 86400000));
    const allowAiSummaryAndAsk = Boolean(isPro || isTrialActive);
    const effectivePlanCode = isPro ? plan : (isTrialActive ? 'trial_10d' : 'free_expired');

    // Voice typing AND Meeting recording/transcription remain unlimited forever on all plans.
    // Only AI Meeting Summary Generation and Ask VOX require active 10-day Trial or VOX Pro.
    const limits = {
      dictation_words_per_week: null,
      meeting_duration_sec_per_week: null,
      meeting_count_per_week: null,
      max_meeting_duration_sec: null,
      ask_vox_queries_per_week: allowAiSummaryAndAsk ? null : 0,
      allow_meeting_overrun: true,
      allow_ai_summary_and_ask: allowAiSummaryAndAsk,
      trial_days: TRIAL_DAYS,
    };

    const usageLimits = {
      dictation_words_weekly: null,
      meeting_minutes_weekly: null,
      meeting_sessions_weekly: null,
      meeting_session_cap_min: null,
      ask_vox_queries_weekly: allowAiSummaryAndAsk ? null : 0,
    };

    const enabledFeatures = [
      'dictation',
      'unlimited_words',
      'dual_track_recording',
      'unlimited_meetings',
      'meeting_transcription',
      'local_vocabulary',
      'grey_text_preview',
    ];
    if (allowAiSummaryAndAsk) {
      enabledFeatures.push('meeting_summaries', 'ask_vox_unlimited', 'executive_export');
    }

    const payload = {
      user_id: userId,
      email: em,
      plan: isPro ? plan : 'free',
      plan_code: effectivePlanCode,
      plan_name: isPro ? 'VOX Pro' : (isTrialActive ? 'VOX 10-Day Trial' : 'VOX Free'),
      is_pro: isPro,
      trial_active: isTrialActive,
      trial_started_at: trialStartedIso,
      trial_ends_at: trialEndsIso,
      trial_days_remaining: trialDaysRemaining,
      allow_ai_summary_and_ask: allowAiSummaryAndAsk,
      limits: limits,
      usage_limits: usageLimits,
      features: {
        voice_typing: true,
        offline_whisper: true,
        meeting_recorder: true,
        meeting_transcription: true,
        ai_mom_summaries: allowAiSummaryAndAsk,
        ask_vox: allowAiSummaryAndAsk,
        local_first: true,
      },
      enabled_features: enabledFeatures,
      server_now_utc: nowIso,
      issued_at: now,
      issued_at_utc: nowIso,
      period_start_utc: nowIso,
      exp: exp,
      expires_at_utc: expIso,
    };

    let token;
    try {
      token = signEntitlement(payload);
    } catch (signErr) {
      console.error('Cryptographic signing failed in /api/entitlements:', signErr);
      return res.status(500).json({ ok: false, error: 'Cryptographic signing failure', details: signErr.message });
    }

    return res.status(200).json({
      ok: true,
      token,
      plan: payload.plan,
      plan_code: payload.plan_code,
      plan_name: payload.plan_name,
      is_pro: isPro,
      trial_active: isTrialActive,
      trial_started_at: trialStartedIso,
      trial_ends_at: trialEndsIso,
      trial_days_remaining: trialDaysRemaining,
      allow_ai_summary_and_ask: allowAiSummaryAndAsk,
      limits,
      usage_limits: usageLimits,
      user_id: userId,
      server_now_utc: nowIso,
      expires_at: exp,
      expires_at_utc: expIso,
    });
  } catch (err) {
    console.error('Error in /api/entitlements:', err);
    return res.status(500).json({ ok: false, error: 'Internal server error', details: err.message });
  }
};
