"""Source-level guards over app.js / app.html / styles.css.

There is no JS test runner here, and adding one would mean adding a build step
to a repo that deliberately has none. These are regression locks, not behaviour
tests: they catch a revert of a decision, not a logic error.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
APP_JS = (REPO / "app.js").read_text(encoding="utf-8")
APP_HTML = (REPO / "app.html").read_text(encoding="utf-8")
INDEX_HTML = (REPO / "index.html").read_text(encoding="utf-8")
STYLES = (REPO / "styles.css").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# The page must never invent an analysis
# --------------------------------------------------------------------------
def test_no_fabricated_sample_analysis_remains():
    """A fact-checker rendering invented claims into its results panel is the
    worst failure this product has. The fallback fired on any dropped
    connection and on every 503, including a revoked API key."""
    assert "demoData" not in APP_JS
    assert "The video presents a single chronological sequence" not in APP_JS


def test_network_failure_reports_an_error():
    catch = APP_JS[APP_JS.index(".catch(function (e)") :]
    assert "renderError" in catch
    assert "renderAnalysis" not in catch.split(".then(cleanup")[0]


def test_only_the_no_api_key_503_switches_to_demo_mode():
    """503 is emitted by load balancers and WAFs too. Treating all of them as
    'no key configured' let an infrastructure fault look like a normal state."""
    assert "err.reason === 'no_api_key'" in APP_JS


# --------------------------------------------------------------------------
# Error rendering
# --------------------------------------------------------------------------
def test_pydantic_list_details_are_rendered():
    """FastAPI returns `detail` as a list for anything pydantic rejects, so
    assuming a string turned every length violation into 'error (422)'."""
    assert "function detailText" in APP_JS
    assert "Array.isArray(detail)" in APP_JS


def test_input_has_a_length_cap():
    assert re.search(r'id="videoInput"[^>]*maxlength="\d+"', APP_HTML)


# --------------------------------------------------------------------------
# Client/server agreement on what a video ID is
# --------------------------------------------------------------------------
def test_bare_video_id_is_routed_as_a_url():
    """The server accepts a bare 11-char id as a URL. Sending it as a title
    produced an invented analysis of a meaningless string."""
    assert "function looksLikeVideo" in APP_JS
    assert "(?=[a-zA-Z0-9_-]{11}$)" in APP_JS
    body = APP_JS[APP_JS.index("function run()") :]
    assert "looksLikeVideo(q) ? { url: q } : { title: q }" in body


def test_title_only_results_are_visibly_marked():
    """An analysis that never read the video must not look identical to one
    that did."""
    assert "data.basis === 'title'" in APP_JS
    assert "Title only" in APP_JS
    assert "No transcript was read" in APP_JS


# --------------------------------------------------------------------------
# Accessibility
# --------------------------------------------------------------------------
def test_elapsed_counter_is_not_announced():
    """It sits inside the results panel and ticks once a second for up to three
    minutes — as a live region that is continuous speech over everything else."""
    line = next(l for l in APP_JS.splitlines() if 'id="elapsed"' in l and "div" in l)
    assert 'aria-hidden="true"' in line


def test_results_panel_is_not_a_live_region():
    results = re.search(r'<div id="results"[^>]*>', APP_HTML).group(0)
    assert "aria-live" not in results
    assert 'aria-busy' in results


def test_a_dedicated_status_line_exists():
    assert 'id="srStatus"' in APP_HTML
    assert "function announce" in APP_JS


def test_skip_link_target_is_focusable():
    for html in (APP_HTML, INDEX_HTML):
        assert '<main id="main" tabindex="-1">' in html


def test_sample_output_label_is_not_hidden_from_screen_readers():
    """'Sample analysis' is the only thing marking that block as fabricated, and
    aria-hidden hid it from exactly the users who cannot see the frame."""
    bar = re.search(r'<div class="sample-bar"[^>]*>', INDEX_HTML).group(0)
    assert "aria-hidden" not in bar


def test_input_keeps_a_visible_focus_indicator():
    assert "#videoInput:focus-visible" in STYLES


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------
@pytest.mark.parametrize("selector", [".claim-text", ".claim-why", ".panel-body p"])
def test_long_tokens_wrap_instead_of_being_clipped(selector):
    """.panel clips overflow and body hides horizontal scroll, so an unbroken
    token — a 300-char pasted title needs no model at all — is unreadable and
    unreachable."""
    rule = re.search(re.escape(selector) + r"\s*\{[^}]*\}", STYLES)
    assert rule, f"{selector} rule missing"
    assert "overflow-wrap" in rule.group(0)


def test_every_class_the_script_emits_is_styled():
    """An unstyled class rendered at runtime is a visibly broken results panel,
    and nothing else in this repo would catch it."""
    emitted = set()
    # Only literal class="..." segments; the scan stops at the first quote or
    # concatenation so an interpolated variable name is never mistaken for one.
    for value in re.findall(r"""class="([a-z0-9 _-]*)""", APP_JS):
        emitted.update(value.split())
    for name in re.findall(r"classList\.add\('([^']+)'\)", APP_JS):
        emitted.add(name)
    # Interpolated through the `pill` variable, so they never appear inside a
    # class="..." literal for the scan above to find.
    emitted.update({"busy", "done", "error", "demo"})

    styled = set(re.findall(r"\.([a-zA-Z][\w-]*)", STYLES))
    missing = sorted(c for c in emitted if c not in styled)
    assert not missing, f"unstyled classes rendered at runtime: {missing}"


