/* Claimifi.biz — pricing, account, email-link and password-reset pages.
   Every call goes to this site's own /api; the browser never talks to Supabase
   or Stripe and never holds a token (the session lives in HttpOnly cookies). */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var page = document.body.getAttribute('data-page');
  var root = document.documentElement;
  var ACCOUNTS_ON = root.getAttribute('data-accounts') === 'on';
  var BILLING_ON = root.getAttribute('data-billing') === 'on';

  /* ---------------- Helpers ---------------- */

  function request(method, url, body) {
    var opts = {
      method: method,
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { 'Accept': 'application/json' }
    };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        return { ok: res.ok, status: res.status, data: data || {} };
      });
    }, function () {
      return { ok: false, status: 0, data: { detail: 'Could not reach Claimifi.biz. Check your connection and try again.' } };
    });
  }

  // FastAPI sends `detail` as a list for validation errors and a string otherwise.
  function errorText(r) {
    var d = r.data && r.data.detail;
    if (typeof d === 'string' && d) return d;
    if (Array.isArray(d) && d.length && d[0] && d[0].msg) return d[0].msg;
    return 'Something went wrong (' + (r.status || 'offline') + '). Please try again.';
  }

  function say(el, kind, text) {
    if (!el) return;
    el.className = 'msg ' + kind;
    el.textContent = text || '';
  }

  function params() {
    try { return new URLSearchParams(window.location.search); } catch (e) { return { get: function () { return null; } }; }
  }

  // Where to go after signing in. Only a path on this site: never a scheme, a
  // //host or a backslash, which some browsers read as a slash.
  function safeNext(value) {
    if (!value || value.charAt(0) !== '/' || value.charAt(1) === '/' || value.indexOf('\\') > -1) return null;
    return value;
  }

  function busy(button, on) {
    if (!button) return;
    button.disabled = !!on;
    if (on) button.setAttribute('aria-busy', 'true'); else button.removeAttribute('aria-busy');
  }

  function money(amount, currency) {
    if (typeof amount !== 'number') return null;
    try {
      return new Intl.NumberFormat(undefined, { style: 'currency', currency: (currency || 'usd').toUpperCase() }).format(amount / 100);
    } catch (e) {
      return '$' + (amount / 100).toFixed(2);
    }
  }

  function longDate(unixSeconds) {
    if (typeof unixSeconds !== 'number') return '';
    try {
      return new Date(unixSeconds * 1000).toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' });
    } catch (e) { return ''; }
  }

  /* ---------------- Pricing ---------------- */

  function pricingPage() {
    var msg = $('pricingMsg');
    var q = params();
    if (q.get('checkout') === 'cancelled') {
      say(msg, 'info', "Checkout was cancelled, and you haven't been charged.");
    }
    if (!BILLING_ON) return;

    var monthlyBtn = $('intervalMonthly');
    var yearlyBtn = $('intervalYearly');
    var subscribe = $('subscribeBtn');
    var status = null;
    var interval = q.get('interval') === 'yearly' ? 'yearly' : 'monthly';

    function render() {
      monthlyBtn.setAttribute('aria-pressed', interval === 'monthly' ? 'true' : 'false');
      yearlyBtn.setAttribute('aria-pressed', interval === 'yearly' ? 'true' : 'false');
      var prices = (status && status.prices) || {};
      var p = prices[interval];
      var m = prices.monthly;
      var y = prices.yearly;
      $('proPrice').textContent = p ? money(p.amount, p.currency) : '—';
      $('proPer').textContent = p ? (interval === 'yearly' ? 'per year' : 'per month') : '';
      var note = '';
      if (interval === 'yearly' && y && m && typeof y.amount === 'number' && typeof m.amount === 'number') {
        note = 'Works out at ' + money(Math.round(y.amount / 12), y.currency) + ' a month.';
      } else if (interval === 'monthly') {
        note = 'Billed every month. Cancel any time.';
      }
      $('proNote').textContent = note;
      if (y && m && typeof y.amount === 'number' && typeof m.amount === 'number' && m.amount > 0) {
        var saving = Math.round((1 - y.amount / (12 * m.amount)) * 100);
        $('yearlySave').textContent = saving > 0 ? 'save ' + saving + '%' : '';
      }
      if (status && status.plan === 'pro' && status.cancel_at_period_end) {
        // Cancelled but still paid up: say when it ends, and offer the way back.
        var ends = longDate(status.current_period_end);
        subscribe.textContent = 'Keep Pro: it ends ' + (ends ? 'on ' + ends : 'soon');
        say(msg, 'info', 'Your Pro plan is cancelled and ends ' + (ends ? 'on ' + ends : 'at the end of this period') +
          '. You can keep it from your account page.');
      } else if (status && status.plan === 'pro') {
        subscribe.textContent = 'You have Pro: manage your plan';
      } else {
        subscribe.textContent = 'Subscribe to Pro' + (p ? ' · ' + money(p.amount, p.currency) + (interval === 'yearly' ? '/yr' : '/mo') : '');
      }
      subscribe.disabled = !!status && !p && status.plan !== 'pro';
      if (status && !p && status.plan !== 'pro') say(msg, 'err', "Pro isn't available to buy right now. Please check back soon.");
    }

    monthlyBtn.addEventListener('click', function () { interval = 'monthly'; render(); });
    yearlyBtn.addEventListener('click', function () { interval = 'yearly'; render(); });

    subscribe.addEventListener('click', function () {
      if (status && status.plan === 'pro') { window.location.href = '/account'; return; }
      if (status && !status.signed_in) {
        window.location.href = '/account?next=' + encodeURIComponent('/pricing?interval=' + interval);
        return;
      }
      busy(subscribe, true);
      say(msg, 'info', 'Taking you to secure checkout…');
      request('POST', '/api/billing/checkout', { interval: interval }).then(function (r) {
        if (r.ok && r.data.url) { window.location.href = r.data.url; return; }
        busy(subscribe, false);
        if (r.data.reason === 'signed_out') {
          window.location.href = '/account?next=' + encodeURIComponent('/pricing?interval=' + interval);
          return;
        }
        if (r.data.reason === 'already_subscribed') {
          say(msg, 'info', errorText(r));
          return;
        }
        say(msg, 'err', errorText(r));
      });
    });

    render();
    request('GET', '/api/billing/status').then(function (r) {
      if (!r.ok) { say(msg, 'err', errorText(r)); return; }
      status = r.data;
      render();
    });
  }

  /* ---------------- Account ---------------- */

  function accountPage() {
    if (!ACCOUNTS_ON) return;
    var msg = $('accountMsg');
    var q = params();
    var next = safeNext(q.get('next'));

    if (q.get('checkout') === 'success') say(msg, 'ok', 'Thank you! Your payment went through. Pro switches on within a few seconds.');
    if (q.get('deleted') === '1') say(msg, 'ok', 'Your account has been deleted, and any subscription cancelled. Thank you for using Claimifi.biz.');
    if (q.get('email_changed') === '1') say(msg, 'ok', 'Your email address has been changed.');
    var linkError = q.get('error_code');
    if (linkError) say(msg, 'err', linkMessage(linkError));

    /* ----- signed out ----- */

    var tabLogin = $('tabLogin'), tabSignup = $('tabSignup');
    var loginForm = $('loginForm'), signupForm = $('signupForm'), forgotForm = $('forgotForm');

    if (q.get('deleted') === '1') {
      $('signedOutIllustration').hidden = true;
      $('deletedIllustration').hidden = false;
    }

    function show(which) {
      loginForm.hidden = which !== 'login';
      signupForm.hidden = which !== 'signup';
      forgotForm.hidden = which !== 'forgot';
      tabLogin.setAttribute('aria-selected', which === 'login' ? 'true' : 'false');
      tabSignup.setAttribute('aria-selected', which === 'signup' ? 'true' : 'false');
    }

    tabLogin.addEventListener('click', function () { show('login'); });
    tabSignup.addEventListener('click', function () { show('signup'); });
    $('showForgot').addEventListener('click', function () {
      $('forgotEmail').value = $('loginEmail').value;
      show('forgot');
      $('forgotEmail').focus();
    });
    $('backToLogin').addEventListener('click', function () { show('login'); });

    function afterSignIn(isNewAccount) {
      window.location.href = next || (isNewAccount ? '/account?welcome=1' : '/account');
    }

    loginForm.addEventListener('submit', function (e) {
      e.preventDefault();
      var button = loginForm.querySelector('button[type="submit"]');
      var email = $('loginEmail').value.trim();
      $('resendBtn').hidden = true;
      if (!email || !$('loginPassword').value) { say(msg, 'err', 'Enter your email and password.'); return; }
      busy(button, true);
      request('POST', '/api/auth/login', { email: email, password: $('loginPassword').value }).then(function (r) {
        busy(button, false);
        if (r.ok) { afterSignIn(false); return; }
        if (r.data.reason === 'email_not_confirmed') $('resendBtn').hidden = false;
        say(msg, 'err', errorText(r));
      });
    });

    $('resendBtn').addEventListener('click', function () {
      var button = $('resendBtn');
      busy(button, true);
      request('POST', '/api/auth/resend', { email: $('loginEmail').value.trim() }).then(function (r) {
        busy(button, false);
        say(msg, r.ok ? 'ok' : 'err', r.ok ? 'Sent. Check your inbox for the confirmation link.' : errorText(r));
      });
    });

    signupForm.addEventListener('submit', function (e) {
      e.preventDefault();
      var button = signupForm.querySelector('button[type="submit"]');
      var email = $('signupEmail').value.trim();
      var password = $('signupPassword').value;
      if (!email) { say(msg, 'err', 'Enter your email address.'); return; }
      if (password.length < 8) { say(msg, 'err', 'Use at least 8 characters for your password.'); return; }
      busy(button, true);
      request('POST', '/api/auth/signup', { email: email, password: password }).then(function (r) {
        busy(button, false);
        if (!r.ok) { say(msg, 'err', errorText(r)); return; }
        if (r.data.status === 'signed_in') { afterSignIn(true); return; }
        say(msg, 'ok', 'Almost there: we sent a confirmation link to ' + email + '. Open it to finish creating your account.');
        signupForm.reset();
      });
    });

    forgotForm.addEventListener('submit', function (e) {
      e.preventDefault();
      var button = forgotForm.querySelector('button[type="submit"]');
      var email = $('forgotEmail').value.trim();
      if (!email) { say(msg, 'err', 'Enter your email address.'); return; }
      busy(button, true);
      request('POST', '/api/auth/forgot-password', { email: email }).then(function (r) {
        busy(button, false);
        say(msg, r.ok ? 'ok' : 'err', r.ok
          ? 'If there is an account for ' + email + ', a reset link is on its way.'
          : errorText(r));
      });
    });

    /* ----- signed in ----- */

    // Messages scroll into view: on a long account page the result of a click
    // near the bottom would otherwise appear off-screen at the top.
    function notify(kind, text) {
      say(msg, kind, text);
      try { msg.scrollIntoView({ block: 'nearest' }); } catch (e) { /* old browsers */ }
    }

    function renderProfile(user) {
      $('acctName').textContent = user.display_name || '—';
      $('displayName').value = user.display_name || '';
      $('acctEmail').textContent = user.email || '';
      var since = user.created_at ? new Date(user.created_at) : null;
      $('acctSince').textContent = since && !isNaN(since)
        ? since.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' }) : '—';
      var pending = $('pendingEmail');
      pending.hidden = !user.new_email;
      if (user.new_email) {
        pending.textContent = 'Waiting for you to confirm ' + user.new_email + '. Open the link we emailed to finish the change.';
      }
    }

    var plan = null;

    // Welcomes are remembered per account, not per browser: with one shared key,
    // a second person subscribing on the same computer never saw theirs. The
    // key carries a short hash of the email, never the address itself.
    var accountTag = '';

    function tagFor(email) {
      var h = 5381;
      var s = String(email || '').toLowerCase();
      for (var i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) >>> 0;
      return h.toString(36);
    }

    function welcomeKey(base) {
      return accountTag ? base + '-' + accountTag : base;
    }

    function welcomeSeen(key) {
      try { return window.localStorage.getItem(welcomeKey(key)) === '1'; } catch (e) { return false; }
    }

    function rememberWelcome(key) {
      try { window.localStorage.setItem(welcomeKey(key), '1'); } catch (e) { /* storage unavailable */ }
    }

    function showWelcome(cardId, titleId, storageKey) {
      if (welcomeSeen(storageKey)) return;
      var card = $(cardId);
      var title = $(titleId);
      card.hidden = false;
      window.setTimeout(function () {
        try { title.focus({ preventScroll: true }); } catch (e) { title.focus(); }
      }, 0);
    }

    function dismissWelcome(cardId, storageKey) {
      $(cardId).hidden = true;
      rememberWelcome(storageKey);
      $('profileH').focus();
    }

    $('proWelcomeDismiss').addEventListener('click', function () {
      dismissWelcome('proWelcome', 'claimifi-pro-welcome-seen');
    });
    $('newAccountWelcomeDismiss').addEventListener('click', function () {
      dismissWelcome('newAccountWelcome', 'claimifi-welcome-seen');
    });
    $('showProFeatures').addEventListener('click', function (e) {
      e.preventDefault();
      var features = $('proWelcomeFeatures');
      features.hidden = false;
      this.setAttribute('aria-expanded', 'true');
      try { features.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); } catch (err) { /* old browsers */ }
    });

    function renderPlan(b) {
      plan = b;
      var isPro = b.plan === 'pro';
      var ending = isPro && b.cancel_at_period_end;
      var label = isPro ? 'Pro' + (b.interval ? ' (' + b.interval + ')' : '') : 'Free';
      if (isPro && b.status === 'past_due') label += ', payment due';
      if (ending) label += ', cancelled';
      $('acctPlan').textContent = label;
      var when = longDate(b.current_period_end);
      var showPeriod = isPro && !!when;
      $('acctPeriodLabel').hidden = !showPeriod;
      $('acctPeriod').hidden = !showPeriod;
      if (showPeriod) {
        $('acctPeriodLabel').textContent = ending ? 'Pro until' : 'Renews';
        $('acctPeriod').textContent = when;
      }
      $('cancelUntil').textContent = when || 'the end of this billing period';
      var notice = $('cancelNotice');
      notice.hidden = !ending;
      if (ending) {
        notice.textContent = 'Your subscription is cancelled. You keep Pro until ' + (when || 'the end of this period') +
          ', then move to the free plan. You will not be charged again.';
      }
      $('upgradeBtn').hidden = isPro;
      $('cancelBtn').hidden = !isPro || ending;
      $('resumeBtn').hidden = !ending;
      $('cancelConfirm').hidden = true;
      // The portal needs a Stripe customer, which exists once checkout was reached.
      // Once Pro has ended there is no plan to change there, only past invoices;
      // Upgrade is the way back to Pro.
      $('portalBtn').hidden = !(isPro || b.status);
      $('portalBtn').textContent = isPro ? 'Change plan, card or see invoices' : 'See past invoices';
    }

    function loadPlan(attempt) {
      if (!BILLING_ON) return;
      request('GET', '/api/billing/status').then(function (r) {
        if (!r.ok) return;
        renderPlan(r.data);
        if (q.get('checkout') === 'success' && r.data.plan === 'pro') {
          msg.textContent = '';
          $('newAccountWelcome').hidden = true;
          showWelcome('proWelcome', 'proWelcomeTitle', 'claimifi-pro-welcome-seen');
        }
        // Stripe tells the site about a payment by webhook, a moment after the
        // visitor is sent back here. Look again a few times before giving up.
        if (q.get('checkout') === 'success' && r.data.plan !== 'pro' && attempt < 6) {
          setTimeout(function () { loadPlan(attempt + 1); }, 2500);
        } else if (q.get('checkout') === 'success' && r.data.plan !== 'pro') {
          say(msg, 'info', 'Your payment went through, but Pro has not switched on yet. Refresh this page in a minute.');
        }
      });
    }

    /* Profile: update the name */
    $('profileForm').addEventListener('submit', function (e) {
      e.preventDefault();
      var button = $('profileForm').querySelector('button[type="submit"]');
      busy(button, true);
      request('POST', '/api/auth/profile', { display_name: $('displayName').value.trim() }).then(function (r) {
        busy(button, false);
        if (!r.ok) { notify('err', errorText(r)); return; }
        if (r.data.user) renderProfile(r.data.user);
        notify('ok', 'Your name has been saved.');
      });
    });

    /* Profile: change the email (confirmed by link) */
    $('emailForm').addEventListener('submit', function (e) {
      e.preventDefault();
      var button = $('emailForm').querySelector('button[type="submit"]');
      var email = $('newEmail').value.trim();
      if (!email) { notify('err', 'Enter the new email address.'); return; }
      busy(button, true);
      request('POST', '/api/auth/email', { email: email }).then(function (r) {
        busy(button, false);
        if (!r.ok) { notify('err', errorText(r)); return; }
        $('emailForm').reset();
        var pending = $('pendingEmail');
        pending.hidden = false;
        pending.textContent = 'Waiting for you to confirm ' + email + '. Open the link we emailed to finish the change.';
        notify('ok', 'Check ' + email + ' for a confirmation link. Your email changes once you open it.');
      });
    });

    /* Subscription: portal, cancel, resume */
    $('portalBtn').addEventListener('click', function () {
      var button = $('portalBtn');
      busy(button, true);
      request('POST', '/api/billing/portal').then(function (r) {
        if (r.ok && r.data.url) { window.location.href = r.data.url; return; }
        busy(button, false);
        notify('err', errorText(r));
      });
    });

    $('cancelBtn').addEventListener('click', function () {
      $('cancelConfirm').hidden = false;
      $('cancelNo').focus();
    });
    $('cancelNo').addEventListener('click', function () {
      $('cancelConfirm').hidden = true;
      $('cancelBtn').focus();
    });
    $('cancelYes').addEventListener('click', function () {
      var button = $('cancelYes');
      busy(button, true);
      request('POST', '/api/billing/cancel').then(function (r) {
        busy(button, false);
        if (!r.ok) { notify('err', errorText(r)); return; }
        renderPlan(r.data);
        notify('ok', 'Your subscription is cancelled. Pro stays on until the end of the period you paid for.');
      });
    });

    $('resumeBtn').addEventListener('click', function () {
      var button = $('resumeBtn');
      busy(button, true);
      request('POST', '/api/billing/resume').then(function (r) {
        busy(button, false);
        if (!r.ok) { notify('err', errorText(r)); return; }
        renderPlan(r.data);
        notify('ok', 'Welcome back: your Pro plan will renew as normal.');
      });
    });

    /* Password */
    $('passwordForm').addEventListener('submit', function (e) {
      e.preventDefault();
      var button = $('passwordForm').querySelector('button[type="submit"]');
      var password = $('newPassword').value;
      if (password.length < 8) { notify('err', 'Use at least 8 characters for your password.'); return; }
      busy(button, true);
      request('POST', '/api/auth/password', { password: password }).then(function (r) {
        busy(button, false);
        notify(r.ok ? 'ok' : 'err', r.ok ? 'Your password has been changed.' : errorText(r));
        if (r.ok) $('passwordForm').reset();
      });
    });

    $('logoutBtn').addEventListener('click', function () {
      request('POST', '/api/auth/logout').then(function () { window.location.href = '/'; });
    });

    /* Delete the account */
    $('deleteStart').addEventListener('click', function () {
      $('deleteForm').hidden = false;
      $('deleteStart').hidden = true;
      $('deletePassword').focus();
    });
    $('deleteCancel').addEventListener('click', function () {
      $('deleteForm').reset();
      $('deleteForm').hidden = true;
      $('deleteStart').hidden = false;
      $('deleteStart').focus();
    });
    $('deleteForm').addEventListener('submit', function (e) {
      e.preventDefault();
      if ($('deleteConfirm').value.trim() !== 'DELETE') { notify('err', 'Type DELETE in capitals to confirm.'); return; }
      if (!$('deletePassword').value) { notify('err', 'Enter your password to confirm.'); return; }
      var button = $('deleteForm').querySelector('button[type="submit"]');
      busy(button, true);
      request('POST', '/api/auth/delete', { password: $('deletePassword').value }).then(function (r) {
        busy(button, false);
        if (!r.ok) { notify('err', errorText(r)); return; }
        window.location.href = '/account?deleted=1';
      });
    });

    request('GET', '/api/auth/me').then(function (r) {
      if (!r.ok) { say(msg, 'err', errorText(r)); return; }
      var user = r.data.user;
      if (user) {
        // Signed in already, and sent here on the way somewhere: carry on.
        if (next) { window.location.href = next; return; }
        $('accountCard').classList.add('wide');
        $('signedIn').hidden = false;
        accountTag = tagFor(user.email);
        renderProfile(user);
        if (q.get('welcome') === '1') {
          showWelcome('newAccountWelcome', 'newAccountWelcomeTitle', 'claimifi-welcome-seen');
        }
        loadPlan(0);
      } else {
        $('signedOut').hidden = false;
        if (q.get('welcome') === '1') {
          say(msg, 'ok', 'Your email is confirmed. Log in to open your account.');
        }
        if (next && next.indexOf('/pricing') === 0 && !msg.textContent) {
          say(msg, 'info', 'Log in or create an account to subscribe to Pro.');
        }
        if (window.location.hash === '#forgot') show('forgot');
      }
    });
  }

  function linkMessage(code) {
    if (code === 'otp_expired' || code === 'invalid_link') {
      return 'That link has expired or was already used. Log in, or request a new link below.';
    }
    if (code === 'rate_limited') return 'Too many attempts. Wait a minute, then try the link again.';
    return "That link didn't work. Log in, or request a new link below.";
  }

  /* ---------------- Email link landing ---------------- */

  function callbackPage() {
    var msg = $('callbackMsg');
    var hash = {};
    (window.location.hash || '').replace(/^#/, '').split('&').forEach(function (part) {
      var i = part.indexOf('=');
      if (i > 0) {
        try { hash[decodeURIComponent(part.slice(0, i))] = decodeURIComponent(part.slice(i + 1).replace(/\+/g, ' ')); } catch (e) { /* skip */ }
      }
    });
    // The tokens must not sit in the address bar, the history or a bookmark.
    try { history.replaceState(null, '', window.location.pathname); } catch (e) { /* unavailable */ }

    var failed = hash.error_code || params().get('error_code');
    if (failed || !ACCOUNTS_ON) {
      say(msg, 'err', ACCOUNTS_ON ? linkMessage(failed) : "Accounts aren't available yet.");
      $('callbackActions').hidden = false;
      return;
    }
    if (!hash.access_token || !hash.refresh_token) {
      say(msg, 'err', linkMessage('invalid_link'));
      $('callbackActions').hidden = false;
      return;
    }
    request('POST', '/api/auth/session', {
      access_token: hash.access_token,
      refresh_token: hash.refresh_token
    }).then(function (r) {
      if (!r.ok) {
        say(msg, 'err', errorText(r));
        $('callbackActions').hidden = false;
        return;
      }
      window.location.replace(
        hash.type === 'recovery' ? '/reset-password'
          : hash.type === 'email_change' ? '/account?email_changed=1'
          : '/account?welcome=1');
    });
  }

  /* ---------------- Reset password ---------------- */

  function resetPage() {
    var msg = $('resetMsg');
    var form = $('resetForm');
    if (!ACCOUNTS_ON) {
      form.hidden = true;
      say(msg, 'info', "Accounts aren't available yet.");
      return;
    }
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var a = $('resetPassword').value, b = $('resetConfirm').value;
      if (a.length < 8) { say(msg, 'err', 'Use at least 8 characters for your password.'); return; }
      if (a !== b) { say(msg, 'err', "The two passwords don't match."); return; }
      var button = form.querySelector('button[type="submit"]');
      busy(button, true);
      request('POST', '/api/auth/password', { password: a }).then(function (r) {
        busy(button, false);
        if (r.ok) {
          form.hidden = true;
          say(msg, 'ok', 'Your new password is saved. Taking you to your account…');
          setTimeout(function () { window.location.href = '/account'; }, 1500);
          return;
        }
        if (r.data.reason === 'signed_out') {
          say(msg, 'err', 'This reset link has expired. Request a new one from the account page.');
          return;
        }
        say(msg, 'err', errorText(r));
      });
    });
  }

  if (page === 'pricing') pricingPage();
  else if (page === 'account') accountPage();
  else if (page === 'callback') callbackPage();
  else if (page === 'reset') resetPage();
})();
