const { supabase, authenticateUser } = require('./_lib/supabase');
const { signEntitlement } = require('./_lib/token');

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
    const user = await authenticateUser(req);
    let plan = 'free';
    let isPro = false;
    let userId = user ? user.id : 'guest-device';

    if (user) {
      const MASTER_DEVELOPER_EMAILS = ['milinddwivedi0@gmail.com'];
      const isMasterAdmin = (user.email && MASTER_DEVELOPER_EMAILS.includes(user.email.toLowerCase())) ||
                            user.id === '50d9a219-b06d-4fe9-acb3-7e32bf7cec2f';

      if (isMasterAdmin) {
        plan = 'pro_annual';
        isPro = true;
      } else {
        // 1. Check subscriptions table
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
        } else {
          // Fallback: check entitlements table directly
          const { data: ent } = await supabase
            .from('entitlements')
            .select('*')
            .eq('user_id', user.id)
            .maybeSingle();
          if (ent && ent.is_pro) {
            plan = ent.plan_id || 'pro_monthly';
            isPro = true;
          }
        }
      }
    }

    const now = Math.floor(Date.now() / 1000);
    const exp = now + 7 * 86400; // 7 days validity
    const nowIso = new Date(now * 1000).toISOString();
    const expIso = new Date(exp * 1000).toISOString();

    const limits = isPro
      ? {
          dictation_words_per_week: null,
          meeting_duration_sec_per_week: null,
          meeting_count_per_week: null,
          max_meeting_duration_sec: null,
          ask_vox_queries_per_week: null,
          allow_meeting_overrun: true,
        }
      : {
          dictation_words_per_week: 3000,
          meeting_duration_sec_per_week: 10800,
          meeting_count_per_week: 5,
          max_meeting_duration_sec: 5400,
          ask_vox_queries_per_week: 10,
          allow_meeting_overrun: true,
        };

    const usageLimits = isPro
      ? {
          dictation_words_weekly: null,
          meeting_minutes_weekly: null,
          meeting_sessions_weekly: null,
          meeting_session_cap_min: null,
          ask_vox_queries_weekly: null,
        }
      : {
          dictation_words_weekly: 3000,
          meeting_minutes_weekly: 180.0,
          meeting_sessions_weekly: 5,
          meeting_session_cap_min: 90.0,
          ask_vox_queries_weekly: 10,
        };

    const enabledFeatures = isPro
      ? [
          'dictation',
          'dual_track_recording',
          'meeting_summaries',
          'local_vocabulary',
          'grey_text_preview',
          'unlimited_words',
          'unlimited_meetings',
          'ask_vox_unlimited',
          'executive_export',
        ]
      : [
          'dictation',
          'dual_track_recording',
          'meeting_summaries',
          'local_vocabulary',
          'grey_text_preview',
        ];

    const payload = {
      user_id: userId,
      plan: plan,
      plan_code: plan,
      plan_name: isPro ? 'VOX Pro' : 'VOX Free',
      is_pro: isPro,
      limits: limits,
      usage_limits: usageLimits,
      features: {
        voice_typing: true,
        offline_whisper: true,
        meeting_recorder: true,
        ai_mom_summaries: true,
        ask_vox: true,
        local_first: true,
      },
      enabled_features: enabledFeatures,
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
      plan,
      plan_code: plan,
      plan_name: payload.plan_name,
      is_pro: isPro,
      limits,
      usage_limits: usageLimits,
      user_id: userId,
      expires_at: exp,
      expires_at_utc: expIso,
    });
  } catch (err) {
    console.error('Error in /api/entitlements:', err);
    return res.status(500).json({ ok: false, error: 'Internal server error', details: err.message });
  }
};
