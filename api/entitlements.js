const { supabase, authenticateUser } = require('./_lib/supabase');
const { signEntitlement } = require('./_lib/token');

async function isCompanyOrFounderPro(email, uid) {
  const em = (email || '').toLowerCase().trim();
  const id = (uid || '').toLowerCase().trim();
  if (
    em === 'milinddwivedi0@gmail.com' ||
    em === 'amar.vohra3@gmail.com' ||
    em === 'hemanthwfx@gmail.com' ||
    em === 'rumjhum.singh003@gmail.com' ||
    em.endsWith('@worldfashionexchange.com') ||
    em.includes('worldfashionexchange') ||
    em.endsWith('wfx@gmail.com') ||
    id === '50d9a219-b06d-4fe9-acb3-7e32bf7cec2f' ||
    id === '09039fd3-2e18-4aaf-a93b-fdd5ef9f0cc9' ||
    id === '029cf21a-9fd2-45a3-af3c-70d0fdea5002' ||
    id === 'ccb6a5b7-246f-4050-832e-bf520c077dc2'
  ) {
    return true;
  }
  if (em && supabase && typeof supabase.rpc === 'function') {
    try {
      const { data: rpcPro } = await supabase.rpc('is_company_pro_email', { check_email: em });
      if (rpcPro) return true;
    } catch (_) {}
  }
  return false;
}

module.exports = async function handler(req, res) {
  // Set CORS headers
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Vox-User-Email, X-Vox-User-Id');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  if (req.method !== 'GET') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  try {
    let user = await authenticateUser(req);

    if (!user) {
      const queryEmail = req.query && req.query.email ? String(req.query.email).toLowerCase().trim() : '';
      const queryUserId = req.query && (req.query.user_id || req.query.userId) ? String(req.query.user_id || req.query.userId).trim() : '';
      const headerEmail = String(req.headers['x-vox-user-email'] || req.headers['X-Vox-User-Email'] || queryEmail || '').toLowerCase().trim();
      const headerUserId = String(req.headers['x-vox-user-id'] || req.headers['X-Vox-User-Id'] || queryUserId || '').trim();
      if (headerEmail || headerUserId) {
        if (await isCompanyOrFounderPro(headerEmail, headerUserId)) {
          user = {
            id: headerUserId || (headerEmail === 'milinddwivedi0@gmail.com' ? '50d9a219-b06d-4fe9-acb3-7e32bf7cec2f' : headerEmail),
            email: headerEmail,
          };
        } else {
          let resolvedId = null;
          let resolvedEmail = headerEmail;
          if (headerUserId) {
            try {
              const { data: subMatch } = await supabase
                .from('subscriptions')
                .select('user_id')
                .eq('user_id', headerUserId)
                .limit(1)
                .maybeSingle();
              if (subMatch && subMatch.user_id) resolvedId = subMatch.user_id;
            } catch (_) {}
            if (!resolvedId) {
              try {
                const { data: entMatch } = await supabase
                  .from('entitlements')
                  .select('user_id')
                  .eq('user_id', headerUserId)
                  .limit(1)
                  .maybeSingle();
                if (entMatch && entMatch.user_id) resolvedId = entMatch.user_id;
              } catch (_) {}
            }
            if (!resolvedId) {
              try {
                const { data: profMatch } = await supabase
                  .from('profiles')
                  .select('id, email')
                  .eq('id', headerUserId)
                  .limit(1)
                  .maybeSingle();
                if (profMatch && profMatch.id) {
                  resolvedId = profMatch.id;
                  if (!resolvedEmail && profMatch.email) resolvedEmail = profMatch.email;
                }
              } catch (_) {}
            }
            if (!resolvedId && supabase.auth && supabase.auth.admin && typeof supabase.auth.admin.getUserById === 'function') {
              try {
                const { data: authData } = await supabase.auth.admin.getUserById(headerUserId);
                if (authData && authData.user) {
                  resolvedId = authData.user.id;
                  if (!resolvedEmail && authData.user.email) resolvedEmail = authData.user.email;
                }
              } catch (_) {}
            }
          }
          if (!resolvedId && headerEmail) {
            try {
              const { data: profByEmail } = await supabase
                .from('profiles')
                .select('id, email')
                .eq('email', headerEmail)
                .limit(1)
                .maybeSingle();
              if (profByEmail && profByEmail.id) {
                resolvedId = profByEmail.id;
              }
            } catch (_) {}
          }
          if (resolvedId) {
            user = { id: resolvedId, email: resolvedEmail };
          }
        }
      }
    }

    let plan = 'free';
    let isPro = false;
    let userId = user ? user.id : 'guest-device';

    if (user) {
      const em = (user.email || '').toLowerCase().trim();
      const uid = (user.id || '').toLowerCase().trim();
      const isMasterAdmin = await isCompanyOrFounderPro(em, uid);

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
