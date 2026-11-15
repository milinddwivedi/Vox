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
      // Check database for active subscription
      const { data: sub } = await supabase
        .from('subscriptions')
        .select('*')
        .eq('user_id', user.id)
        .eq('status', 'active')
        .order('created_at', { ascending: false })
        .limit(1)
        .maybeSingle();

      if (sub && sub.plan_id === 'pro') {
        plan = 'pro';
        isPro = true;
      }
    }

    const now = Math.floor(Date.now() / 1000);
    const exp = now + 7 * 86400; // 7 days validity

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

    const payload = {
      user_id: userId,
      plan: plan,
      plan_name: isPro ? 'VOX Pro' : 'VOX Free',
      is_pro: isPro,
      limits: limits,
      features: {
        voice_typing: true,
        offline_whisper: true,
        meeting_recorder: true,
        ai_mom_summaries: true,
        ask_vox: true,
        local_first: true,
      },
      issued_at: now,
      exp: exp,
    };

    const token = signEntitlement(payload);

    return res.status(200).json({
      ok: true,
      token,
      plan,
      plan_name: payload.plan_name,
      is_pro: isPro,
      limits,
      user_id: userId,
      expires_at: exp,
    });
  } catch (err) {
    console.error('Error in /api/entitlements:', err);
    return res.status(500).json({ error: 'Internal server error', details: err.message });
  }
};
