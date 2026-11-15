/**
 * VOX Local AI — Web Auth & Razorpay Commerce Client (2026 Edition)
 * Handles Google Sign-in modal, account state synchronization across pages,
 * direct Razorpay checkout links with full fallbacks (back-button, reload, offline),
 * and desktop deep-linking (voxlocal://).
 */

(function () {
  'use strict';

  const RAZORPAY_LINKS = {
    monthly: 'https://rzp.io/rzp/ugv7JBF2',
    annual: 'https://rzp.io/rzp/fycKln1w'
  };

  const STORAGE_KEY_USER = 'vox_user';
  const STORAGE_KEY_CHECKOUT = 'vox_pending_checkout';

  // --- Auth State Management ---
  function getUser() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY_USER);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  function setUser(userObj) {
    try {
      if (!userObj) {
        localStorage.removeItem(STORAGE_KEY_USER);
      } else {
        localStorage.setItem(STORAGE_KEY_USER, JSON.stringify(userObj));
      }
      renderAuthNavbar();
      if (typeof window.onVoxAuthChanged === 'function') {
        window.onVoxAuthChanged(userObj);
      }
    } catch (e) {
      console.error('Failed to save vox_user:', e);
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
            <p class="vox-modal-subtitle">Sync your subscription, access weekly usage metrics, and link your Windows desktop assistant.</p>
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
              <span>or enter custom email for testing</span>
            </div>

            <div class="vox-auth-form">
              <input type="email" id="voxAuthEmailInput" placeholder="name@company.com" class="vox-auth-input" />
              <button class="vox-btn-auth-submit" onclick="window.submitManualEmailAuth()">Sign In / Verify</button>
            </div>

            <div class="vox-auth-presets">
              <span style="font-size:11px; color:#64748B; font-weight:600;">1-Click Quick Accounts:</span>
              <div style="display:flex; gap:8px; margin-top:6px; flex-wrap:wrap;">
                <button type="button" class="vox-preset-pill" onclick="window.quickAuth('Milind Dwivedi', 'milinddwivedi7@gmail.com')">milinddwivedi7@gmail.com</button>
                <button type="button" class="vox-preset-pill" onclick="window.quickAuth('Alex Morgan', 'alex.morgan@test.com')">alex.morgan@test.com</button>
              </div>
            </div>
          </div>

          <div id="voxAuthSuccess" style="display:none; text-align:center; padding: 12px 0;">
            <div style="width:54px; height:54px; border-radius:50%; background:rgba(6,182,212,0.12); color:#06B6D4; display:flex; align-items:center; justify-content:center; margin: 0 auto 16px; font-size:24px; font-weight:800;">✓</div>
            <h3 style="font-size:18px; font-weight:700; margin-bottom:6px; color:#0F172A;">Successfully Signed In</h3>
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

    // Close on backdrop click or ESC
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
    // Official Google OAuth Simulator / Test account
    const user = {
      name: 'Google User',
      email: 'user@google.com',
      avatar: 'G',
      plan: localStorage.getItem('vox_plan') || 'Free',
      token: 'vox_oauth_tok_' + Math.random().toString(36).substring(2, 10),
      signedInAt: new Date().toISOString()
    };
    completeSignIn(user);
  };

  window.submitManualEmailAuth = function () {
    const emailInp = document.getElementById('voxAuthEmailInput');
    const val = (emailInp ? emailInp.value : '').trim();
    if (!val || !val.includes('@')) {
      alert('Please enter a valid email address.');
      return;
    }
    const namePart = val.split('@')[0].replace(/[._-]/g, ' ');
    const displayName = namePart.charAt(0).toUpperCase() + namePart.slice(1);
    const user = {
      name: displayName,
      email: val,
      avatar: displayName.charAt(0).toUpperCase(),
      plan: localStorage.getItem('vox_plan') || 'Free',
      token: 'vox_email_tok_' + Math.random().toString(36).substring(2, 10),
      signedInAt: new Date().toISOString()
    };
    completeSignIn(user);
  };

  window.quickAuth = function (name, email) {
    const user = {
      name: name,
      email: email,
      avatar: name.charAt(0).toUpperCase(),
      plan: localStorage.getItem('vox_plan') || 'Free',
      token: 'vox_quick_tok_' + Math.random().toString(36).substring(2, 10),
      signedInAt: new Date().toISOString()
    };
    completeSignIn(user);
  };

  function completeSignIn(user) {
    setUser(user);
    const bodyEl = document.getElementById('voxAuthBody');
    const successEl = document.getElementById('voxAuthSuccess');
    const msgEl = document.getElementById('voxAuthSuccessMsg');
    const deepLinkEl = document.getElementById('voxDesktopDeepLink');

    if (bodyEl && successEl && msgEl) {
      bodyEl.style.display = 'none';
      msgEl.innerHTML = `Signed in as <b>${user.name}</b> (${user.email})<br>Plan: <b style="color:${user.plan === 'Pro' ? '#06B6D4' : '#64748B'};">${user.plan}</b>`;
      if (deepLinkEl) {
        deepLinkEl.href = `voxlocal://auth/callback?email=${encodeURIComponent(user.email)}&name=${encodeURIComponent(user.name)}&token=${encodeURIComponent(user.token)}`;
      }
      successEl.style.display = 'block';
    }
  }

  window.signOutVox = function () {
    setUser(null);
    if (window.location.pathname.includes('account.html')) {
      window.location.reload();
    }
  };

  // --- Render Auth Navigation Pill ---
  function renderAuthNavbar() {
    const user = getUser();
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
      const isPro = user.plan === 'Pro' || localStorage.getItem('vox_plan') === 'pro';
      authSlot.innerHTML = `
        <div class="vox-user-dropdown-wrap" style="position:relative; display:inline-block;">
          <button type="button" class="vox-nav-user-pill" onclick="window.toggleUserMenu(event)">
            <span class="vox-nav-avatar">${user.avatar || 'U'}</span>
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

  // --- Razorpay Direct Checkout Routing with Full Edge Case Resilience ---
  window.handleDirectRazorpayCheckout = function (interval, sourceBtn) {
    interval = interval || 'monthly';
    const targetUrl = RAZORPAY_LINKS[interval] || RAZORPAY_LINKS.monthly;

    // Check connectivity
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      alert('You appear to be offline. Please verify your internet connection before accessing Razorpay checkout.');
      return;
    }

    const btn = sourceBtn || document.getElementById('btnSubscribe');
    const originalText = btn ? btn.innerText : 'Upgrade to VOX Pro';

    if (btn) {
      btn.innerText = 'Opening Razorpay Secure Checkout... ⏳';
      btn.disabled = true;
      btn.style.opacity = '0.85';
    }

    // Save state in case of reload or back navigation
    try {
      sessionStorage.setItem(STORAGE_KEY_CHECKOUT, JSON.stringify({
        interval: interval,
        url: targetUrl,
        timestamp: Date.now()
      }));
    } catch (e) {}

    // Show fallback container in case popup/redirect is blocked or slow
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

    // Proceed to Razorpay checkout page
    setTimeout(() => {
      window.location.href = targetUrl;
    }, 150);
  };

  // --- Back-Button & PageShow Fallback Handler ---
  // When user returns via Back button or BFCache, reset checkout buttons
  window.addEventListener('pageshow', function (event) {
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
      localStorage.setItem('vox_plan', 'pro');
      const currentUser = getUser();
      if (currentUser) {
        currentUser.plan = 'Pro';
        setUser(currentUser);
      }

      // Display Top Notification Banner
      const banner = document.createElement('div');
      banner.className = 'vox-payment-success-banner';
      banner.innerHTML = `
        <div class="vox-success-content">
          <span style="font-size:18px;">🎉</span>
          <div>
            <b>Payment Successful!</b> Your VOX Pro plan is now active. ${paymentId ? `(Ref: <code>${paymentId}</code>)` : ''}
          </div>
          <a href="voxlocal://entitlements/activate?plan=pro&payment_id=${encodeURIComponent(paymentId || '')}" class="vox-btn-activate-desktop">
            Activate in VOX Desktop
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

      .vox-auth-form { display: flex; gap: 8px; margin-bottom: 16px; }
      .vox-auth-input {
        flex: 1; padding: 10px 14px; border: 1.5px solid #E2E8F0; border-radius: 999px;
        font-size: 13px; outline: none; transition: border-color 0.2s;
      }
      .vox-auth-input:focus { border-color: #4F46E5; }
      .vox-btn-auth-submit {
        background: #4F46E5; color: #FFFFFF; border: none; border-radius: 999px;
        padding: 10px 18px; font-size: 13px; font-weight: 600; cursor: pointer;
        transition: background 0.2s;
      }
      .vox-btn-auth-submit:hover { background: #4338CA; }

      .vox-preset-pill {
        background: #F1F5F9; border: 1px solid #E2E8F0; border-radius: 999px;
        padding: 4px 10px; font-size: 11px; color: #475569; cursor: pointer; transition: all 0.15s;
      }
      .vox-preset-pill:hover { background: #E2E8F0; color: #0F172A; }

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
        font-size: 11px; font-weight: 700;
      }
      .vox-nav-plan-badge {
        font-size: 10px; font-weight: 800; padding: 2px 6px; border-radius: 999px; letter-spacing: 0.04em;
      }
      .vox-nav-plan-badge.pro { background: rgba(6, 182, 212, 0.15); color: #0891B2; }
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
    injectAuthStyles();
    injectAuthModal();
    renderAuthNavbar();
    checkPaymentReturnStatus();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Export globally
  window.voxAuth = {
    getUser: getUser,
    setUser: setUser,
    openModal: window.openVoxAuthModal,
    closeModal: window.closeVoxAuthModal,
    checkout: window.handleDirectRazorpayCheckout,
    links: RAZORPAY_LINKS
  };
})();