# --------------------------------------------------------------------------
# Colour and type
# --------------------------------------------------------------------------
def _root_token(name: str) -> str:
    root = re.search(r":root\s*\{([^}]*)\}", STYLES).group(1)
    match = re.search(rf"--{name}:\s*([^;]+);", root)
    assert match, f"--{name} missing from :root"
    return match.group(1).strip()


def test_the_page_background_is_plain_white():
    """The brief is a plain white page. The checkered grid and the glow it
    replaced lived in a fixed body::before layer, so that is what must not
    come back, and the browser chrome should match the page."""
    assert _root_token("bg").lower() in ("#fff", "#ffffff")
    assert not re.search(r"body::(?:before|after)", STYLES)
    body = re.search(r"(?m)^body\s*\{[^}]*\}", STYLES).group(0)
    assert "background: var(--bg);" in body
    for html in (INDEX_HTML, APP_HTML):
        assert '<meta name="theme-color" content="#ffffff">' in html


def test_one_token_sets_the_typeface_everywhere():
    """The complaint was a font that kept changing from one element to the
    next. Every family now resolves through --font, and controls inherit it
    instead of dropping to the browser's own form-control font."""
    families = re.findall(r"font-family:\s*([^;]+);", STYLES)
    assert families, "no font-family declarations found"
    for value in families:
        assert value.startswith("var(--font)") or value == "inherit", value
    for shorthand in re.findall(r"(?<![-\w])font:\s*([^;]+);", STYLES):
        assert shorthand == "inherit", shorthand


def test_both_pages_load_the_family_the_stylesheet_names():
    """Changing the typeface is --font plus the Google Fonts link. Changing one
    without the other renders the system fallback with no error anywhere."""
    family = _root_token("font").strip("'\"")
    for html in (INDEX_HTML, APP_HTML):
        assert f"css2?family={family.replace(' ', '+')}:" in html
    default = re.search(r"var DEFAULT_FONT = '([^']+)'", APP_JS).group(1)
    assert f"'{default}': ['{family}'," in APP_JS, "font preview default is not the live family"


def test_font_preview_only_loads_listed_fonts():
    """?font= comes from the address bar. It may pick an entry off the list,
    never supply the stylesheet URL or the family name itself."""
    assert "Object.prototype.hasOwnProperty.call(FONTS, k)" in APP_JS
    apply = APP_JS[APP_JS.index("function applyFont") : APP_JS.index("function syncFontParam")]
    assert "FONTS[key][1]" in apply and "FONTS[key][0]" in apply
    assert "location" not in apply


# --------------------------------------------------------------------------
# Comments must not describe behaviour the code does not have
# --------------------------------------------------------------------------
def test_deep_link_comment_matches_the_code():
    """The comment claimed ?q= 'runs immediately'; it only prefills — and it
    should, because a link must not be able to spend an API call on load."""
    comment = APP_JS[APP_JS.index("// Deep link:") : APP_JS.index("// Deep link:") + 400]
    assert "does not run" in comment
    tail = APP_JS[APP_JS.index("var q0 = new URLSearchParams") :]
    assert "run()" not in tail


# --------------------------------------------------------------------------
# Regression locks for behaviour verified by running app.js
# --------------------------------------------------------------------------
def test_client_and_server_agree_on_what_a_bare_video_id_is(fresh_main):
    """The two patterns are meant to be identical. When they drift, the page
    sends a title down the transcript path, or an id down the title path, and
    either way the visitor gets a 400 they cannot make sense of."""
    module = fresh_main()
    found = re.search(r"/(\^\(\?=\[a-zA-Z0-9_-\]\{11\}\$\)[^/\n]*)/\.test\(q\)", APP_JS)
    assert found, "bare-id regex not found in app.js"
    client_pattern = re.compile(found.group(1))
    server_pattern = module._VIDEO_ID_PATTERNS[1]

    corpus = [
        "dQw4w9WgXcQ", "a_bcdefghij", "12345678901", "abcdefghi-j", "aBcdefgh-ij",
        "Renaissance", "Charlemagne", "Anglo-Saxon", "Greco-Roman", "post-soviet",
        "Why 1453 ma", "dQw4w9WgXcQx", "", "The Fall of the Roman Empire",
    ]
    for text in corpus:
        assert bool(client_pattern.search(text)) == bool(server_pattern.search(text)), text


def test_a_finished_request_leaves_a_paused_button_disabled():
    """A pause can arrive with the very response that reports it: applyPaused()
    disabled the button, then cleanup() re-enabled it, leaving a button that
    looked live and silently did nothing."""
    cleanup = APP_JS[APP_JS.index("var cleanup = function") : APP_JS.index("var ctrl = new AbortController")]
    assert "btn.disabled = paused" in cleanup
    assert "btn.disabled = false" not in cleanup


def test_the_copied_report_carries_the_title_only_caveat():
    """The badge stays on the page. The copy goes wherever it is pasted, where a
    title-only report read exactly like one that checked the transcript."""
    handler = APP_JS[APP_JS.index("copyBtn.addEventListener") : APP_JS.index("navigator.clipboard.writeText")]
    assert "titleOnly" in handler
    assert "TITLE_ONLY_NOTE" in handler
