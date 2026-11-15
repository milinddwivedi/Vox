const fs = require('fs');
const assert = require('assert');

console.log('=== Milestone 3 Automated Verification ===');

// 1. docs/account.html
const accountHtml = fs.readFileSync('docs/account.html', 'utf8');
assert(!accountHtml.includes('420 / 3,000'), 'account.html should not contain fake 420 words');
assert(!accountHtml.includes('35 / 180'), 'account.html should not contain fake 35 mins');
assert(!accountHtml.includes('2 / 5 sessions (40%)'), 'account.html should not contain fake 2 sessions');
assert(!accountHtml.includes('3 / 10 queries (30%)'), 'account.html should not contain fake 3 queries');
assert(!accountHtml.includes("localStorage.getItem('vox_plan')"), 'account.html should not use localStorage for plan');
assert(accountHtml.includes('https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2'), 'account.html must include Supabase CDN');
assert(accountHtml.includes('handleAuthButtonClick'), 'account.html must have real auth handler');
assert(accountHtml.includes('getWeeklyUsage'), 'account.html must call getWeeklyUsage');
console.log('[PASS] docs/account.html verified clean of fake metrics & wired to Supabase');

// 2. docs/assets/vox-auth.js
const voxAuthJs = fs.readFileSync('docs/assets/vox-auth.js', 'utf8');
assert(!voxAuthJs.includes('vox_tok_'), 'vox-auth.js should not contain fake tokens');
assert(!voxAuthJs.includes('vox_oauth_tok_'), 'vox-auth.js should not contain mock oauth tokens');
assert(!voxAuthJs.includes("localStorage.setItem('vox_plan', 'pro')"), 'vox-auth.js should not have plan backdoor');
assert(voxAuthJs.includes('qiigoipgoedexmdowtvj.supabase.co'), 'vox-auth.js must have real Supabase project URL');
assert(voxAuthJs.includes('signInWithGoogle'), 'vox-auth.js must export signInWithGoogle');
assert(voxAuthJs.includes('getCurrentUser'), 'vox-auth.js must export getCurrentUser');
assert(voxAuthJs.includes('getSubscription'), 'vox-auth.js must export getSubscription');
assert(voxAuthJs.includes('getWeeklyUsage'), 'vox-auth.js must export getWeeklyUsage');
console.log('[PASS] docs/assets/vox-auth.js verified clean and exports auth & usage APIs');

// 3. docs/login.html
const loginHtml = fs.readFileSync('docs/login.html', 'utf8');
assert(!loginHtml.includes('applyPreset'), 'login.html must not contain mock account presets');
assert(!loginHtml.includes('vox_tok_'), 'login.html must not contain fake tokens');
assert(loginHtml.includes('https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2'), 'login.html must include Supabase CDN');
assert(loginHtml.includes('handleGoogleSignIn'), 'login.html must include Google sign-in handler');
console.log('[PASS] docs/login.html verified clean of fake presets & wired to Google OAuth');

// 4. api/auth/desktop-login.js
const desktopLoginJs = fs.readFileSync('api/auth/desktop-login.js', 'utf8');
assert(desktopLoginJs.includes('127.0.0.1:8400/callback'), 'desktop-login.js must default to /callback');
assert(desktopLoginJs.includes('qiigoipgoedexmdowtvj.supabase.co'), 'desktop-login.js must use real Supabase project URL');
console.log('[PASS] api/auth/desktop-login.js defaults to /callback and real project URL');

// 5. api/auth/desktop-callback.js
const desktopCallback = require('../api/auth/desktop-callback');
assert.strictEqual(typeof desktopCallback, 'function', 'desktop-callback must export handler function');

// Test desktop-callback execution
let capturedStatus = null;
let capturedHtml = '';
const mockReq = {
  method: 'GET',
  query: {
    target: 'http://127.0.0.1:8400/callback',
    state: 'test_state_verification_123'
  }
};
const mockRes = {
  setHeader: (k, v) => {},
  status: (code) => {
    capturedStatus = code;
    return {
      send: (html) => { capturedHtml = html; },
      json: (data) => {},
      end: () => {}
    };
  }
};
desktopCallback(mockReq, mockRes).then(() => {
  assert.strictEqual(capturedStatus, 200, 'desktop-callback status should be 200');
  assert(capturedHtml.includes('http://127.0.0.1:8400/callback'), 'callback HTML must include target loopback URL');
  assert(capturedHtml.includes('test_state_verification_123'), 'callback HTML must include state');
  console.log('[PASS] api/auth/desktop-callback.js executes successfully and renders loopback relay');
  console.log('=== All Milestone 3 Checks PASSED ===');
}).catch(err => {
  console.error('Test execution error:', err);
  process.exit(1);
});
