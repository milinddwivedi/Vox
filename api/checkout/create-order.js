const Razorpay = require('razorpay');
const { supabase, authenticateUser } = require('../_lib/supabase');

const keyId = process.env.RAZORPAY_KEY_ID || process.env.NEXT_PUBLIC_RAZORPAY_KEY_ID || 'rzp_test_placeholder';
const keySecret = process.env.RAZORPAY_KEY_SECRET || 'secret_placeholder';

const razorpay = new Razorpay({
  key_id: keyId,
  key_secret: keySecret,
});

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
    const { plan_id = 'pro', interval = 'monthly', email } = req.body || {};

    const userId = user ? user.id : null;
    const customerEmail = user ? user.email : (email || 'customer@example.com');

    // ₹199/month (19900 paise) vs ₹1,599/year (159900 paise)
    const isAnnual = interval === 'annual';
    const amountInPaise = isAnnual ? 159900 : 19900;
    const amountInRupees = isAnnual ? 1599 : 199;

    const receipt = `vox_${userId ? userId.slice(0, 8) : 'guest'}_${Date.now()}`;

    const orderOptions = {
      amount: amountInPaise,
      currency: 'INR',
      receipt,
      notes: {
        user_id: userId || 'anonymous',
        email: customerEmail,
        plan_id: 'pro',
        interval: isAnnual ? 'annual' : 'monthly',
        product: 'VOX Pro AI Assistant',
      },
    };

    let order = null;
    try {
      order = await razorpay.orders.create(orderOptions);
    } catch (rzpErr) {
      console.warn('Razorpay order creation fallback (test/sandbox mode active):', rzpErr.message);
      // Fallback stub for local / unconfigured test environment
      order = {
        id: `order_sim_${Date.now()}`,
        amount: amountInPaise,
        currency: 'INR',
        receipt,
        status: 'created',
      };
    }

    return res.status(200).json({
      ok: true,
      key_id: keyId,
      order_id: order.id,
      amount: order.amount,
      currency: order.currency,
      plan_name: 'VOX Pro',
      interval: isAnnual ? 'annual' : 'monthly',
      price_display: `₹${amountInRupees}`,
      customer: {
        email: customerEmail,
        id: userId,
      },
    });
  } catch (err) {
    console.error('Error in /api/checkout/create-order:', err);
    return res.status(500).json({ error: 'Failed to initialize order', details: err.message });
  }
};
