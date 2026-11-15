const { supabase, authenticateUser } = require('./_lib/supabase');

module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  try {
    const user = await authenticateUser(req);
    if (!user) {
      return res.status(401).json({ error: 'Unauthorized: Valid Supabase user session required' });
    }

    const { events } = req.body || {};
    if (!Array.isArray(events) || events.length === 0) {
      return res.status(400).json({ error: 'Missing or empty events array' });
    }

    const ackedEventIds = [];

    for (const evt of events) {
      if (!evt.id || !evt.event_type || !evt.period_key) {
        continue;
      }

      // Idempotently record event in database
      const { error: insErr } = await supabase
        .from('usage_events')
        .insert({
          id: evt.id,
          user_id: user.id,
          device_id: evt.device_id || 'unknown_device',
          event_type: evt.event_type,
          period_key: evt.period_key,
          quantity: Number(evt.quantity) || 0,
          client_timestamp: evt.client_timestamp || new Date().toISOString(),
        })
        .select()
        .maybeSingle();

      if (!insErr || insErr.code === '23505') { // 23505 is PostgreSQL unique constraint violation (already recorded)
        ackedEventIds.push(evt.id);

        // Update aggregated usage_periods
        const delta = Number(evt.quantity) || 0;
        let updateField = null;
        if (evt.event_type === 'dictation_words') updateField = 'dictation_words_committed';
        else if (evt.event_type === 'meeting_duration_sec') updateField = 'meeting_duration_sec';
        else if (evt.event_type === 'meeting_count') updateField = 'meeting_count';
        else if (evt.event_type === 'ask_vox_query') updateField = 'ask_vox_queries';

        if (updateField && !insErr) {
          // Upsert usage_period row
          const { data: periodRow } = await supabase
            .from('usage_periods')
            .select('*')
            .eq('user_id', user.id)
            .eq('period_key', evt.period_key)
            .maybeSingle();

          if (periodRow) {
            await supabase
              .from('usage_periods')
              .update({
                [updateField]: (periodRow[updateField] || 0) + delta,
                updated_at: new Date().toISOString(),
              })
              .eq('id', periodRow.id);
          } else {
            await supabase
              .from('usage_periods')
              .insert({
                user_id: user.id,
                period_key: evt.period_key,
                [updateField]: delta,
              });
          }
        }
      }
    }

    return res.status(200).json({
      ok: true,
      acked_event_ids: ackedEventIds,
      total_processed: ackedEventIds.length,
    });
  } catch (err) {
    console.error('Error in /api/usage/sync:', err);
    return res.status(500).json({ error: 'Internal server error', details: err.message });
  }
};
