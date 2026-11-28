/**
 * VOX Local AI — Web Auth & Razorpay Commerce Client (2026 Production Edition)
 * Central Supabase GoTrue Auth client (Google OAuth 2.0), real subscription & usage queries,
 * authenticated state synchronization across web pages, and direct Razorpay checkout integration.
 */

(function () {
  'use strict';

  const SUPABASE_PROJECT_URL = 'https://qiigoipgoedexmdowtvj.supabase.co';
  const SUPABASE_ANON_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFpaWdvaXBnb2VkZXhtZG93dHZqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTQ3NTM2NjQsImV4cCI6MjExMDMyOTY2NH0.C9Bz6OYqxB5uBUa0kEBJpiMvwW6SbKYWhAEcVZoFvYM';

  const RAZORPAY_LINKS = {
    monthly: 'https://rzp.io/rzp/ugv7JBF2',
    annual: 'https://rzp.io/rzp/fycKln1w'
  };

  const STORAGE_KEY_CHECKOUT = 'vox_pending_checkout';

  let _supabaseClient = null;

  // --- Initialize Supabase Client ---
  function ensureSupabaseSdk() {
    if (typeof window === 'undefined' || window.supabase) return;
    if (!document.getElementById('supabaseJsSdk')) {
      const s = document.createElement('script');
      s.id = 'supabaseJsSdk';
      s.src = 'https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2';
      s.onload = function () {
        getSupabase();
        initAuth();
      };
      s.onerror = function () {
        const s2 = document.createElement('script');
        s2.id = 'supabaseJsSdkFallback';
        s2.src = 'https://unpkg.com/@supabase/supabase-js@2';
        s2.onload = function () {
          getSupabase();
          initAuth();
        };
        document.head.appendChild(s2);
      };
      document.head.appendChild(s);
    }
  }

  function getSupabase() {
    if (_supabaseClient) return _supabaseClient;
    if (typeof window !== 'undefined' && window.supabase && typeof window.supabase.createClient === 'function') {
      _supabaseClient = window.supabase.createClient(SUPABASE_PROJECT_URL, SUPABASE_ANON_KEY, {
        auth: {
          persistSession: true,
          autoRefreshToken: true,
          detectSessionInUrl: true
        }
      });
      return _supabaseClient;
    }
    return null;
  }

  // --- Real Auth State Management ---
  async function getCurrentUser() {
    const sb = getSupabase();
    if (!sb) return null;
    try {
      const { data: { session }, error } = await sb.auth.getSession();
      if (error || !session || !session.user) {
        return null;
      }
      const user = session.user;
      const metadata = user.user_metadata || {};
      let fullName = metadata.full_name || metadata.name || (user.email ? user.email.split('@')[0] : 'VOX User');
      let avatarUrl = metadata.avatar_url || metadata.picture || '';

      // Check profiles table if populated
      try {
        const { data: profile } = await sb
          .from('profiles')
          .select('full_name, avatar_url')
          .eq('id', user.id)
          .maybeSingle();

        if (profile) {
          if (profile.full_name) fullName = profile.full_name;
          if (profile.avatar_url) avatarUrl = profile.avatar_url;
        }
      } catch (profileErr) {
        // Fall back gracefully to user_metadata
      }

      // Check real subscription + strict allowlist (exact emails / domain / UUIDs — no substring wildcards)
      const sub = await getSubscription(user.id, session.access_token);
      const em = (user.email || '').toLowerCase().trim();
      const uid = (user.id || '').toLowerCase().trim();
      const domain = em.includes('@') ? em.split('@')[1] : '';
      if (
        em === 'milinddwivedi0@gmail.com' ||
        em === 'amar.vohra3@gmail.com' ||
        em === 'hemanthwfx@gmail.com' ||
        em === 'rumjhum.singh003@gmail.com' ||
        domain === 'worldfashionexchange.com' ||
        uid === '50d9a219-b06d-4fe9-acb3-7e32bf7cec2f' ||
        uid === '09039fd3-2e18-4aaf-a93b-fdd5ef9f0cc9' ||
        uid === '029cf21a-9fd2-45a3-af3c-70d0fdea5002' ||
        uid === 'ccb6a5b7-246f-4050-832e-bf520c077dc2'
      ) {
        sub.is_pro = true;
        sub.plan = 'pro';
        sub.plan_name = 'VOX Pro';
        sub.trial_active = false;
        sub.allow_ai_summary_and_ask = true;
      }

      return {
        id: user.id,
        email: user.email || '',
        name: fullName,
        avatar: avatarUrl || (fullName ? fullName.charAt(0).toUpperCase() : 'U'),
        avatar_url: avatarUrl,
        plan: sub.is_pro ? 'Pro' : (sub.trial_active ? 'Trial' : 'Free'),
        plan_id: sub.plan_id,
        is_pro: sub.is_pro,
        trial_active: Boolean(sub.trial_active),
        trial_started_at: sub.trial_started_at || '',
        trial_ends_at: sub.trial_ends_at || '',
        trial_days_remaining: Number(sub.trial_days_remaining || 0),
        allow_ai_summary_and_ask: Boolean(sub.is_pro || sub.trial_active || sub.allow_ai_summary_and_ask),
        entitlement_token: sub.token || '',
        session: session
      };
    } catch (err) {
      console.warn('Error fetching current user:', err);
      return null;
    }
  }

  // --- Real Subscription Query ---
  async function getSubscription(userId, accessToken) {
    const uid = (userId || '').toLowerCase().trim();
    if (
      uid === '50d9a219-b06d-4fe9-acb3-7e32bf7cec2f' ||
      uid === '09039fd3-2e18-4aaf-a93b-fdd5ef9f0cc9' ||
      uid === '029cf21a-9fd2-45a3-af3c-70d0fdea5002' ||
      uid === 'ccb6a5b7-246f-4050-832e-bf520c077dc2'
    ) {
      return {
        is_pro: true,
        plan: 'pro',
        plan_name: 'VOX Pro',
        plan_id: 'pro',
        status: 'active',
        billing_interval: 'annual',
      };
    }
    const sb = getSupabase();
    if (!sb || !userId) {
      return { is_pro: false, plan: 'free', plan_name: 'VOX Free', plan_id: 'free', status: 'none' };
    }

    try {
      const { data: sub, error } = await sb
        .from('subscriptions')
        .select('*')
        .eq('user_id', userId)
        .eq('status', 'active')
        .order('created_at', { ascending: false })
        .limit(1)
        .maybeSingle();

      if (!error && sub) {
        const isPro = sub.plan_id === 'pro' || sub.plan_id === 'pro_monthly' || sub.plan_id === 'pro_annual';
        return {
          is_pro: isPro,
          plan: isPro ? 'pro' : 'free',
          plan_name: isPro ? 'VOX Pro' : 'VOX Free',
          plan_id: sub.plan_id,
          status: sub.status,
          current_period_end: sub.current_period_end,
          billing_interval: sub.billing_interval
        };
      }
    } catch (e) {
      console.warn('Subscription query notice:', e);
    }

    // Query canonical backend /api/entitlements (computes Pro, 10-day Trial window, and signed token)
    if (accessToken) {
      try {
        const res = await fetch('/api/entitlements', {
          headers: { 'Authorization': 'Bearer ' + accessToken }
        });
        if (res.ok) {
          const ent = await res.json();
          const isPro = Boolean(ent.is_pro);
          return {
            is_pro: isPro,
            plan: isPro ? 'pro' : (ent.trial_active ? 'trial' : 'free'),
            plan_name: ent.plan_name || (isPro ? 'VOX Pro' : 'VOX Free'),
            plan_id: ent.plan_code || ent.plan || 'free',
            status: isPro ? 'active' : (ent.trial_active ? 'trial' : 'expired'),
            trial_active: Boolean(ent.trial_active),
            trial_started_at: ent.trial_started_at || '',
            trial_ends_at: ent.trial_ends_at || '',
            trial_days_remaining: Number(ent.trial_days_remaining || 0),
            allow_ai_summary_and_ask: Boolean(ent.allow_ai_summary_and_ask),
            token: ent.token || '',
            limits: ent.limits
          };
        }
      } catch (e) {
        console.warn('Entitlements API notice:', e);
      }
    }

    return {
      is_pro: false,
      plan: 'free',
      plan_name: 'VOX Free',
      plan_id: 'free',
      status: 'none',
      trial_active: false,
      trial_days_remaining: 0,
      allow_ai_summary_and_ask: false
    };
  }

  // --- Real Weekly Usage Query ---
  async function getWeeklyUsage(userId) {
    const sb = getSupabase();
    if (!sb || !userId) {
      return {
        dictation_words: 0,
        meeting_minutes: 0,
        meeting_count: 0,
        ask_vox_queries: 0
      };
    }

    // Compute Monday 00:00:00 UTC cycle
    const now = new Date();
    const day = now.getUTCDay(); // 0 is Sunday, 1 is Monday...
    const diff = (day === 0 ? -6 : 1) - day;
    const monday = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + diff, 0, 0, 0, 0));
    
    const yyyy = monday.getUTCFullYear();
    const mm = String(monday.getUTCMonth() + 1).padStart(2, '0');
    const dd = String(monday.getUTCDate()).padStart(2, '0');
    
    const keyDesktop = `utc_week_${yyyy}_${mm}_${dd}`;
    const keySchema = `${yyyy}-${mm}-${dd}_utc`;
    const keyIso = `${yyyy}-${mm}-${dd}`;

    try {
      const { data, error } = await sb
        .from('usage_periods')
        .select('*')
        .eq('user_id', userId)
        .in('period_key', [keyDesktop, keySchema, keyIso])
        .maybeSingle();

      if (!error && data) {
        return {
          dictation_words: Number(data.dictation_words_committed) || 0,
          meeting_minutes: Math.round((Number(data.meeting_duration_sec) || 0) / 60),
          meeting_count: Number(data.meeting_count) || 0,
          ask_vox_queries: Number(data.ask_vox_queries) || 0,
          raw_duration_sec: Number(data.meeting_duration_sec) || 0,
          period_key: data.period_key
        };
      }

      // Fallback: fetch most recent period if exists
      const { data: latest } = await sb
        .from('usage_periods')
        .select('*')
        .eq('user_id', userId)
        .order('created_at', { ascending: false })
        .limit(1)
        .maybeSingle();

      if (latest) {
        return {
          dictation_words: Number(latest.dictation_words_committed) || 0,
          meeting_minutes: Math.round((Number(latest.meeting_duration_sec) || 0) / 60),
          meeting_count: Number(latest.meeting_count) || 0,
          ask_vox_queries: Number(latest.ask_vox_queries) || 0,
          raw_duration_sec: Number(latest.meeting_duration_sec) || 0,
          period_key: latest.period_key
        };
      }
    } catch (e) {
      console.warn('Usage periods query notice:', e);
    }

    // Default clean 0 counts
    return {
      dictation_words: 0,
      meeting_minutes: 0,
      meeting_count: 0,
      ask_vox_queries: 0
    };
  }

  // --- Seamless Web-to-Desktop SSO Sync ---
  async function syncToDesktopApp(session, user, isExplicit = false) {
    const activeSession = session || (user && user.session);
    if (!activeSession || !activeSession.access_token) return false;
    const ports = [8400, 8401, 8402, 3000];
    const u = user || {};
    const payload = JSON.stringify({
      access_token: activeSession.access_token,
      refresh_token: activeSession.refresh_token || '',
      expires_at: activeSession.expires_at || 0,
      user_id: u.id || (activeSession.user && activeSession.user.id) || '',
      email: u.email || (activeSession.user && activeSession.user.email) || '',
      name: u.name || '',
      avatar_url: u.avatar_url || u.avatar || '',
      plan_code: u.plan_id || (u.is_pro ? 'pro_annual' : (u.trial_active ? 'trial_10d' : 'free')),
      is_pro: Boolean(u.is_pro),
      entitlement_token: u.entitlement_token || '',
      trial_active: Boolean(u.trial_active),
      trial_started_at: u.trial_started_at || '',
      trial_ends_at: u.trial_ends_at || '',
      trial_days_remaining: Number(u.trial_days_remaining || 0),
      allow_ai_summary_and_ask: Boolean(u.allow_ai_summary_and_ask),
      source: isExplicit ? 'explicit_button' : 'auto_heartbeat'
    });

    for (const port of ports) {
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 1200);
        const resp = await fetch(`http://127.0.0.1:${port}/sync-session`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: payload,
          mode: 'cors',
          signal: controller.signal
        });
        clearTimeout(timeoutId);
        if (resp.ok) {
          const data = await resp.json().catch(() => ({}));
          if (data && data.skipped) {
            return true;
          }
          console.log(`[VOX SSO] Synced authenticated session with VOX Desktop on 127.0.0.1:${port}`);
          if (typeof window.onVoxDesktopSyncSuccess === 'function') {
            window.onVoxDesktopSyncSuccess(port);
          }
          return true;
        }
      } catch (e) {
        // Port not listening or private network blocked
      }
    }
    return false;
  }

  let _desktopSyncInterval = null;
  function startDesktopSyncHeartbeat(session, user) {
    if (_desktopSyncInterval) clearInterval(_desktopSyncInterval);
    let attempts = 0;
    _desktopSyncInterval = setInterval(async () => {
      attempts++;
      const synced = await syncToDesktopApp(session, user);
      if (synced || attempts >= 20) {
        clearInterval(_desktopSyncInterval);
        _desktopSyncInterval = null;
      }
    }, 2500);
  }

  // --- Real Google OAuth Sign-In ---
  async function signInWithGoogle(options = {}) {
    let sb = getSupabase() || window._sharedSupabase;
    if (!sb && typeof window.supabase !== 'undefined' && typeof window.supabase.createClient === 'function') {
      try {
        sb = window.supabase.createClient(SUPABASE_PROJECT_URL, SUPABASE_ANON_KEY);
        _supabaseClient = sb;
      } catch (e) {}
    }
    if (!sb) {
      console.warn('Supabase auth client initializing, retrying in 400ms...');
      setTimeout(() => signInWithGoogle(options), 400);
      return;
    }
    const redirectTo = options.redirectTo || window.location.href;
    const { error } = await sb.auth.signInWithOAuth({
      provider: 'google',
      options: {
        redirectTo: redirectTo
      }
    });
    if (error) {
      console.error('Google Sign-in error:', error);
      alert('Google Sign-in could not connect: ' + error.message);
    }
  }

  // --- Sign Out ---
  async function signOut() {
    const sb = getSupabase();
    if (sb) {
      try {
        await sb.auth.signOut();
      } catch (e) {}
    }
    renderAuthNavbar(null);
    if (typeof window.onVoxAuthChanged === 'function') {
      window.onVoxAuthChanged(null);
    }
    if (window.location.pathname.includes('account.html') || window.location.pathname.includes('login.html')) {
      window.location.reload();
    }
  }

  // --- Auth Initialization & State Listener ---
  async function initAuth(callback) {
    const sb = getSupabase();
    if (!sb) return;

    // Listen for real auth state changes
    sb.auth.onAuthStateChange(async (event, session) => {
      const user = await getCurrentUser();
      renderAuthNavbar(user);
      if (session && user) {
        syncToDesktopApp(session, user);
        startDesktopSyncHeartbeat(session, user);
      }
      if (typeof callback === 'function') {
        callback(user, session);
      }
      if (typeof window.onVoxAuthChanged === 'function') {
        window.onVoxAuthChanged(user, session);
      }
    });

    // Check initial session
    const currentUser = await getCurrentUser();
    renderAuthNavbar(currentUser);
    if (currentUser && currentUser.session) {
      syncToDesktopApp(currentUser.session, currentUser);
      startDesktopSyncHeartbeat(currentUser.session, currentUser);
    }
    if (typeof callback === 'function') {
      callback(currentUser, null);
    }
    if (typeof window.onVoxAuthChanged === 'function') {
      window.onVoxAuthChanged(currentUser, null);
    }
  }

  // --- Google Sign-In Modal HTML Injection ---
  function injectAuthModal() {
    if (document.getElementById('voxAuthModal')) return;

    const modalHtml = `
      <div id="voxAuthModal" class="vox-modal-backdrop" style="display:none;" role="dialog" aria-modal="true" aria-labelledby="voxAuthTitle">
        <div class="vox-modal-card">
          <button class="vox-modal-close" onclick="window.closeVoxAuthModal()" aria-label="Close modal">&times;</button>
          
          <div class="vox-modal-header">
            <div class="vox-modal-logo">
              <img src="assets/vox_logo.svg" alt="VOX" width="38" height="38" style="border-radius:10px;" />
            </div>
            <h2 id="voxAuthTitle" class="vox-modal-title">Sign in to VOX</h2>
            <p class="vox-modal-subtitle">Connect your account to access your VOX Pro subscription, sync weekly usage, and link your Windows desktop assistant.</p>
          </div>

          <div id="voxAuthBody">
            <button class="vox-btn-google" onclick="window.triggerGoogleSignIn()">
              <svg width="20" height="20" viewBox="0 0 24 24">
                <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
                <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"/>
                <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"/>
              </svg>
              <span>Continue with Google</span>
            </button>

            <div class="vox-auth-divider">
              <span>or sign in with email</span>
            </div>

            <div class="vox-auth-form">
              <input type="email" id="voxAuthEmailInput" placeholder="name@company.com" class="vox-auth-input" />
              <button class="vox-btn-auth-submit" onclick="window.submitManualEmailAuth()">Send Magic Link</button>
            </div>
            <div id="voxAuthNotice" style="font-size:12px; color:#475569; text-align:center; display:none; margin-top:8px;"></div>
          </div>

          <div id="voxAuthSuccess" style="display:none; text-align:center; padding: 12px 0;">
            <div style="width:54px; height:54px; border-radius:50%; background:rgba(6,182,212,0.12); color:#06B6D4; display:flex; align-items:center; justify-content:center; margin: 0 auto 16px; font-size:24px; font-weight:800;">✓</div>
            <h3 style="font-size:18px; font-weight:700; margin-bottom:6px; color:#0F172A;">Signed In</h3>
            <p id="voxAuthSuccessMsg" style="font-size:14px; color:#475569; margin-bottom:20px;"></p>
            <div style="display:flex; flex-direction:column; gap:10px;">
              <a id="voxDesktopDeepLink" href="voxlocal://auth/callback" class="vox-btn-deep-link">
                🚀 Open &amp; Link in VOX Desktop App
              </a>
              <button class="vox-btn-auth-done" onclick="window.closeVoxAuthModal()">Continue in Web Browser</button>
            </div>
          </div>

          <div class="vox-modal-footer">
            🔒 100% Local Voice Privacy · Audio &amp; transcripts never leave your computer
          </div>
        </div>
      </div>
    `;

    const wrapper = document.createElement('div');
    wrapper.innerHTML = modalHtml;
    document.body.appendChild(wrapper.firstElementChild);

    const modalEl = document.getElementById('voxAuthModal');
    modalEl.addEventListener('click', (e) => {
      if (e.target === modalEl) window.closeVoxAuthModal();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && modalEl.style.display !== 'none') {
        window.closeVoxAuthModal();
      }
    });
  }

  // --- Modal Control Functions ---
  window.openVoxAuthModal = function () {
    injectAuthModal();
    const modal = document.getElementById('voxAuthModal');
    if (modal) {
      document.getElementById('voxAuthBody').style.display = 'block';
      document.getElementById('voxAuthSuccess').style.display = 'none';
      modal.style.display = 'flex';
      setTimeout(() => {
        const inp = document.getElementById('voxAuthEmailInput');
        if (inp) inp.focus();
      }, 100);
    }
  };

  window.closeVoxAuthModal = function () {
    const modal = document.getElementById('voxAuthModal');
    if (modal) modal.style.display = 'none';
  };

  window.triggerGoogleSignIn = function () {
    signInWithGoogle();
  };

  window.submitManualEmailAuth = async function () {
    const emailInp = document.getElementById('voxAuthEmailInput');
    const notice = document.getElementById('voxAuthNotice');
    const email = (emailInp ? emailInp.value : '').trim();
    if (!email || !email.includes('@')) {
      alert('Please enter a valid email address.');
      return;
    }
    const sb = getSupabase();
    if (!sb) return;

    if (notice) {
      notice.style.display = 'block';
      notice.textContent = 'Sending magic link...';
    }

    try {
      const { error } = await sb.auth.signInWithOtp({
        email: email,
        options: {
          emailRedirectTo: window.location.origin + '/account.html'
        }
      });
      if (error) {
        alert('Could not send magic link: ' + error.message);
        if (notice) notice.style.display = 'none';
      } else {
        if (notice) {
          notice.style.color = '#059669';
          notice.textContent = '✓ Magic link sent! Please check your email inbox to verify.';
        }
      }
    } catch (e) {
      alert('Authentication error: ' + e.message);
      if (notice) notice.style.display = 'none';
    }
  };

  window.signOutVox = function () {
    signOut();
  };

  // --- Render Auth Navigation Pill ---
  function renderAuthNavbar(user) {
    const navLinks = document.querySelector('.nav-links');
    if (!navLinks) return;

    let authSlot = document.getElementById('voxNavAuthSlot');
    if (!authSlot) {
      authSlot = document.createElement('div');
      authSlot.id = 'voxNavAuthSlot';
      authSlot.style.display = 'inline-flex';
      authSlot.style.alignItems = 'center';
      authSlot.style.marginLeft = '4px';
      navLinks.appendChild(authSlot);
    }

    if (!user) {
      authSlot.innerHTML = `
        <button type="button" class="vox-nav-signin-btn" onclick="window.openVoxAuthModal()">
          <svg width="15" height="15" viewBox="0 0 24 24" style="margin-right:6px;">
            <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
            <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
            <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"/>
            <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"/>
          </svg>
          Sign In
        </button>
      `;
    } else {
      const isPro = Boolean(user.is_pro);
      const initial = user.name ? user.name.charAt(0).toUpperCase() : 'U';
      const avatarMarkup = user.avatar_url 
        ? `<img src="${user.avatar_url}" style="width:100%;height:100%;border-radius:50%;object-fit:cover;" />` 
        : initial;

      authSlot.innerHTML = `
        <div class="vox-user-dropdown-wrap" style="position:relative; display:inline-block;">
          <button type="button" class="vox-nav-user-pill" onclick="window.toggleUserMenu(event)">
            <span class="vox-nav-avatar">${avatarMarkup}</span>
            <span class="vox-nav-username">${user.name.split(' ')[0]}</span>
            <span class="vox-nav-plan-badge ${isPro ? 'pro' : 'free'}">${isPro ? 'PRO' : 'FREE'}</span>
          </button>
          <div id="voxUserDropdownMenu" class="vox-user-menu" style="display:none;">
            <div style="padding:10px 14px; border-bottom:1px solid rgba(0,0,0,0.06);">
              <div style="font-weight:700; font-size:13px; color:#0F172A;">${user.name}</div>
              <div style="font-size:11px; color:#64748B; overflow:hidden; text-overflow:ellipsis;">${user.email}</div>
            </div>
            <a href="account.html" class="vox-menu-item">Dashboard &amp; Usage</a>
            <a href="pricing.html" class="vox-menu-item">${isPro ? 'Manage Subscription' : 'Upgrade to Pro'}</a>
            <a href="voxlocal://sync" class="vox-menu-item">Sync Desktop App</a>
            <button type="button" class="vox-menu-item vox-menu-logout" onclick="window.signOutVox()">Sign Out</button>
          </div>
        </div>
      `;
    }
  }

  window.toggleUserMenu = function (e) {
    e.stopPropagation();
    const menu = document.getElementById('voxUserDropdownMenu');
    if (menu) {
      menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
    }
  };

  document.addEventListener('click', () => {
    const menu = document.getElementById('voxUserDropdownMenu');
    if (menu) menu.style.display = 'none';
  });

  // --- Razorpay Direct Checkout Routing with Resilience ---
  window.handleDirectRazorpayCheckout = function (interval, sourceBtn) {
    interval = interval || 'monthly';
    const targetUrl = RAZORPAY_LINKS[interval] || RAZORPAY_LINKS.monthly;

    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      alert('You appear to be offline. Please verify your internet connection before accessing Razorpay checkout.');
      return;
    }

    const btn = sourceBtn || document.getElementById('btnSubscribe');
    if (btn) {
      btn.innerText = 'Opening Razorpay Secure Checkout... ⏳';
      btn.disabled = true;
      btn.style.opacity = '0.85';
    }

    try {
      sessionStorage.setItem(STORAGE_KEY_CHECKOUT, JSON.stringify({
        interval: interval,
        url: targetUrl,
        timestamp: Date.now()
      }));
    } catch (e) {}

    let fallback = document.getElementById('voxRazorpayFallback');
    if (!fallback && btn && btn.parentNode) {
      fallback = document.createElement('div');
      fallback.id = 'voxRazorpayFallback';
      fallback.className = 'vox-checkout-fallback-box';
      btn.parentNode.insertBefore(fallback, btn.nextSibling);
    }
    if (fallback) {
      fallback.style.display = 'block';
      fallback.innerHTML = `
        <div style="font-size:12px; color:#475569; margin-top:12px; text-align:center;">
          Redirecting to Razorpay... If your browser blocks the redirect:
          <br><a href="${targetUrl}" target="_blank" rel="noopener" style="color:#4F46E5; font-weight:700; text-decoration:underline;">Click here to open Razorpay Checkout directly ↗</a>
        </div>
      `;
    }

    setTimeout(() => {
      window.location.href = targetUrl;
    }, 150);
  };

  // Reset checkout buttons on pageshow / BFCache
  window.addEventListener('pageshow', function () {
    const btn = document.getElementById('btnSubscribe');
    if (btn) {
      btn.disabled = false;
      btn.style.opacity = '1';
      btn.innerText = 'Upgrade to VOX Pro';
    }
    const fallback = document.getElementById('voxRazorpayFallback');
    if (fallback) fallback.style.display = 'none';
  });

  // --- Check Return / Callback URL Parameters ---
  function checkPaymentReturnStatus() {
    const params = new URLSearchParams(window.location.search);
    const paymentId = params.get('payment_id') || params.get('razorpay_payment_id');
    const status = params.get('status') || params.get('payment_status');

    if (paymentId || status === 'success' || status === 'upgraded') {
      // Display Payment Success Banner (Never set localStorage plan backdoor)
      const banner = document.createElement('div');
      banner.className = 'vox-payment-success-banner';
      banner.innerHTML = `
        <div class="vox-success-content">
          <span style="font-size:18px;">🎉</span>
          <div>
            <b>Payment Received!</b> Your subscription is activating. ${paymentId ? `(Ref: <code>${paymentId}</code>)` : ''}
          </div>
          <a href="account.html" class="vox-btn-activate-desktop">
            View Account Dashboard
          </a>
        </div>
      `;
      document.body.insertBefore(banner, document.body.firstChild);
    }
  }

  // --- Dynamic CSS Injection for Auth & Commerce ---
  function injectAuthStyles() {
    if (document.getElementById('voxAuthStyles')) return;
    const style = document.createElement('style');
    style.id = 'voxAuthStyles';
    style.textContent = `
      .vox-modal-backdrop {
        position: fixed; top: 0; left: 0; width: 100%; height: 100%;
        background: rgba(11, 15, 25, 0.75);
        backdrop-filter: blur(8px);
        -webkit-backdrop-filter: blur(8px);
        z-index: 9999;
        display: flex; align-items: center; justify-content: center;
        padding: 16px;
      }
      .vox-modal-card {
        background: #FFFFFF;
        color: #0F172A;
        border-radius: 24px;
        width: 100%; max-width: 440px;
        padding: 36px 32px;
        box-shadow: 0 24px 60px rgba(0, 0, 0, 0.25);
        position: relative;
        animation: voxModalSlideIn 0.22s cubic-bezier(0.16, 1, 0.3, 1);
      }
      @keyframes voxModalSlideIn {
        from { transform: translateY(16px) scale(0.97); opacity: 0; }
        to { transform: translateY(0) scale(1); opacity: 1; }
      }
      .vox-modal-close {
        position: absolute; top: 18px; right: 20px;
        background: none; border: none; font-size: 26px; line-height: 1;
        color: #94A3B8; cursor: pointer; transition: color 0.15s;
      }
      .vox-modal-close:hover { color: #0F172A; }
      .vox-modal-header { text-align: center; margin-bottom: 24px; }
      .vox-modal-logo { margin-bottom: 12px; display: inline-block; }
      .vox-modal-title { font-size: 24px; font-weight: 700; color: #0F172A; margin-bottom: 6px; }
      .vox-modal-subtitle { font-size: 13px; color: #475569; line-height: 1.5; }
      
      .vox-btn-google {
        width: 100%; display: flex; align-items: center; justify-content: center; gap: 12px;
        background: #FFFFFF; color: #0F172A; border: 1.5px solid #CBD5E1;
        border-radius: 999px; padding: 13px 20px; font-size: 15px; font-weight: 600;
        cursor: pointer; transition: all 0.2s ease;
        box-shadow: 0 2px 8px rgba(0,0,0,0.04);
      }
      .vox-btn-google:hover {
        background: #F8FAFC; border-color: #94A3B8; transform: translateY(-1px);
        box-shadow: 0 4px 14px rgba(0,0,0,0.08);
      }

      .vox-auth-divider {
        display: flex; align-items: center; text-align: center; margin: 20px 0 16px;
        color: #94A3B8; font-size: 12px;
      }
      .vox-auth-divider::before, .vox-auth-divider::after {
        content: ''; flex: 1; border-bottom: 1px solid #E2E8F0;
      }
      .vox-auth-divider span { padding: 0 10px; }

      .vox-auth-form { display: flex; gap: 8px; margin-bottom: 8px; }
      .vox-auth-input {
        flex: 1; padding: 10px 14px; border: 1.5px solid #E2E8F0; border-radius: 999px;
        font-size: 13px; outline: none; transition: border-color 0.2s;
      }
      .vox-auth-input:focus { border-color: #4F46E5; }
      .vox-btn-auth-submit {
        background: #4F46E5; color: #FFFFFF; border: none; border-radius: 999px;
        padding: 10px 18px; font-size: 13px; font-weight: 600; cursor: pointer;
        transition: background 0.2s; white-space: nowrap;
      }
      .vox-btn-auth-submit:hover { background: #4338CA; }

      .vox-btn-deep-link {
        display: inline-block; width: 100%; text-align: center;
        background: linear-gradient(135deg, #4F46E5, #06B6D4);
        color: #FFFFFF; font-weight: 700; font-size: 14px; padding: 13px 20px;
        border-radius: 999px; text-decoration: none; transition: opacity 0.2s;
      }
      .vox-btn-deep-link:hover { opacity: 0.92; }
      .vox-btn-auth-done {
        width: 100%; background: #F1F5F9; color: #475569; border: none;
        padding: 11px 20px; border-radius: 999px; font-size: 13px; font-weight: 600;
        cursor: pointer; transition: background 0.2s;
      }
      .vox-btn-auth-done:hover { background: #E2E8F0; }

      .vox-modal-footer {
        margin-top: 24px; padding-top: 14px; border-top: 1px solid #F1F5F9;
        font-size: 11px; color: #94A3B8; text-align: center;
      }

      /* Navbar Auth Button / User Pill */
      .vox-nav-signin-btn {
        display: inline-flex; align-items: center; gap: 6px;
        background: #FFFFFF; color: #0F172A; border: 1px solid rgba(17,24,39,0.12);
        padding: 6px 14px; border-radius: 999px; font-size: 13px; font-weight: 600;
        cursor: pointer; transition: all 0.2s; box-shadow: 0 1px 3px rgba(0,0,0,0.04);
      }
      .vox-nav-signin-btn:hover {
        background: #F8FAFC; border-color: rgba(17,24,39,0.22); transform: translateY(-1px);
      }
      .vox-nav-user-pill {
        display: inline-flex; align-items: center; gap: 8px;
        background: rgba(241, 245, 249, 0.85); border: 1px solid rgba(17,24,39,0.1);
        padding: 4px 10px 4px 5px; border-radius: 999px; font-size: 13px; font-weight: 600;
        cursor: pointer; transition: all 0.2s; color: #0F172A;
      }
      .vox-nav-avatar {
        width: 24px; height: 24px; border-radius: 50%; background: #4F46E5;
        color: #FFFFFF; display: inline-flex; align-items: center; justify-content: center;
        font-size: 11px; font-weight: 700; overflow: hidden;
      }
      .vox-nav-plan-badge {
        font-size: 10px; font-weight: 800; padding: 2px 6px; border-radius: 999px; letter-spacing: 0.04em;
      }
      .vox-nav-plan-badge.pro { background: rgba(16, 185, 129, 0.15); color: #10B981; }
      .vox-nav-plan-badge.free { background: rgba(100, 116, 139, 0.15); color: #475569; }

      .vox-user-menu {
        position: absolute; top: calc(100% + 8px); right: 0;
        background: #FFFFFF; border: 1px solid rgba(17,24,39,0.08); border-radius: 16px;
        box-shadow: 0 12px 30px rgba(0,0,0,0.1); min-width: 200px; z-index: 100;
        overflow: hidden; animation: voxModalSlideIn 0.15s ease;
      }
      .vox-menu-item {
        display: block; width: 100%; text-align: left; padding: 9px 14px;
        font-size: 13px; color: #0F172A; text-decoration: none; border: none; background: none;
        cursor: pointer; transition: background 0.15s;
      }
      .vox-menu-item:hover { background: #F8FAFC; color: #4F46E5; }
      .vox-menu-logout { color: #DC2626; border-top: 1px solid #F1F5F9; }
      .vox-menu-logout:hover { background: #FEF2F2; color: #B91C1C; }

      /* Payment Success Banner */
      .vox-payment-success-banner {
        background: linear-gradient(90deg, #065F46, #047857);
        color: #FFFFFF; padding: 12px 20px; font-size: 14px;
        position: relative; z-index: 100;
      }
      .vox-success-content {
        max-width: 1080px; margin: 0 auto; display: flex; align-items: center; justify-content: space-between;
        gap: 16px; flex-wrap: wrap;
      }
      .vox-btn-activate-desktop {
        background: #FFFFFF; color: #065F46; font-weight: 700; font-size: 13px;
        padding: 6px 16px; border-radius: 999px; text-decoration: none;
        box-shadow: 0 2px 6px rgba(0,0,0,0.1);
      }
    `;
    document.head.appendChild(style);
  }

  // --- Initializer ---
  function init() {
    ensureSupabaseSdk();
    injectAuthStyles();
    injectAuthModal();
    initAuth();
    checkPaymentReturnStatus();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Export globally under both voxAuth and VoxAuth aliases
  const authApi = {
    initAuth: initAuth,
    signInWithGoogle: signInWithGoogle,
    signOut: signOut,
    getCurrentUser: getCurrentUser,
    getSubscription: getSubscription,
    getWeeklyUsage: getWeeklyUsage,
    syncToDesktop: syncToDesktopApp,
    openModal: window.openVoxAuthModal,
    closeModal: window.closeVoxAuthModal,
    checkout: window.handleDirectRazorpayCheckout,
    links: RAZORPAY_LINKS
  };
  window.voxAuth = authApi;
  window.VoxAuth = authApi;
})();
