const { supabase, authenticateUser } = require('../_lib/supabase');

/**
 * Normalizes raw metric aliases and transforms units into canonical representations.
 */
function normalizeMetric(rawMetric, rawQuantity) {
  if (!rawMetric) return null;
  const m = String(rawMetric).toLowerCase().trim();
  const q = Number(rawQuantity) || 0;

  switch (m) {
    case 'dictation_words':
    case 'dictation_words_committed':
      return {
        canonicalEventType: 'dictation_words',
        periodField: 'dictation_words_committed',
        delta: q,
      };

    case 'meeting_duration_sec':
    case 'meeting_seconds':
      return {
        canonicalEventType: 'meeting_duration_sec',
        periodField: 'meeting_duration_sec',
        delta: q,
      };

    case 'meeting_minutes':
      return {
        canonicalEventType: 'meeting_duration_sec',
        periodField: 'meeting_duration_sec',
        delta: q * 60.0, // Convert minutes to seconds
      };

    case 'meeting_count':
    case 'meeting_sessions':
      return {
        canonicalEventType: 'meeting_count',
        periodField: 'meeting_count',
        delta: q,
      };

    case 'ask_vox_query':
    case 'ask_vox_count':
    case 'ask_vox_queries':
      return {
        canonicalEventType: 'ask_vox_query',
        periodField: 'ask_vox_queries',
        delta: q,
      };

    default:
      return null;
  }
}

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

    const { events, device_id: bodyDeviceId } = req.body || {};
    if (!Array.isArray(events) || events.length === 0) {
      return res.status(400).json({ error: 'Missing or empty events array' });
    }

    const ackedEventIds = [];
    const periodDeltas = {}; // periodKey -> { dictation_words_committed, meeting_duration_sec, meeting_count, ask_vox_queries }

    for (const evt of events) {
      // Dual-schema resolution (Schema A vs Schema B)
      const eventId = evt.id || evt.event_id;
      const rawMetric = evt.event_type || evt.metric;
      const periodKey = evt.period_key || evt.period_id;
      const rawQuantity = evt.quantity !== undefined ? evt.quantity : evt.amount;
      const clientTimestamp = evt.client_timestamp || evt.timestamp_utc || evt.timestamp || new Date().toISOString();
      const deviceId = evt.device_id || bodyDeviceId || 'unknown_device';

      if (!eventId || !rawMetric || !periodKey || rawQuantity === undefined || isNaN(Number(rawQuantity))) {
        continue;
      }

      const metricMapping = normalizeMetric(rawMetric, rawQuantity);
      if (!metricMapping) {
        console.warn(`[usage/sync] Unrecognized metric: "${rawMetric}" for event: ${eventId}`);
        continue;
      }

      const { canonicalEventType, periodField, delta } = metricMapping;

      // Idempotently record event in public.usage_events
      const { error: insErr } = await supabase
        .from('usage_events')
        .insert({
          id: eventId,
          user_id: user.id,
          device_id: deviceId,
          event_type: canonicalEventType,
          period_key: periodKey,
          quantity: Number(rawQuantity) || 0,
          client_timestamp: clientTimestamp,
        });

      if (!insErr) {
        ackedEventIds.push(eventId);

        if (!periodDeltas[periodKey]) {
          periodDeltas[periodKey] = {
            dictation_words_committed: 0,
            meeting_duration_sec: 0,
            meeting_count: 0,
            ask_vox_queries: 0,
          };
        }
        periodDeltas[periodKey][periodField] = (periodDeltas[periodKey][periodField] || 0) + delta;
      } else if (insErr.code === '23505') {
        // Unique constraint violation (already recorded) -> ack without double counting
        ackedEventIds.push(eventId);
      } else {
        console.error(`[usage/sync] Failed to insert usage event ${eventId}:`, insErr);
      }
    }

    // Apply batch aggregated deltas to usage_periods (one fetch/upsert per periodKey)
    for (const [pKey, deltas] of Object.entries(periodDeltas)) {
      const hasDeltas =
        deltas.dictation_words_committed > 0 ||
        deltas.meeting_duration_sec > 0 ||
        deltas.meeting_count > 0 ||
        deltas.ask_vox_queries > 0;

      if (!hasDeltas) continue;

      const { data: periodRow, error: fetchErr } = await supabase
        .from('usage_periods')
        .select('*')
        .eq('user_id', user.id)
        .eq('period_key', pKey)
        .maybeSingle();

      if (fetchErr) {
        console.error(`[usage/sync] Failed to fetch usage_periods for ${pKey}:`, fetchErr);
        continue;
      }

      if (periodRow) {
        const { error: updateErr } = await supabase
          .from('usage_periods')
          .update({
            dictation_words_committed: (periodRow.dictation_words_committed || 0) + (deltas.dictation_words_committed || 0),
            meeting_duration_sec: (periodRow.meeting_duration_sec || 0) + (deltas.meeting_duration_sec || 0),
            meeting_count: (periodRow.meeting_count || 0) + (deltas.meeting_count || 0),
            ask_vox_queries: (periodRow.ask_vox_queries || 0) + (deltas.ask_vox_queries || 0),
            updated_at: new Date().toISOString(),
          })
          .eq('id', periodRow.id);

        if (updateErr) {
          console.error(`[usage/sync] Failed to update usage_periods for ${pKey}:`, updateErr);
        }
      } else {
        const { error: insertErr } = await supabase
          .from('usage_periods')
          .insert({
            user_id: user.id,
            period_key: pKey,
            dictation_words_committed: deltas.dictation_words_committed || 0,
            meeting_duration_sec: deltas.meeting_duration_sec || 0,
            meeting_count: deltas.meeting_count || 0,
            ask_vox_queries: deltas.ask_vox_queries || 0,
          });

        if (insertErr) {
          console.error(`[usage/sync] Failed to insert usage_periods for ${pKey}:`, insertErr);
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
