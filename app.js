/* Claimifi.biz — analyzer logic.
   Loaded by both pages, but only /app carries the widget: on the landing page
   everything below the hook check is skipped and only the status badge runs. */
(function () {
  'use strict';

  // The server allows up to ~200s for a Pro analysis plus the transcript fetch.
  // The client budget must exceed it, or real results get discarded moments
  // before they arrive.
  var REQUEST_TIMEOUT_MS = 270000;

  var $ = function (id) { return document.getElementById(id); };
  var input = $('videoInput');
  var results = $('results');
  var btn = $('analyzeBtn');
  var txToggle = $('transcriptToggle');

  // Off by default while the deploy has no residential proxy: YouTube refuses
  // caption requests from cloud hosts, so a link is checked on its title.
  var useTranscript = false;
  try { useTranscript = localStorage.getItem('useTranscript') === '1'; } catch (e) { /* storage blocked */ }
  function syncToggle() {
    if (txToggle) txToggle.setAttribute('aria-checked', useTranscript ? 'true' : 'false');
  }
  syncToggle();
  if (txToggle) {
    txToggle.addEventListener('click', function () {
      useTranscript = !useTranscript;
      syncToggle();
      try { localStorage.setItem('useTranscript', useTranscript ? '1' : '0'); } catch (e) { /* storage blocked */ }
    });
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // The server accepts a bare 11-character id as a URL. Without this the page
  // sent it as a *title* instead, and the user got an analysis of a meaningless
  // string presented as a completed check.
  //
  // The digit/underscore/hyphen requirement matters: "Renaissance",
  // "Reformation" and "Charlemagne" are all exactly 11 letters, and routing
  // those as video ids broke the title-only path for common one-word topics.
  // Hyphenated words are excluded for the same reason: "Anglo-Saxon" and
  // "Greco-Roman" are 11 characters too. Kept identical to
  // _VIDEO_ID_PATTERNS[1] in main.py.
  //
  // Hosts are case-insensitive: "YouTube.com" in a pasted link was sent as a
  // title and billed as a title analysis of the URL string.
  function looksLikeVideo(q) {
    return /youtube\.com|youtu\.be|youtube-nocookie\.com/i.test(q) ||
           /^(?=[a-zA-Z0-9_-]{11}$)(?![A-Za-z][a-z]+(?:-[A-Za-z][a-z]+)+$)[a-zA-Z]*[0-9_-][a-zA-Z0-9_-]*$/.test(q);
  }

  // "Unsupported" contains "supported", so it must be tested first.
  function verdictKey(v) {
    var s = String(v || '').toLowerCase();
    if (s.indexOf('unsupported') > -1) return 'unsupported';
    if (s.indexOf('supported') > -1) return 'supported';
    if (s.indexOf('mixed') > -1) return 'mixed';
    return 'insufficient';
  }

  /* ---------------- Font preview ---------------- */

  // Shows the whole site in a candidate typeface before committing to one: open
  // /?font=inter, then flick through the rest from the switcher that appears.
  // Only names on this list load anything, so the query string can never pick a
  // stylesheet URL of its own. Each entry asks for 400, 700 and 700 italic - the
  // weights the CSS uses - where the family has them.
  var FONTS = {
    'lato': ['Lato', 'Lato:ital,wght@0,400;0,700;1,700'],
    'inter': ['Inter', 'Inter:ital,wght@0,400;0,700;1,700'],
    'plus-jakarta-sans': ['Plus Jakarta Sans', 'Plus+Jakarta+Sans:ital,wght@0,400;0,700;1,700'],
    'dm-sans': ['DM Sans', 'DM+Sans:ital,wght@0,400;0,700;1,700'],
    'manrope': ['Manrope', 'Manrope:wght@400;700'],
    'outfit': ['Outfit', 'Outfit:wght@400;700'],
    'space-grotesk': ['Space Grotesk', 'Space+Grotesk:wght@400;700'],
    'poppins': ['Poppins', 'Poppins:ital,wght@0,400;0,700;1,700'],
    'nunito-sans': ['Nunito Sans', 'Nunito+Sans:ital,wght@0,400;0,700;1,700']
  };
  // The family styles.css names in --font and both pages already load.
  var DEFAULT_FONT = 'lato';
  var FONT_STORE = 'claimifi-font-preview';

  function fontKey(name) {
    var k = String(name || '').trim().toLowerCase().replace(/[\s+_]+/g, '-');
    return Object.prototype.hasOwnProperty.call(FONTS, k) ? k : null;
  }

  // Storage throws outright in some private windows and under strict cookie
  // settings. A preview is a convenience and must never stop the page working.
  function session(fn) {
    try { return fn(window.sessionStorage); } catch (e) { return null; }
  }

  function applyFont(key) {
    var link = $('fontPreviewCss');
    if (key === DEFAULT_FONT) {
      if (link) link.parentNode.removeChild(link);
      document.documentElement.style.removeProperty('--font');
      return;
    }
    if (!link) {
      link = document.createElement('link');
      link.id = 'fontPreviewCss';
      link.rel = 'stylesheet';
      document.head.appendChild(link);
    }
    link.href = 'https://fonts.googleapis.com/css2?family=' + FONTS[key][1] + '&display=swap';
    document.documentElement.style.setProperty('--font', "'" + FONTS[key][0] + "'");
  }

  // Keeps ?font= in the address bar matching the pick, so the link can be sent
  // on as it stands. ?q= and anything else in the query is left alone.
  function syncFontParam(key) {
    try {
      var url = new URL(window.location.href);
      if (key) url.searchParams.set('font', key); else url.searchParams.delete('font');
      history.replaceState(history.state, '', url.pathname + url.search + url.hash);
    } catch (e) { /* URL or history unavailable */ }
  }

  function openFontPreview(current) {
    var box = document.createElement('div');
    box.className = 'font-preview';
    box.setAttribute('role', 'group');
    box.setAttribute('aria-label', 'Font preview');
    box.innerHTML =
      '<label for="fontPreviewSelect">Font preview</label>' +
      '<select id="fontPreviewSelect">' +
        Object.keys(FONTS).map(function (k) {
          return '<option value="' + k + '"' + (k === current ? ' selected' : '') + '>' +
            esc(FONTS[k][0]) + (k === DEFAULT_FONT ? ' (current)' : '') + '</option>';
        }).join('') +
      '</select>' +
      '<button type="button" id="fontPreviewClose" aria-label="Close the font preview">&times;</button>';
    document.body.appendChild(box);

    $('fontPreviewSelect').addEventListener('change', function (e) {
      var key = fontKey(e.target.value) || DEFAULT_FONT;
      applyFont(key);
      session(function (s) { s.setItem(FONT_STORE, key); });
      syncFontParam(key);
    });
    $('fontPreviewClose').addEventListener('click', function () {
      applyFont(DEFAULT_FONT);
      session(function (s) { s.removeItem(FONT_STORE); });
      syncFontParam(null);
      box.parentNode.removeChild(box);
    });
  }

  (function () {
    var param = null;
    try { param = new URLSearchParams(window.location.search).get('font'); } catch (e) { /* unavailable */ }
    var saved = session(function (s) { return s.getItem(FONT_STORE); });
    // Opened by ?font= - an unknown name still opens it, showing what is on
    // offer - or by a pick made earlier in this tab. Otherwise nothing happens.
    if (param === null && !fontKey(saved)) return;
    var key = fontKey(param) || fontKey(saved) || DEFAULT_FONT;
    applyFont(key);
    session(function (s) { s.setItem(FONT_STORE, key); });
    syncFontParam(key);
    openFontPreview(key);
  })();

  /* ---------------- Status badge ---------------- */

  // Short, discrete announcements for screen readers. The results panel is
  // rewritten wholesale on every render and carries a per-second timer, so it
  // is the wrong element to make a live region.
  function announce(text) {
    var el = $('srStatus');
    if (el) el.textContent = text;
  }

  function setBusy(on) {
    if (results) results.setAttribute('aria-busy', on ? 'true' : 'false');
  }

  function setStatus(mode, text) {
    var el = $('status');
    if (!el) return;
    el.setAttribute('data-mode', mode);
    var t = $('statusText');
    if (t) t.textContent = text;
  }

  // Set by the config probe below, and again by any analysis that comes back
  // 503/analysis_disabled - the switch can be thrown while the page is open.
  var paused = false;

  var PAUSED_MSG = 'AI analysis is switched off right now, so nothing can be ' +
    'fact-checked at the moment. No verdicts are being generated — including ' +
    'the examples on this page. Please check back later.';

  // Three states, matching /api/config: a deliberate pause is not the same as
  // an unfinished deploy, and neither is a working service.
  var STATUS_LABELS = {
    live: ['live', 'Live'],
    paused: ['demo', 'Paused'],
    unconfigured: ['demo', 'Not configured']
  };

  fetch('/api/config', { cache: 'no-store' })
    .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(function (cfg) {
      // `status` is newer than `live`; fall back so a stale cached page still works.
      var key = cfg.status || (cfg.live ? 'live' : 'unconfigured');
      var label = STATUS_LABELS[key] || STATUS_LABELS.unconfigured;
      setStatus(label[0], label[1]);
      if (key === 'paused') applyPaused();
    })
    .catch(function () { setStatus('demo', 'Unavailable'); });

  /* ---------------- Plan and account in the header ---------------- */

  var BILLING_ON = document.documentElement.getAttribute('data-billing') === 'on';
  var ACCOUNTS_ON = document.documentElement.getAttribute('data-accounts') === 'on';
  // The visitor's plan as far as this page knows. It only shapes the waiting
  // messages: what an analysis actually gets is decided by the server.
  var currentPlan = 'free';

  function showSignedIn(signedIn) {
    var acct = $('navAccount');
    if (!acct) return;
    acct.textContent = signedIn ? 'Profile' : 'Sign in';
    acct.setAttribute('href', signedIn ? '/profile' : '/login');
  }

  // Only once payments exist: until then there is no plan to show, and the
  // request would be one more for every visitor on every page.
  if (BILLING_ON) {
    fetch('/api/billing/status', { cache: 'no-store', credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (b) {
        currentPlan = b.plan === 'pro' ? 'pro' : 'free';
        var badge = $('navPlan');
        if (badge) badge.hidden = b.plan !== 'pro';
        showSignedIn(!!b.signed_in);
      })
      .catch(function () { /* the header simply keeps its defaults */ });
  } else if (ACCOUNTS_ON) {
    // Accounts can be on before payments are: the header still has to know
    // who is signed in, or it offers "Sign in" to someone who already is.
    fetch('/api/auth/me', { cache: 'no-store', credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (me) { showSignedIn(!!me.user); })
      .catch(function () { /* the header simply keeps its defaults */ });
  }

  if (!input || !results || !btn) return;

  // Say so up front rather than letting someone type a URL, wait, and get an
  // error. The input stays enabled so a pasted link is not lost on re-enable.
  function applyPaused() {
    paused = true;
    if (!results || !btn) return;
    btn.disabled = true;
    Array.prototype.forEach.call(document.querySelectorAll('.chip'), function (c) {
      c.disabled = true;
    });
    shell('demo', 'Paused',
      '<div class="panel-body notice">' +
        '<div class="label">Analysis is paused</div>' +
        '<p>' + esc(PAUSED_MSG) + '</p>' +
      '</div>');
    announce('Analysis is currently switched off.');
  }

  /* ---------------- Rendering ---------------- */

  var CLOSE_ICON =
    '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>';

  // `closable` adds an X that dismisses the panel and hands focus back to the
  // input, ready for the next video.
  function shell(pillClass, pillText, body, extra, closable) {
    results.innerHTML =
      '<div class="panel">' +
        '<div class="panel-head">' +
          '<h2>Analysis</h2>' +
          '<span class="pill ' + pillClass + '">' + esc(pillText) + '</span>' +
          (extra || '') +
          (closable ? '<button class="panel-close" type="button" id="closeBtn" aria-label="Close this analysis">' + CLOSE_ICON + '</button>' : '') +
        '</div>' + body +
      '</div>';
    var close = $('closeBtn');
    if (close) {
      close.addEventListener('click', function () {
        results.innerHTML = '';
        announce('Analysis closed.');
        input.focus();
      });
    }
  }

  // A title run never fetches a transcript, so its first step must not claim
  // one was fetched: the page's whole stance is telling those two apart.
  function renderLoading(titleOnly) {
    var steps = titleOnly
      ? ['Reading the title', 'Identifying the claims such videos make', 'Checking against trusted sources', 'Writing sourced verdicts']
      : ['Fetching the transcript', 'Extracting historical claims', 'Checking against trusted sources', 'Writing sourced verdicts'];
    shell('busy', 'Working',
      '<div class="loading">' +
        '<div class="spinner" role="presentation"></div>' +
        '<div class="steps">' +
          steps.map(function (text, i) {
            return '<div class="step" id="s' + i + '"><span class="box"></span>' + esc(text) + '</div>';
          }).join('') +
        '</div>' +
        '<div class="elapsed" id="elapsed" aria-hidden="true">0s elapsed</div>' +
        '<p class="loading-note" id="slowNote" hidden></p>' +
      '</div>');
  }

  // FastAPI returns `detail` as a string for our own HTTPExceptions, but as a
  // list of error objects for anything pydantic rejects (a title over 300
  // chars, a malformed body). Assuming a string turned every one of those into
  // an opaque "The server returned an error (422)."
  function detailText(detail, status) {
    if (typeof detail === 'string' && detail) return detail;
    if (Array.isArray(detail) && detail.length) {
      var parts = detail.map(function (d) {
        return d && typeof d.msg === 'string' ? d.msg : null;
      }).filter(Boolean);
      if (parts.length) return parts.join('; ') + '.';
    }
    return 'The server returned an error (' + status + ').';
  }

  function renderError(msg, retryable) {
    announce('Analysis failed. ' + msg);
    shell('error', 'Error',
      '<div class="panel-body">' +
        '<div class="label">Could not complete the analysis</div>' +
        '<p style="color:var(--text-2)">' + esc(msg) + '</p>' +
        (retryable ? '<button class="ghost" type="button" id="retryBtn" style="margin-top:16px;margin-left:0">Try again</button>' : '') +
      '</div>', '', true);
    var r = $('retryBtn');
    if (r) r.addEventListener('click', run);
  }

  /* ---------------- Input hint ---------------- */

  var hintEl = $('inputHint');

  function hint(text) {
    if (hintEl) hintEl.textContent = text || '';
    if (text) input.setAttribute('aria-invalid', 'true');
    else input.removeAttribute('aria-invalid');
  }

  input.addEventListener('input', function () { hint(''); });

  // Characters that render as nothing, as the server's _visible_text drops them.
  function visibleLength(q) {
    return q.replace(/[­​-‏‪-‮⁠-⁤﻿]/g, '').trim().length;
  }

  function tagLinks(list) {
    return (Array.isArray(list) ? list : []).map(function (s) {
      return '<a class="tag" href="https://' + esc(s) + '" target="_blank" rel="noopener nofollow">' + esc(s) + '</a>';
    }).join('');
  }

  // Shown on the page and carried into the copied report, which travels
  // without the page's "Title only" badge.
  var TITLE_ONLY_NOTE = 'No transcript was read. This covers the claims a video with this ' +
    'title typically makes, not what this video actually says.';

  function pct(n) {
    var v = parseInt(n, 10);
    return isNaN(v) ? null : Math.max(0, Math.min(100, v));
  }

  function bulletList(cls, items) {
    return '<ul class="' + cls + '">' + items.map(function (t) { return '<li>' + esc(t) + '</li>'; }).join('') + '</ul>';
  }

  function arr(v) { return Array.isArray(v) ? v : []; }

  /* ---------- Pro: per-claim depth ---------- */

  function proClaimExtras(c) {
    var html = '';
    if (c.video_says || c.scholarship_says) {
      html += '<div class="versus">' +
        '<div class="versus-col"><div class="versus-h">The video says</div><p>' + esc(c.video_says || '—') + '</p></div>' +
        '<div class="versus-col scholarship"><div class="versus-h">Scholarship says</div><p>' + esc(c.scholarship_says || '—') + '</p></div>' +
      '</div>';
    }
    var views = arr(c.competing_views);
    if (views.length) html += '<div class="sub-h">Competing views</div>' + bulletList('views', views);
    var dig = arr(c.dig_deeper).filter(function (d) { return d && d.domain && d.search; });
    if (dig.length) {
      html += '<div class="sub-h">Dig deeper</div><ul class="dig">' + dig.map(function (d) {
        return '<li><a href="https://' + esc(d.domain) + '" target="_blank" rel="noopener nofollow">' + esc(d.domain) +
          '</a> — search “' + esc(d.search) + '”</li>';
      }).join('') + '</ul>';
    }
    return html;
  }

  function confidenceHtml(n) {
    var v = pct(n);
    if (v === null) return '';
    return '<span class="confidence"><span>Confidence</span>' +
      '<span class="meter" aria-hidden="true"><span style="width:' + v + '%"></span></span>' +
      '<span class="confidence-n">' + v + '%</span></span>';
  }

  /* ---------- Pro: whole-video sections ---------- */

  var VERDICT_ORDER = [
    ['supported', 'Supported'], ['mixed', 'Mixed'],
    ['unsupported', 'Unsupported'], ['insufficient', 'Insufficient']
  ];

  function proMetricsHtml(m, counts) {
    if (!m) return '';
    var score = pct(m.accuracy_score);
    var conf = pct(m.average_confidence);
    var total = VERDICT_ORDER.reduce(function (s, d) { return s + counts[d[0]]; }, 0);
    var bar = total ? '<div class="verdict-bar" aria-hidden="true">' + VERDICT_ORDER.map(function (d) {
      var w = counts[d[0]] / total * 100;
      return w ? '<span data-v="' + d[0] + '" style="width:' + w.toFixed(1) + '%"></span>' : '';
    }).join('') + '</div>' : '';
    var cats = Object.keys(m.by_category || {}).map(function (k) {
      return '<span class="tag">' + esc(k) + ' · ' + esc(m.by_category[k]) + '</span>';
    }).join('');
    return '<div class="panel-body">' +
      '<div class="label">Metrics</div>' +
      '<div class="metrics">' +
        '<div class="metric score"><div class="metric-v">' + (score === null ? '—' : score + '%') + '</div>' +
          '<div class="metric-k">Accuracy score</div>' +
          '<div class="metric-note">' + (score === null
            ? 'No claim could be judged either way.'
            : 'Supported counts 1, Mixed ½, Unsupported 0, over ' + esc(m.claims_judged) + ' judged claim' + (m.claims_judged === 1 ? '' : 's') + '.') +
          '</div></div>' +
        '<div class="metric"><div class="metric-v">' + (conf === null ? '—' : conf + '%') + '</div><div class="metric-k">Average confidence</div></div>' +
        '<div class="metric"><div class="metric-v">' + esc(m.claims_checked) + '</div><div class="metric-k">Claims checked</div></div>' +
      '</div>' + bar +
      (cats ? '<div class="sub-h">Claims by type</div><div class="cats">' + cats + '</div>' : '') +
    '</div>';
  }

  function findingsHtml(label, cls, items) {
    items = arr(items);
    if (!items.length) return '';
    return '<div class="panel-body"><div class="label">' + esc(label) + '</div>' + bulletList('findings ' + cls, items) + '</div>';
  }

  /* ---------- Free: what Pro adds ---------- */

  var PRO_FEATURES = [
    'Up to 20 claims checked per video',
    'A confidence score on every verdict',
    'What the video says, next to what scholarship says',
    'Competing interpretations historians hold',
    'An accuracy score and verdict metrics',
    'The biggest errors, and what the video leaves out',
    'Where to dig deeper on the trusted sources'
  ];

  function upsellHtml(data, checked) {
    var found = parseInt(data.claims_found, 10);
    var lead = !isNaN(found) && found > checked
      ? 'This video makes about ' + found + ' checkable claims. The free plan checks the ' + checked + ' most significant.'
      : 'The free plan checks up to ' + esc(data.claims_limit || 5) + ' claims per video.';
    return '<div class="panel-body upsell">' +
      '<div class="label">Go deeper with Pro</div>' +
      '<p>' + esc(lead) + ' Pro adds:</p>' +
      bulletList('locked', PRO_FEATURES) +
      (BILLING_ON
        ? '<a class="btn btn-upsell" href="/pricing">See Pro plans</a>'
        : '<p class="muted">Pro is coming soon.</p>') +
    '</div>';
  }

  function renderAnalysis(data) {
    var claims = Array.isArray(data.claims) ? data.claims : [];
    var titleOnly = data.basis === 'title';
    var pro = data.plan === 'pro';

    var counts = { supported: 0, mixed: 0, unsupported: 0, insufficient: 0 };
    claims.forEach(function (c) { counts[verdictKey(c.verdict)]++; });

    var tally = VERDICT_ORDER.map(function (d) {
      return '<div class="tally-item" data-v="' + d[0] + '">' +
               '<div class="n">' + counts[d[0]] + '</div><div class="t">' + d[1] + '</div>' +
             '</div>';
    }).join('');

    var claimsHtml = claims.map(function (c, i) {
      var k = verdictKey(c.verdict);
      var srcs = tagLinks(c.sources);
      return '<div class="claim" data-v="' + k + '">' +
               '<div class="claim-top">' +
                 '<span class="claim-n">' + (i + 1) + '</span>' +
                 '<span class="claim-text">' + esc(c.claim) + '</span>' +
               '</div>' +
               '<div class="claim-meta">' +
                 '<span class="verdict" data-v="' + k + '">' + esc(c.verdict) + '</span>' +
                 (pro && c.category ? '<span class="cat">' + esc(c.category) + '</span>' : '') +
                 (pro ? confidenceHtml(c.confidence) : '') +
               '</div>' +
               '<div class="claim-why">' + esc(c.explanation) + '</div>' +
               (pro ? proClaimExtras(c) : '') +
               (srcs ? '<div class="tags">' + srcs + '</div>' : '') +
             '</div>';
    }).join('');

    var used = tagLinks(data.sources_used);
    // A title-only run never read the video. It has to be visibly different
    // from one that did, or the page presents guesswork as a transcript check.
    var pill = titleOnly ? ['demo', 'Title only'] : ['done', 'Complete'];

    shell(pill[0], pill[1],
      (titleOnly
        ? '<div class="panel-body notice">' +
            '<p>' + esc(TITLE_ONLY_NOTE) + '</p>' +
          '</div>'
        : '') +
      '<div class="panel-body">' +
        '<div class="label">' + (titleOnly ? 'Title' : 'Video') + '</div>' +
        '<p style="font-weight:700">' + esc(data.video_title || 'Unknown') + '</p>' +
        (data.video_id
          ? '<p style="font-size:.82rem;color:var(--text-3);margin-top:4px">ID: ' + esc(data.video_id) + '</p>' : '') +
      '</div>' +
      '<div class="panel-body">' +
        '<div class="label">Verdict breakdown</div><div class="tally">' + tally + '</div>' +
      '</div>' +
      (pro ? proMetricsHtml(data.metrics, counts) : '') +
      '<div class="panel-body">' +
        '<div class="label">' + claims.length + ' claim' + (claims.length === 1 ? '' : 's') + ' checked</div>' +
        (claimsHtml || '<p style="color:var(--text-2)">No distinct claims were extracted from this video.</p>') +
      '</div>' +
      (pro ? findingsHtml('Most significant errors', 'errors', data.key_errors) : '') +
      (pro ? findingsHtml('What the video leaves out', 'gaps', data.omissions) : '') +
      '<div class="panel-body">' +
        '<div class="label">Overall assessment</div>' +
        '<p style="color:var(--text-2)">' + esc(data.overall_assessment) + '</p>' +
      '</div>' +
      (pro ? '' : upsellHtml(data, claims.length)) +
      '<div class="panel-body">' +
        '<div class="label">Sources consulted</div>' +
        '<div class="tags">' + (used || '<span style="color:var(--text-3)">None reported</span>') + '</div>' +
        '<p style="font-size:.8rem;color:var(--text-3);margin-top:16px">' + esc(data.note || '') + '</p>' +
      '</div>',
      (pro ? '<span class="pill pro">Pro</span>' : '') +
      '<button class="ghost" type="button" id="copyBtn">' +
        '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
        '<rect x="9" y="9" width="13" height="13" rx="2"/>' +
        '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>' +
        '<span id="copyLabel">Copy report</span></button>',
      true);

    var copyBtn = $('copyBtn');
    if (copyBtn) {
      copyBtn.addEventListener('click', function () {
        var lines = ['Claimifi.biz — ' + (data.video_title || '')];
        // Without this a pasted title-only report reads exactly like one that
        // checked the transcript: the badge that says otherwise stays on the page.
        if (titleOnly) lines.push('Title only: ' + TITLE_ONLY_NOTE);
        if (pro && data.metrics) {
          var m = data.metrics;
          lines.push('Pro analysis. Accuracy score: ' + (pct(m.accuracy_score) === null ? 'n/a' : pct(m.accuracy_score) + '%') +
            '; average confidence: ' + (pct(m.average_confidence) === null ? 'n/a' : pct(m.average_confidence) + '%') + '.');
        }
        lines.push('');
        claims.forEach(function (c, i) {
          lines.push((i + 1) + '. ' + c.claim);
          lines.push('   Verdict: ' + c.verdict + (pro && pct(c.confidence) !== null ? ' (confidence ' + pct(c.confidence) + '%)' : ''));
          lines.push('   ' + c.explanation);
          if (pro) {
            if (c.video_says) lines.push('   The video says: ' + c.video_says);
            if (c.scholarship_says) lines.push('   Scholarship says: ' + c.scholarship_says);
            arr(c.competing_views).forEach(function (v) { lines.push('   Competing view: ' + v); });
            arr(c.dig_deeper).forEach(function (d) { lines.push('   Dig deeper: ' + d.domain + ' — search "' + d.search + '"'); });
          }
          if (c.sources && c.sources.length) lines.push('   Sources: ' + c.sources.join(', '));
          lines.push('');
        });
        if (pro) {
          arr(data.key_errors).forEach(function (t, i) { if (i === 0) lines.push('Most significant errors:'); lines.push(' - ' + t); });
          arr(data.omissions).forEach(function (t, i) { if (i === 0) lines.push('What the video leaves out:'); lines.push(' - ' + t); });
        }
        lines.push('Overall: ' + data.overall_assessment);

        // navigator.clipboard is undefined on any non-HTTPS origin, and the
        // write can be rejected outright. Silently doing nothing reads as a
        // broken button, so both outcomes get a label.
        var flash = function (text) {
          var label = $('copyLabel');
          if (!label) return;
          label.textContent = text;
          setTimeout(function () { label.textContent = 'Copy report'; }, 1800);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(lines.join('\n')).then(
            function () { flash('Copied'); },
            function () { flash('Copy failed'); }
          );
        } else {
          flash('Copy unavailable');
        }
      });
    }
  }

  /* ---------------- Run ---------------- */

  var running = false;

  function run() {
    if (running || paused) return;
    var q = input.value.trim();
    // Said next to the box, not by silently refocusing it. Checked here too so a
    // too-short title never costs a request, though the server checks again.
    if (!q) {
      hint('Paste a YouTube link, or type a video title.');
      input.focus();
      return;
    }
    var asVideo = looksLikeVideo(q);
    if (!asVideo && visibleLength(q) < 3) {
      hint("Type at least 3 characters of the video's title, or paste its YouTube link.");
      input.focus();
      return;
    }
    hint('');

    running = true;
    btn.disabled = true;
    setBusy(true);
    // A Pro analysis checks up to four times the claims in far more depth, and
    // takes minutes rather than one. Saying "about a minute" and then sitting
    // on the last step read as stuck.
    var pro = currentPlan === 'pro';
    announce(pro
      ? 'Analyzing in depth. Pro analyses usually take one to three minutes.'
      : 'Analyzing. This usually takes about a minute.');
    renderLoading(!asVideo || !useTranscript);
    results.scrollIntoView({ block: 'start' });

    // Progress affordances on a rough schedule, not real server milestones.
    // Pro's steps are spread across its longer run.
    var schedule = pro ? [0, 10000, 35000, 70000] : [0, 6000, 14000, 26000];
    var timers = schedule.map(function (ms, i) {
      return setTimeout(function () {
        if (i > 0) { var p = $('s' + (i - 1)); if (p) { p.classList.remove('on'); p.classList.add('ok'); } }
        var el = $('s' + i); if (el) el.classList.add('on');
      }, ms);
    });
    // Past the last step, say it is still going rather than leave it frozen.
    timers.push(setTimeout(function () {
      var note = $('slowNote');
      if (!note) return;
      note.textContent = pro
        ? 'Still working. Pro checks up to 20 claims in depth, which can take two to three minutes.'
        : 'Still working, nearly there.';
      note.hidden = false;
      announce(note.textContent);
    }, pro ? 100000 : 45000));

    var t0 = Date.now();
    var tick = setInterval(function () {
      var el = $('elapsed');
      if (el) el.textContent = Math.round((Date.now() - t0) / 1000) + 's elapsed';
    }, 1000);

    var cleanup = function () {
      running = false;
      // Not simply false: a pause can arrive with this very response, and
      // re-enabling here left a button that looked live and did nothing.
      btn.disabled = paused;
      setBusy(false);
      // Cleared only here, once the body has been read: clearing it when the
      // headers arrived left a stalled body spinning with no limit at all.
      clearTimeout(killer);
      timers.forEach(clearTimeout);
      clearInterval(tick);
    };

    var ctrl = new AbortController();
    var killer = setTimeout(function () { ctrl.abort(); }, REQUEST_TIMEOUT_MS);

    fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(asVideo ? { url: q, transcript: useTranscript } : { title: q }),
      signal: ctrl.signal
    })
    .then(function (res) {
      if (res.ok) {
        return res.json().then(function (d) {
          setStatus('live', 'Live');
          renderAnalysis(d);
          announce((d.basis === 'title'
            ? 'Title-only analysis complete, no transcript was read. '
            : 'Analysis complete. ') + ((d.claims || []).length) + ' claims checked.');
        });
      }
      // A non-JSON body is normal for an error served by an edge proxy rather
      // than by this app, so parse defensively and fall back to the status.
      return res.json().catch(function () { return {}; }).then(function (err) {
        if (res.status === 503 && err.reason === 'analysis_disabled') {
          // Switched off since the page loaded. Fabricating a sample verdict
          // here would be indistinguishable from a real one, on a page whose
          // entire purpose is telling those two apart.
          setStatus('demo', 'Paused');
          applyPaused();
          return;
        }
        if (res.status === 503 && err.reason === 'no_api_key') {
          // The server is up but has no Grok key, so nothing can be analysed.
          // It is still reported as an error: showing sample verdicts here
          // would be indistinguishable from a real result, on a page whose
          // whole purpose is telling those two apart.
          setStatus('demo', 'Not configured');
        }
        // "Try again" only where trying again can work: not on a deploy with
        // no key, and not when the server says the wait is an hour or more
        // (the day's budget is spent).
        var wait = parseInt(res.headers.get('Retry-After') || '0', 10) || 0;
        var retryable = (res.status >= 500 || res.status === 429) &&
          err.reason !== 'no_api_key' && wait < 3600;
        renderError(detailText(err.detail, res.status), retryable);
        // Anything else in the 4xx range is about what was typed, so the box
        // gets focus back - and scrolls into view - ready to be corrected.
        if (res.status >= 400 && res.status < 500 && res.status !== 429) input.focus();
      });
    })
    .catch(function (e) {
      if (e && e.name === 'AbortError') {
        renderError('The analysis ran past four and a half minutes and was stopped. Try a shorter video.', true);
      } else {
        // Offline, DNS, a dropped connection, or a reply this page could not
        // read. Every one of them is a failure to report, never a cue to
        // invent an analysis: this is a fact-checker.
        console.error('Analysis request failed:', e);
        renderError('The connection was interrupted before the analysis came back. Please try again.', true);
      }
    })
    .then(cleanup, cleanup);
  }

  btn.addEventListener('click', run);
  input.addEventListener('keydown', function (e) { if (e.key === 'Enter') run(); });

  Array.prototype.forEach.call(document.querySelectorAll('.chip'), function (c) {
    c.addEventListener('click', function () {
      // Guarded: otherwise the box shows one query while the panel below still
      // shows the results of another.
      if (running) return;
      input.value = c.textContent;
      run();
    });
  });


  // Deep link: /app?q=... prefills the box. It deliberately does not run on
  // its own — an analysis costs an API call and a slice of the visitor's
  // quota, and a link should not be able to spend either without a click.
  try {
    var q0 = new URLSearchParams(window.location.search).get('q');
    if (q0) {
      // 300 is the server's title limit; a pasted URL may legitimately be
      // longer, so only the title path gets clipped.
      input.value = looksLikeVideo(q0) ? q0.slice(0, 2000) : q0.slice(0, 300);
      input.focus();
    }
  } catch (e) { /* URLSearchParams unavailable */ }
})();
