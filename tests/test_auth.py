"""Accounts through Supabase Auth: the routes, the cookies, and what reaches Supabase.

Supabase is stubbed at the client this app keeps for it, so each route runs for
real: validation, the same-origin check, the error wording, the cookie handling
and the refresh logic. Only the network hop is replaced.
"""

from __future__ import annotations

import base64
import json
import time

import httpx
import pytest

from conftest import xff

SUPABASE = "https://proj.supabase.co"
USER = {"id": "user-1", "email": "ada@example.com", "created_at": "2026-09-16T10:00:00Z"}
GOOD = {"email": "ada@example.com", "password": "correct horse"}


def jwt(exp_in: int = 3600, amr=None) -> str:
    """An unsigned stand-in shaped like a Supabase access token."""

    def segment(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    payload = {"exp": int(time.time()) + exp_in, "sub": USER["id"], "email": USER["email"],
               "amr": amr if amr is not None else [{"method": "password", "timestamp": int(time.time()) - 60}]}
    return f"{segment({'alg': 'ES256'})}.{segment(payload)}.signature"


def from_link(ago: int = 60) -> list:
    """The `amr` claim of a session an email link opened `ago` seconds back."""
    return [{"method": "otp", "timestamp": int(time.time()) - ago}]


def session(refresh: str = "rt-1", exp_in: int = 3600, amr=None) -> dict:
    return {
        "access_token": jwt(exp_in, amr),
        "token_type": "bearer",
        "expires_in": 3600,
        "refresh_token": refresh,
        "user": USER,
    }


class FakeSupabase:
    """Answers from a table keyed by method and path, and records every call.

    /token is keyed by grant type as well - "/token?password" and
    "/token?refresh_token" - because one path serves both.
    """

    def __init__(self):
        self.calls = []
        self.routes = {}

    def on(self, method, path, status=200, body=None):
        self.routes[(method, path)] = (status, body)
        return self

    async def request(self, method, url, params=None, json=None, headers=None):
        path = url.split("/auth/v1", 1)[1]
        params = dict(params or {})
        self.calls.append({"method": method, "path": path, "params": params, "json": json, "headers": dict(headers or {})})
        key = (method, f"{path}?{params['grant_type']}" if "grant_type" in params else path)
        if key not in self.routes:
            raise AssertionError(f"unexpected Supabase call: {key}")
        status, body = self.routes[key]
        if isinstance(body, Exception):
            raise body
        return httpx.Response(status, json=body) if body is not None else httpx.Response(status)

    def last(self, method, path):
        matching = [c for c in self.calls if c["method"] == method and c["path"] == path]
        assert matching, f"Supabase was never asked {method} {path}"
        return matching[-1]


@pytest.fixture
def auth(client):
    """The app with accounts switched on and Supabase replaced by FakeSupabase."""

    def _make(**env):
        env = {"SUPABASE_URL": SUPABASE, "SUPABASE_SECRET_KEY": "sb_secret_test", **env}
        module, c = client(**env)
        fake = FakeSupabase()
        module._auth_client = fake
        return module, c, fake

    return _make


def set_cookies(headers) -> list:
    return headers.get_list("set-cookie")


def sent(access=None, refresh=None) -> dict:
    """A Cookie header, sent as-is rather than through the client's jar: a jar
    cookie and a server-set one can differ by domain, and then clash by name."""
    parts = [f"claimifi_access={access}" if access else "", f"claimifi_refresh={refresh}" if refresh else ""]
    return {"Cookie": "; ".join(part for part in parts if part)}


def written(headers, name):
    """The value a response set for a cookie, or None."""
    for header in set_cookies(headers):
        if header.startswith(name + "="):
            return header.split(";", 1)[0].split("=", 1)[1]
    return None


def pending_note(module, email="ada@example.com", next_path="") -> str:
    """The cookie this browser gets on asking for an email link, as the server writes it."""
    from types import SimpleNamespace

    from starlette.responses import Response

    response = Response()
    module._set_pending(response, SimpleNamespace(headers={}, url=SimpleNamespace(scheme="http")), email, next_path)
    header = response.headers.getlist("set-cookie")[0]
    return header.split(";", 1)[0].split("=", 1)[1]


# --------------------------------------------------------------------------
# Off until configured
# --------------------------------------------------------------------------
def test_accounts_are_off_without_supabase(client):
    _, c = client()
    assert c.get("/api/auth/me").json() == {"enabled": False, "user": None}
    r = c.post("/api/auth/login", json=GOOD)
    assert r.status_code == 503
    assert r.json()["reason"] == "auth_unavailable"
    r = c.get("/auth/confirm?token_hash=abc&type=recovery", follow_redirects=False)
    assert r.status_code == 404


def test_half_a_configuration_is_still_off(client):
    _, c = client(SUPABASE_URL=SUPABASE)
    assert c.get("/api/auth/me").json()["enabled"] is False


def test_health_says_whether_accounts_are_on(auth, client):
    _, c = client()
    assert c.get("/health").json()["auth_configured"] is False
    _, c2, _ = auth()
    body = c2.get("/health").json()
    assert body["auth_configured"] is True
    assert body["auth_forwards_client_ip"] is True


# --------------------------------------------------------------------------
# Sign up
# --------------------------------------------------------------------------
def test_signup_sends_a_confirmation_email_and_no_session(auth):
    _, c, fake = auth(SITE_URL="https://claimifi.biz")
    fake.on("POST", "/signup", body={"id": "user-1", "email": "ada@example.com"})
    r = c.post("/api/auth/signup", json={"email": "  ada@example.com ", "password": "correct horse"})
    assert r.status_code == 200
    assert r.json() == {"status": "confirmation_sent"}
    assert written(r.headers, "claimifi_access") is None and written(r.headers, "claimifi_refresh") is None
    # Only the note that this browser asked for the link, which names no address.
    note = written(r.headers, "claimifi_pending")
    assert note and "ada" not in base64.urlsafe_b64decode(note.split(".")[0] + "==").decode()

    call = fake.last("POST", "/signup")
    assert call["json"] == GOOD
    assert call["params"] == {"redirect_to": "https://claimifi.biz/auth/callback"}
    assert call["headers"]["apikey"] == "sb_secret_test"
    assert "Authorization" not in call["headers"]


def test_signup_with_confirmation_off_signs_in_at_once(auth):
    _, c, fake = auth()
    fake.on("POST", "/signup", body=session())
    r = c.post("/api/auth/signup", json=GOOD)
    assert r.json()["status"] == "signed_in"
    assert len(set_cookies(r.headers)) == 2


@pytest.mark.parametrize("password", ["short", "x" * 73])
def test_password_length_is_checked_before_supabase(auth, password):
    _, c, fake = auth()
    r = c.post("/api/auth/signup", json={"email": "ada@example.com", "password": password})
    assert r.status_code == 422
    assert r.json()["reason"] == "weak_password"
    assert isinstance(r.json()["detail"], str)
    assert fake.calls == []


@pytest.mark.parametrize("email", ["", "ada", "ada@", "@example.com", "ada@example", "a da@example.com"])
def test_malformed_email_never_reaches_supabase(auth, email):
    _, c, fake = auth()
    r = c.post("/api/auth/signup", json={"email": email, "password": "correct horse"})
    assert r.status_code == 400
    assert fake.calls == []


def test_a_project_password_rule_is_passed_on_in_supabases_words(auth):
    _, c, fake = auth()
    fake.on(
        "POST",
        "/signup",
        status=422,
        body={"code": "weak_password", "msg": "Password should contain at least one digit.", "weak_password": {"reasons": ["characters"]}},
    )
    r = c.post("/api/auth/signup", json=GOOD)
    assert r.status_code == 422
    assert r.json() == {"detail": "Password should contain at least one digit.", "reason": "weak_password"}


# --------------------------------------------------------------------------
# Log in
# --------------------------------------------------------------------------
def test_login_sets_two_httponly_lax_cookies_and_returns_no_tokens(auth):
    _, c, fake = auth()
    s = session()
    fake.on("POST", "/token?password", body=s)
    r = c.post("/api/auth/login", json=GOOD)
    assert r.status_code == 200
    assert r.json() == {"user": {"email": "ada@example.com", "created_at": "2026-09-16T10:00:00Z",
                                 "display_name": "", "new_email": None}}
    assert s["access_token"] not in r.text and "rt-1" not in r.text

    cookies = set_cookies(r.headers)
    assert {h.split("=", 1)[0] for h in cookies} == {"claimifi_access", "claimifi_refresh"}
    for header in cookies:
        assert "HttpOnly" in header and "SameSite=lax" in header and "Path=/" in header
        assert "Secure" not in header  # the test server is plain HTTP
    assert fake.last("POST", "/token")["params"] == {"grant_type": "password"}


def test_cookies_are_secure_behind_https(auth):
    _, c, fake = auth()
    fake.on("POST", "/token?password", body=session())
    r = c.post("/api/auth/login", json=GOOD, headers={"x-forwarded-proto": "https"})
    assert all("Secure" in header for header in set_cookies(r.headers))


@pytest.mark.parametrize(
    "code,status",
    [("invalid_credentials", 400), ("email_not_confirmed", 403), ("over_request_rate_limit", 429)],
)
def test_login_refusals_are_worded_for_visitors(auth, code, status):
    _, c, fake = auth()
    fake.on("POST", "/token?password", status=400, body={"code": code, "msg": "Invalid login credentials"})
    r = c.post("/api/auth/login", json=GOOD)
    assert r.status_code == status
    assert r.json()["reason"] == code
    assert "Invalid login credentials" not in r.json()["detail"]
    assert not set_cookies(r.headers)


def test_the_older_error_shape_is_understood(auth):
    _, c, fake = auth()
    fake.on("POST", "/token?password", status=400, body={"error": "invalid_grant", "error_description": "Invalid login credentials"})
    r = c.post("/api/auth/login", json=GOOD)
    assert r.status_code == 400
    assert r.json()["reason"] == "invalid_credentials"


def test_supabase_down_is_a_502_without_its_internals(auth):
    _, c, fake = auth()
    fake.on("POST", "/token?password", status=500, body={"msg": "pq: connection refused at 10.0.0.7"})
    r = c.post("/api/auth/login", json=GOOD)
    assert r.status_code == 502
    assert "10.0.0.7" not in r.text


def test_supabase_timeout_is_a_504(auth):
    _, c, fake = auth()
    fake.on("POST", "/token?password", body=httpx.ReadTimeout("slow"))
    assert c.post("/api/auth/login", json=GOOD).status_code == 504


# --------------------------------------------------------------------------
# Who is signed in
# --------------------------------------------------------------------------
def test_a_token_is_checked_with_supabase_once_then_reused(auth):
    _, c, fake = auth()
    fake.on("GET", "/user", body=USER)
    token = jwt()
    for _ in range(3):
        assert c.get("/api/auth/me", headers=sent(token, "rt-1")).json()["user"]["email"] == "ada@example.com"
    checks = [call for call in fake.calls if call["path"] == "/user"]
    assert len(checks) == 1
    assert checks[0]["headers"]["Authorization"] == f"Bearer {token}"


def test_me_is_never_cached_by_the_browser(auth):
    _, c, _ = auth()
    assert c.get("/api/auth/me").headers["cache-control"] == "no-store"


def test_a_lapsed_access_token_is_refreshed_and_written_back(auth):
    _, c, fake = auth()
    fresh = session(refresh="rt-2")
    fake.on("POST", "/token?refresh_token", body=fresh)
    r = c.get("/api/auth/me", headers=sent(jwt(exp_in=-10), "rt-1"))
    assert r.json()["user"]["email"] == "ada@example.com"
    assert fake.last("POST", "/token")["json"] == {"refresh_token": "rt-1"}
    assert written(r.headers, "claimifi_refresh") == "rt-2"
    assert written(r.headers, "claimifi_access") == fresh["access_token"]


def test_a_revoked_session_clears_the_cookies(auth):
    _, c, fake = auth()
    fake.on("POST", "/token?refresh_token", status=400, body={"code": "refresh_token_not_found", "msg": "Invalid Refresh Token"})
    r = c.get("/api/auth/me", headers=sent(jwt(exp_in=-10), "rt-1"))
    assert r.json() == {"enabled": True, "user": None}
    cleared = set_cookies(r.headers)
    assert len(cleared) == 2 and all("Max-Age=0" in header for header in cleared)


def test_an_outage_during_refresh_keeps_the_cookies(auth):
    """Being unable to ask is not the same as being told the session is over."""
    _, c, fake = auth()
    fake.on("POST", "/token?refresh_token", status=503, body={"msg": "unavailable"})
    r = c.get("/api/auth/me", headers=sent(jwt(exp_in=-10), "rt-1"))
    assert r.status_code == 502
    assert not set_cookies(r.headers)


def test_a_forged_access_cookie_is_not_a_session(auth):
    _, c, fake = auth()
    fake.on("GET", "/user", status=403, body={"code": "bad_jwt", "msg": "invalid JWT: unable to parse or verify signature"})
    assert c.get("/api/auth/me", headers=sent(jwt())).json()["user"] is None


# --------------------------------------------------------------------------
# Log out
# --------------------------------------------------------------------------
def test_logout_revokes_this_session_only_and_clears_cookies(auth):
    module, c, fake = auth()
    s = session()
    fake.on("POST", "/token?password", body=s).on("POST", "/logout", status=204)
    c.post("/api/auth/login", json=GOOD)

    r = c.post("/api/auth/logout")
    assert r.json() == {"status": "signed_out"}
    call = fake.last("POST", "/logout")
    assert call["params"] == {"scope": "local"}
    assert call["headers"]["Authorization"] == f"Bearer {s['access_token']}"
    assert all("Max-Age=0" in header for header in set_cookies(r.headers))
    assert not module._user_cache


def test_logout_clears_cookies_even_when_supabase_is_unreachable(auth):
    _, c, fake = auth()
    fake.on("POST", "/token?password", body=session()).on("POST", "/logout", body=httpx.ConnectError("down"))
    c.post("/api/auth/login", json=GOOD)
    r = c.post("/api/auth/logout")
    assert r.status_code == 200
    assert all("Max-Age=0" in header for header in set_cookies(r.headers))


# --------------------------------------------------------------------------
# Password reset
# --------------------------------------------------------------------------
def test_forgot_password_emails_a_link_back_to_this_site(auth):
    _, c, fake = auth(SITE_URL="https://claimifi.biz")
    fake.on("POST", "/recover", body={})
    r = c.post("/api/auth/forgot-password", json={"email": "ada@example.com"})
    assert r.json() == {"status": "sent"}
    call = fake.last("POST", "/recover")
    assert call["json"] == {"email": "ada@example.com"}
    assert call["params"] == {"redirect_to": "https://claimifi.biz/auth/callback"}


def test_resend_asks_for_the_signup_confirmation_again(auth):
    _, c, fake = auth()
    fake.on("POST", "/resend", body={})
    c.post("/api/auth/resend", json={"email": "ada@example.com"})
    assert fake.last("POST", "/resend")["json"] == {"type": "signup", "email": "ada@example.com"}


def test_a_link_session_is_verified_before_any_cookie_is_set(auth):
    module, c, fake = auth()
    fake.on("GET", "/user", body=USER)
    token = jwt()
    r = c.post("/api/auth/session", json={"access_token": token, "refresh_token": "rt-9"},
               headers={"Cookie": f"claimifi_pending={pending_note(module)}"})
    assert r.status_code == 200
    assert r.json()["status"] == "signed_in"
    assert fake.last("GET", "/user")["headers"]["Authorization"] == f"Bearer {token}"
    assert written(r.headers, "claimifi_refresh") == "rt-9"
    assert written(r.headers, "claimifi_pending") == '""'  # used up


def test_someone_elses_link_asks_before_signing_in(auth):
    """A crafted /auth/callback#access_token=…&refresh_token=… with the
    attacker's own tokens used to sign the visitor in to the attacker's account
    without a word, and a Pro purchase then went to the attacker."""
    _, c, fake = auth()
    fake.on("GET", "/user", body=USER)
    r = c.post("/api/auth/session", json={"access_token": jwt(), "refresh_token": "rt-9"})
    assert r.json() == {"status": "confirm", "email": "ada@example.com", "signed_in_as": None}
    assert not set_cookies(r.headers)


def test_continue_signs_in_once_the_visitor_agrees(auth):
    _, c, fake = auth()
    fake.on("GET", "/user", body=USER)
    r = c.post("/api/auth/session", json={"access_token": jwt(), "refresh_token": "rt-9", "confirm": True})
    assert r.json()["status"] == "signed_in"
    assert written(r.headers, "claimifi_refresh") == "rt-9"


def test_the_question_names_the_account_already_signed_in(auth):
    _, c, fake = auth()
    attacker = {**USER, "id": "user-9", "email": "mallory@example.com"}
    tokens = {}

    async def request(method, url, params=None, json=None, headers=None):
        bearer = (headers or {}).get("Authorization", "")
        body = USER if bearer == f"Bearer {tokens['own']}" else attacker
        return httpx.Response(200, json=body)

    fake.request = request
    tokens["own"] = jwt()
    r = c.post("/api/auth/session", json={"access_token": jwt(exp_in=1800), "refresh_token": "rt-9"},
               headers=sent(tokens["own"], "rt-1"))
    assert r.json() == {"status": "confirm", "email": "mallory@example.com", "signed_in_as": "ada@example.com"}


def test_a_note_for_another_address_does_not_count(auth):
    module, c, fake = auth()
    fake.on("GET", "/user", body=USER)
    r = c.post("/api/auth/session", json={"access_token": jwt(), "refresh_token": "rt-9"},
               headers={"Cookie": f"claimifi_pending={pending_note(module, 'mallory@example.com')}"})
    assert r.json()["status"] == "confirm"


def test_a_forged_note_does_not_count(auth):
    module, c, fake = auth()
    fake.on("GET", "/user", body=USER)
    payload, _ = pending_note(module).split(".")
    r = c.post("/api/auth/session", json={"access_token": jwt(), "refresh_token": "rt-9"},
               headers={"Cookie": f"claimifi_pending={payload}.{'0' * 32}"})
    assert r.json()["status"] == "confirm"


def test_a_sign_up_link_returns_to_where_the_visitor_was_going(auth):
    module, c, fake = auth()
    fake.on("POST", "/signup", body={"id": "user-1", "email": "ada@example.com"})
    r = c.post("/api/auth/signup", json={**GOOD, "next": "/app?q=Rome"})
    note = written(r.headers, "claimifi_pending")
    fake.on("GET", "/user", body=USER)
    r = c.post("/api/auth/session", json={"access_token": jwt(), "refresh_token": "rt-9", "type": "signup"},
               headers={"Cookie": f"claimifi_pending={note}"})
    assert r.json()["next"] == "/app?q=Rome"


@pytest.mark.parametrize("path,body,supabase", [
    ("/api/auth/resend", {"email": "ada@example.com"}, ("POST", "/resend")),
    ("/api/auth/forgot-password", {"email": "ada@example.com"}, ("POST", "/recover")),
])
def test_asking_for_an_email_link_leaves_the_note(auth, path, body, supabase):
    module, c, fake = auth()
    fake.on(*supabase, body={})
    r = c.post(path, json=body)
    assert written(r.headers, "claimifi_pending") == pending_note(module)


def test_a_forged_link_session_sets_nothing(auth):
    _, c, fake = auth()
    fake.on("GET", "/user", status=403, body={"code": "bad_jwt", "msg": "invalid JWT"})
    r = c.post("/api/auth/session", json={"access_token": jwt(), "refresh_token": "rt-9"})
    assert r.status_code == 401
    assert r.json()["reason"] == "otp_expired"
    assert not set_cookies(r.headers)


def test_a_reset_links_session_sets_a_new_password_with_the_visitors_own_token(auth):
    _, c, fake = auth()
    fake.on("GET", "/user", body=USER).on("PUT", "/user", body=USER)
    token = jwt(amr=from_link())
    r = c.post("/api/auth/password", json={"password": "new correct horse"}, headers=sent(token, "rt-1"))
    assert r.json() == {"status": "updated"}
    call = fake.last("PUT", "/user")
    assert call["json"] == {"password": "new correct horse"}
    assert call["headers"]["Authorization"] == f"Bearer {token}"
    assert not [x for x in fake.calls if x["path"] == "/token"]


@pytest.mark.parametrize("token", [jwt(), jwt(amr=from_link(ago=2 * 3600)), jwt(amr=[])],
                         ids=["password sign-in", "old link", "no amr"])
def test_any_other_session_needs_the_current_password(auth, token):
    _, c, fake = auth()
    fake.on("GET", "/user", body=USER).on("PUT", "/user", body=USER)
    r = c.post("/api/auth/password", json={"password": "new correct horse"}, headers=sent(token, "rt-1"))
    assert r.status_code == 400 and r.json()["reason"] == "current_password_required"
    assert not [x for x in fake.calls if x["method"] == "PUT"]


def test_the_current_password_is_checked_before_the_change(auth):
    _, c, fake = auth()
    fake.on("GET", "/user", body=USER).on("PUT", "/user", body=USER)
    fake.on("POST", "/token?password", body=session(refresh="rt-9"))
    r = c.post("/api/auth/password", json={"password": "new correct horse", "current_password": "correct horse"},
               headers=sent(jwt(), "rt-1"))
    assert r.json() == {"status": "updated"}
    assert fake.last("POST", "/token")["json"] == GOOD
    assert fake.last("PUT", "/user")["json"] == {"password": "new correct horse"}


def test_a_wrong_current_password_changes_nothing(auth):
    _, c, fake = auth()
    fake.on("GET", "/user", body=USER).on("PUT", "/user", body=USER)
    fake.on("POST", "/token?password", status=400, body={"code": "invalid_credentials"})
    r = c.post("/api/auth/password", json={"password": "new correct horse", "current_password": "nope"},
               headers=sent(jwt(), "rt-1"))
    assert r.status_code == 403 and r.json()["reason"] == "invalid_credentials"
    assert not [x for x in fake.calls if x["method"] == "PUT"]


def test_a_new_password_without_a_session_is_refused(auth):
    _, c, fake = auth()
    r = c.post("/api/auth/password", json={"password": "new correct horse"})
    assert r.status_code == 401
    assert fake.calls == []


def test_a_refreshed_session_survives_a_failed_password_change(auth):
    """A refresh token works once. Dropping the renewed pair on an error reply
    would leave the visitor holding a spent token, signed out moments later."""
    _, c, fake = auth()
    fake.on("POST", "/token?refresh_token", body=session(refresh="rt-2", amr=from_link()))
    fake.on("PUT", "/user", status=422, body={"code": "same_password", "msg": "New password should be different."})
    r = c.post("/api/auth/password", json={"password": "same old password"}, headers=sent(jwt(exp_in=-5), "rt-1"))
    assert r.status_code == 422
    assert r.json()["reason"] == "same_password"
    assert written(r.headers, "claimifi_refresh") == "rt-2"


# --------------------------------------------------------------------------
# token_hash links through /auth/confirm
# --------------------------------------------------------------------------
def asked_here(module, email="ada@example.com", next_path="") -> dict:
    return {"Cookie": f"claimifi_pending={pending_note(module, email, next_path)}"}


def test_a_recovery_link_signs_in_and_opens_the_reset_page(auth):
    module, c, fake = auth()
    fake.on("POST", "/verify", body=session())
    r = c.get("/auth/confirm?token_hash=abc123&type=recovery", headers=asked_here(module), follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/reset-password"
    assert fake.last("POST", "/verify")["json"] == {"type": "recovery", "token_hash": "abc123"}
    assert written(r.headers, "claimifi_access") and written(r.headers, "claimifi_refresh") == "rt-1"


def test_a_signup_link_opens_the_account_page(auth):
    module, c, fake = auth()
    fake.on("POST", "/verify", body=session())
    r = c.get("/auth/confirm?token_hash=abc123&type=email", headers=asked_here(module), follow_redirects=False)
    assert r.headers["location"] == "/profile?welcome=1"


def test_a_signup_link_returns_to_where_the_sign_up_started(auth):
    module, c, fake = auth()
    fake.on("POST", "/verify", body=session())
    r = c.get("/auth/confirm?token_hash=abc123&type=email",
              headers=asked_here(module, next_path="/app?q=Rome"), follow_redirects=False)
    assert r.headers["location"] == "/app?q=Rome"


def test_a_link_this_browser_did_not_ask_for_goes_through_the_question(auth):
    """The token_hash form of the same attack: the attacker's own link, opened
    by the victim, used to sign the victim in to the attacker's account."""
    _, c, fake = auth()
    fake.on("POST", "/verify", body=session())
    r = c.get("/auth/confirm?token_hash=abc123&type=recovery", follow_redirects=False)
    location = r.headers["location"]
    assert location.startswith("/auth/callback#access_token=")
    assert "refresh_token=rt-1" in location and "type=recovery" in location
    assert not set_cookies(r.headers)


def test_a_signed_in_visitors_own_link_needs_no_question(auth):
    _, c, fake = auth()
    fake.on("POST", "/verify", body=session()).on("GET", "/user", body=USER)
    r = c.get("/auth/confirm?token_hash=abc123&type=email_change", headers=sent(jwt(), "rt-0"), follow_redirects=False)
    assert r.headers["location"] == "/profile?email_changed=1"
    assert written(r.headers, "claimifi_refresh") == "rt-1"


@pytest.mark.parametrize(
    "next_value",
    ["//evil.example", "https://evil.example", "/\\evil.example", "javascript:alert(1)", "/ok\nSet-Cookie: x=1"],
)
def test_confirm_never_redirects_off_the_site(auth, next_value):
    module, c, fake = auth()
    fake.on("POST", "/verify", body=session())
    r = c.get(
        "/auth/confirm",
        params={"token_hash": "abc", "type": "email", "next": next_value},
        headers=asked_here(module),
        follow_redirects=False,
    )
    assert r.headers["location"] == "/profile?welcome=1"


def test_confirm_follows_a_same_site_next(auth):
    module, c, fake = auth()
    fake.on("POST", "/verify", body=session())
    r = c.get("/auth/confirm?token_hash=abc&type=email&next=/app", headers=asked_here(module), follow_redirects=False)
    assert r.headers["location"] == "/app"


def test_an_expired_link_lands_on_the_callback_with_its_code(auth):
    _, c, fake = auth()
    fake.on("POST", "/verify", status=403, body={"code": "otp_expired", "msg": "Email link is invalid or has expired"})
    r = c.get("/auth/confirm?token_hash=abc&type=recovery", follow_redirects=False)
    assert r.headers["location"] == "/auth/callback?error_code=otp_expired"
    assert not set_cookies(r.headers)


def test_confirm_refuses_unknown_link_types_without_asking_supabase(auth):
    _, c, fake = auth()
    r = c.get("/auth/confirm?token_hash=abc&type=admin", follow_redirects=False)
    assert r.headers["location"] == "/auth/callback?error_code=invalid_link"
    assert fake.calls == []


# --------------------------------------------------------------------------
# What reaches Supabase
# --------------------------------------------------------------------------
def test_the_visitors_own_ip_is_forwarded_with_a_secret_key(auth):
    """Every call leaves from this server. Without the visitor's address,
    Supabase's per-IP limits would be one allowance for the whole site. The
    forged left-hand hop in the header must not be what gets forwarded."""
    _, c, fake = auth()
    fake.on("POST", "/recover", body={})
    c.post("/api/auth/forgot-password", json={"email": "ada@example.com"}, headers=xff("203.0.113.9"))
    assert fake.last("POST", "/recover")["headers"]["Sb-Forwarded-For"] == "203.0.113.9"


def test_no_ip_is_forwarded_with_any_other_key(auth):
    """Supabase honours Sb-Forwarded-For only with a secret key."""
    _, c, fake = auth(SUPABASE_SECRET_KEY="sb_publishable_test")
    fake.on("POST", "/recover", body={})
    c.post("/api/auth/forgot-password", json={"email": "ada@example.com"}, headers=xff("203.0.113.9"))
    assert "Sb-Forwarded-For" not in fake.last("POST", "/recover")["headers"]


# --------------------------------------------------------------------------
# Requests from other sites
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "path,body",
    [
        ("/api/auth/login", GOOD),
        ("/api/auth/signup", GOOD),
        ("/api/auth/forgot-password", {"email": "ada@example.com"}),
        ("/api/auth/password", {"password": "new correct horse"}),
        ("/api/auth/session", {"access_token": "x" * 40, "refresh_token": "rt"}),
        ("/api/auth/logout", None),
    ],
)
def test_account_changes_posted_from_another_site_are_refused(auth, path, body):
    _, c, fake = auth()
    r = c.post(path, json=body, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert r.json()["reason"] == "cross_origin"
    assert fake.calls == []


def test_the_sites_own_origin_is_accepted(auth):
    _, c, fake = auth()
    fake.on("POST", "/recover", body={})
    r = c.post("/api/auth/forgot-password", json={"email": "ada@example.com"}, headers={"Origin": "http://testserver"})
    assert r.status_code == 200


def test_a_plain_form_post_cannot_plant_a_session(auth):
    """A cross-site form can send text/plain but never JSON, so the body must be
    refused rather than parsed."""
    _, c, fake = auth()
    body = json.dumps({"access_token": "x" * 40, "refresh_token": "rt"})
    r = c.post("/api/auth/session", content=body, headers={"Content-Type": "text/plain"})
    assert r.status_code == 422
    assert fake.calls == []


# --------------------------------------------------------------------------
# Account emails are rate-limited
# --------------------------------------------------------------------------
EMAIL_ROUTES = [
    ("/api/auth/forgot-password", "/recover"),
    ("/api/auth/resend", "/resend"),
]


@pytest.mark.parametrize("route,upstream", EMAIL_ROUTES)
def test_one_visitor_cannot_send_unlimited_emails(auth, route, upstream):
    """Supabase's email quota is shared by the whole project: one visitor
    looping on these must not be able to block everyone's resets."""
    from conftest import xff

    _, c, fake = auth(EMAIL_RATE_LIMIT_REQUESTS="3", EMAIL_RATE_LIMIT_PER_ADDRESS="0")
    fake.on("POST", upstream, body={})
    codes = [
        c.post(route, json={"email": f"person{i}@example.com"}, headers=xff("5.6.7.8")).status_code
        for i in range(5)
    ]
    assert codes == [200, 200, 200, 429, 429]
    assert len([x for x in fake.calls if x["path"] == upstream]) == 3
    # Someone else is unaffected.
    assert c.post(route, json={"email": "other@example.com"}, headers=xff("5.6.7.9")).status_code == 200


def test_one_address_cannot_be_flooded_from_many_networks(auth):
    from conftest import xff

    _, c, fake = auth(EMAIL_RATE_LIMIT_REQUESTS="0", EMAIL_RATE_LIMIT_PER_ADDRESS="2")
    fake.on("POST", "/recover", body={})
    codes = [
        c.post("/api/auth/forgot-password", json={"email": " Ada@Example.com "}, headers=xff(f"5.6.7.{i}")).status_code
        for i in range(3)
    ]
    assert codes == [200, 200, 429]


def test_the_email_limit_reads_the_same_for_any_address(auth):
    """Whether the address has an account must not show in the refusal."""
    _, c, fake = auth(EMAIL_RATE_LIMIT_REQUESTS="1", EMAIL_RATE_LIMIT_PER_ADDRESS="0")
    fake.on("POST", "/recover", body={})
    c.post("/api/auth/forgot-password", json={"email": "ada@example.com"})
    r = c.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert r.status_code == 429
    assert r.json()["reason"] == "too_many_emails"
    assert int(r.headers["Retry-After"]) > 0


def test_signup_is_counted_against_the_email_limit(auth):
    _, c, fake = auth(EMAIL_RATE_LIMIT_REQUESTS="1", EMAIL_RATE_LIMIT_PER_ADDRESS="0")
    fake.on("POST", "/signup", body={"id": "user-1", "email": "ada@example.com"})
    assert c.post("/api/auth/signup", json={"email": "ada@example.com", "password": "correct horse"}).status_code == 200
    r = c.post("/api/auth/signup", json={"email": "bob@example.com", "password": "correct horse"})
    assert r.status_code == 429
    assert len([x for x in fake.calls if x["path"] == "/signup"]) == 1


def test_an_invalid_address_is_not_charged(auth):
    _, c, fake = auth(EMAIL_RATE_LIMIT_REQUESTS="1", EMAIL_RATE_LIMIT_PER_ADDRESS="0")
    fake.on("POST", "/recover", body={})
    assert c.post("/api/auth/forgot-password", json={"email": "not an address"}).status_code == 400
    assert c.post("/api/auth/forgot-password", json={"email": "ada@example.com"}).status_code == 200



def test_the_password_ceiling_counts_bytes_not_characters(auth):
    """bcrypt reads 72 bytes. Forty accented letters are 80 of them."""
    _, c, fake = auth()
    fake.on("POST", "/signup", body={"id": "user-1", "email": "ada@example.com"})
    r = c.post("/api/auth/signup", json={"email": "ada@example.com", "password": "é" * 40})
    assert r.status_code == 422 and r.json()["reason"] == "weak_password"
    assert not [x for x in fake.calls if x["path"] == "/signup"]
    assert c.post("/api/auth/signup", json={"email": "ada@example.com", "password": "é" * 36}).status_code == 200


# --------------------------------------------------------------------------
# The analyzer is for signed-in visitors once accounts are on
# --------------------------------------------------------------------------
TITLE = {"title": "The Fall of the Roman Empire"}


@pytest.fixture
def analyzer(auth, monkeypatch):
    """Accounts on, analysis live, Grok stubbed and counted."""
    from unittest.mock import AsyncMock

    def _make(**env):
        module, c, fake = auth(XAI_API_KEY="k", ANALYSIS_ENABLED=1, RATE_LIMIT_REQUESTS=0,
                               GLOBAL_RATE_LIMIT_REQUESTS=0, **env)
        grok = AsyncMock(return_value={"claims": [], "claims_found": 0,
                                       "overall_assessment": "Fine.", "sources_used": []})
        monkeypatch.setattr(module, "call_grok", grok)
        return module, c, fake, grok

    return _make


def test_the_analyzer_stays_open_while_accounts_are_off(client):
    _, c = client()
    r = c.get("/app", follow_redirects=False)
    assert r.status_code == 200
    assert "/app" in c.get("/sitemap.xml").text


def test_a_signed_out_visitor_is_sent_to_log_in_and_back(analyzer):
    _, c, fake, _ = analyzer()
    r = c.get("/app", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=/app"
    assert r.headers["cache-control"] == "no-store"
    r = c.get("/app?q=rome%20fell", follow_redirects=False)
    assert r.headers["location"] == "/login?next=/app%3Fq%3Drome%2520fell"
    assert fake.calls == []
    assert c.head("/app", follow_redirects=False).status_code == 303
    assert "/app" not in c.get("/sitemap.xml").text


def test_a_signed_in_visitor_gets_the_analyzer_unstored(analyzer):
    _, c, fake, _ = analyzer()
    fake.on("GET", "/user", body=USER)
    r = c.get("/app", headers=sent(jwt()), follow_redirects=False)
    assert r.status_code == 200
    assert 'id="navAccount"' in r.text
    assert r.headers["cache-control"] == "no-store"


def test_an_ended_session_is_cleared_and_sent_to_log_in(analyzer):
    _, c, fake, _ = analyzer()
    fake.on("POST", "/token?refresh_token", status=400, body={"code": "refresh_token_not_found"})
    r = c.get("/app", headers=sent(jwt(exp_in=-10), "rt-old"), follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=/app"
    assert written(r.headers, "claimifi_refresh") == '""'


def test_a_refreshed_session_reaches_the_browser_with_the_page(analyzer):
    _, c, fake, _ = analyzer()
    fake.on("POST", "/token?refresh_token", body=session(refresh="rt-2"))
    r = c.get("/app", headers=sent(jwt(exp_in=-10), "rt-1"), follow_redirects=False)
    assert r.status_code == 200
    assert written(r.headers, "claimifi_refresh") == "rt-2"


def test_an_account_service_outage_still_opens_the_page(analyzer):
    _, c, fake, _ = analyzer()
    fake.on("GET", "/user", status=500, body={"msg": "down"})
    r = c.get("/app", headers=sent(jwt()), follow_redirects=False)
    assert r.status_code == 200


def test_analysis_needs_a_signed_in_visitor(analyzer):
    _, c, _, grok = analyzer()
    r = c.post("/api/analyze", json=TITLE)
    assert r.status_code == 401
    assert r.json()["reason"] == "login_required"
    grok.assert_not_called()


def test_analysis_runs_for_a_signed_in_visitor(analyzer):
    _, c, fake, grok = analyzer()
    fake.on("GET", "/user", body=USER)
    r = c.post("/api/analyze", json=TITLE, headers=sent(jwt()))
    assert r.status_code == 200
    grok.assert_called_once()


def test_analysis_with_an_ended_session_is_refused_and_cleared(analyzer):
    _, c, fake, grok = analyzer()
    fake.on("POST", "/token?refresh_token", status=400, body={"code": "refresh_token_not_found"})
    r = c.post("/api/analyze", json=TITLE, headers=sent(jwt(exp_in=-10), "rt-old"))
    assert r.status_code == 401
    assert r.json()["reason"] == "login_required"
    assert written(r.headers, "claimifi_refresh") == '""'
    grok.assert_not_called()


def test_analysis_during_an_account_outage_is_refused_not_waved_through(analyzer):
    _, c, fake, grok = analyzer()
    fake.on("GET", "/user", status=500, body={"msg": "down"})
    r = c.post("/api/analyze", json=TITLE, headers=sent(jwt()))
    assert r.status_code == 502
    grok.assert_not_called()


# --------------------------------------------------------------------------
# Continue with Google
# --------------------------------------------------------------------------
GOOGLE_USER = {**USER, "app_metadata": {"provider": "google", "providers": ["google"]}}


def from_google(ago: int = 60) -> list:
    return [{"method": "oauth", "timestamp": int(time.time()) - ago}]


def google_on(fake, on=True):
    return fake.on("GET", "/settings", body={"external": {"email": True, "google": on}})


def google_attr(page) -> str:
    return page.text.split('data-google="', 1)[1].split('"', 1)[0]


def test_google_is_not_offered_until_supabase_has_it(auth, client):
    _, c = client()
    assert google_attr(c.get("/login")) == "off"
    assert c.get("/auth/google", follow_redirects=False).status_code == 404
    _, c2, fake = auth()
    google_on(fake, on=False)
    assert google_attr(c2.get("/login")) == "off"
    r = c2.get("/auth/google", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login?google_error=unavailable"


def test_the_login_page_arrives_with_the_google_button_in_place(auth):
    """Decided before the page is sent: a button that appeared after a fetch
    pushed the form down the page (CLS 0.39 on the page every visitor now sees)."""
    _, c, fake = auth()
    google_on(fake)
    page = c.get("/login")
    assert google_attr(page) == "on"
    assert '<div id="signedOut">' in page.text  # not hidden until a fetch answers


def test_the_provider_check_is_cached(auth):
    _, c, fake = auth()
    google_on(fake)
    for _ in range(3):
        assert google_attr(c.get("/login")) == "on"
    assert len([x for x in fake.calls if x["path"] == "/settings"]) == 1


def test_an_unreachable_settings_endpoint_offers_no_google(auth):
    _, c, fake = auth()
    fake.on("GET", "/settings", status=500, body={"msg": "down"})
    assert google_attr(c.get("/login")) == "off"


def test_google_sign_in_starts_with_a_pkce_challenge(auth):
    import hashlib
    from urllib.parse import parse_qs, urlsplit

    _, c, fake = auth(SITE_URL="https://claimifi.biz")
    google_on(fake)
    r = c.get("/auth/google?next=/app", follow_redirects=False)
    assert r.status_code == 303
    target = urlsplit(r.headers["location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == f"{SUPABASE}/auth/v1/authorize"
    q = parse_qs(target.query)
    assert q["provider"] == ["google"]
    assert q["redirect_to"] == ["https://claimifi.biz/auth/callback"]
    assert q["code_challenge_method"] == ["s256"]
    verifier = written(r.headers, "claimifi_oauth")
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert q["code_challenge"] == [expected]
    assert written(r.headers, "claimifi_oauth_next").strip('"') == "/app"
    cookie = [h for h in set_cookies(r.headers) if h.startswith("claimifi_oauth=")][0]
    assert "HttpOnly" in cookie and "Path=/auth/callback" in cookie


def test_an_offsite_next_is_dropped(auth):
    _, c, fake = auth()
    google_on(fake)
    r = c.get("/auth/google?next=//evil.example", follow_redirects=False)
    assert written(r.headers, "claimifi_oauth_next") in ('""', "")


def test_the_google_code_becomes_a_session(auth):
    _, c, fake = auth()
    fake.on("POST", "/token?pkce", body={**session(refresh="rt-g"), "user": GOOGLE_USER})
    r = c.get("/auth/callback?code=abc123",
              headers={"Cookie": "claimifi_oauth=verif; claimifi_oauth_next=/app"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/app"
    assert fake.last("POST", "/token")["json"] == {"auth_code": "abc123", "code_verifier": "verif"}
    assert written(r.headers, "claimifi_refresh") == "rt-g"
    assert written(r.headers, "claimifi_oauth") == '""'


def test_without_a_next_google_lands_on_the_profile(auth):
    _, c, fake = auth()
    fake.on("POST", "/token?pkce", body=session())
    r = c.get("/auth/callback?code=abc123", headers={"Cookie": "claimifi_oauth=verif"}, follow_redirects=False)
    assert r.headers["location"] == "/profile"


@pytest.mark.parametrize("query, reason", [
    ("error=access_denied&error_description=denied", "cancelled"),
    ("error=server_error&error_code=signup_disabled", "signup_disabled"),
    ("error=server_error", "failed"),
])
def test_a_google_failure_returns_to_log_in_with_its_reason(auth, query, reason):
    _, c, fake = auth()
    r = c.get(f"/auth/callback?{query}",
              headers={"Cookie": "claimifi_oauth=verif; claimifi_oauth_next=/app"}, follow_redirects=False)
    assert r.headers["location"] == f"/login?google_error={reason}&next=/app"
    assert fake.calls == []


def test_a_rejected_code_sets_no_session(auth):
    _, c, fake = auth()
    fake.on("POST", "/token?pkce", status=400, body={"code": "bad_code_verifier"})
    r = c.get("/auth/callback?code=abc123", headers={"Cookie": "claimifi_oauth=verif"}, follow_redirects=False)
    assert r.headers["location"] == "/login?google_error=failed"
    assert written(r.headers, "claimifi_refresh") is None


def test_the_callback_page_still_serves_email_links(auth):
    _, c, fake = auth()
    # No verifier cookie: this is not a Google sign-in, so the page handles it.
    r = c.get("/auth/callback?code=abc123", follow_redirects=False)
    assert r.status_code == 200
    assert fake.calls == []


def test_me_says_how_a_google_account_confirms_itself(auth):
    _, c, fake = auth()
    fake.on("GET", "/user", body=GOOGLE_USER)
    user = c.get("/api/auth/me", headers=sent(jwt(amr=from_google(ago=7200)))).json()["user"]
    assert user["google"] is True and user["has_password"] is False and user["recent_sign_in"] is False
    _, c2, fake2 = auth()
    fake2.on("GET", "/user", body=GOOGLE_USER)
    user = c2.get("/api/auth/me", headers=sent(jwt(amr=from_google()))).json()["user"]
    assert user["recent_sign_in"] is True


def test_a_password_account_still_has_a_password(auth):
    _, c, fake = auth()
    fake.on("GET", "/user", body={**USER, "app_metadata": {"providers": ["email"]}})
    user = c.get("/api/auth/me", headers=sent(jwt())).json()["user"]
    assert user["has_password"] is True and user["google"] is False


def test_a_recent_google_sign_in_deletes_without_a_password(auth):
    _, c, fake = auth()
    fake.on("GET", "/user", body=GOOGLE_USER)
    fake.on("DELETE", "/admin/users/user-1", body={})
    r = c.post("/api/auth/delete", json={}, headers=sent(jwt(amr=from_google())))
    assert r.status_code == 200
    assert not [x for x in fake.calls if x["path"] == "/token"]


def test_an_old_google_sign_in_still_needs_confirming_to_delete(auth):
    _, c, fake = auth()
    fake.on("GET", "/user", body=GOOGLE_USER)
    r = c.post("/api/auth/delete", json={}, headers=sent(jwt(amr=from_google(ago=7200))))
    assert r.status_code == 400
    assert not [x for x in fake.calls if x["method"] == "DELETE"]


def test_a_recent_google_sign_in_sets_a_password_without_the_old_one(auth):
    _, c, fake = auth()
    fake.on("GET", "/user", body=GOOGLE_USER)
    fake.on("PUT", "/user", body=GOOGLE_USER)
    fake.on("PUT", "/admin/users/user-1", body=GOOGLE_USER)
    r = c.post("/api/auth/password", json={"password": "a new password"}, headers=sent(jwt(amr=from_google())))
    assert r.status_code == 200


def test_a_google_account_that_sets_a_password_is_known_to_have_one(auth):
    """Setting a password adds no "email" provider, so without its own note the
    profile went on asking for a Google re-check instead of the password."""
    _, c, fake = auth()
    fake.on("GET", "/user", body=GOOGLE_USER).on("PUT", "/user", body=GOOGLE_USER)
    fake.on("PUT", "/admin/users/user-1", body=GOOGLE_USER)
    c.post("/api/auth/password", json={"password": "a new password"}, headers=sent(jwt(amr=from_google())))
    assert fake.last("PUT", "/admin/users/user-1")["json"] == {"app_metadata": {"password_set": True}}

    noted = {**GOOGLE_USER, "app_metadata": {**GOOGLE_USER["app_metadata"], "password_set": True}}
    _, c2, fake2 = auth()
    fake2.on("GET", "/user", body=noted)
    assert c2.get("/api/auth/me", headers=sent(jwt())).json()["user"]["has_password"] is True


def test_a_password_account_changing_its_password_writes_no_note(auth):
    _, c, fake = auth()
    fake.on("GET", "/user", body={**USER, "app_metadata": {"providers": ["email"]}})
    fake.on("PUT", "/user", body=USER)
    c.post("/api/auth/password", json={"password": "a new password"}, headers=sent(jwt(amr=from_link())))
    assert not [x for x in fake.calls if x["path"].startswith("/admin/")]


def test_googles_name_fills_in_until_the_profile_sets_one(auth):
    _, c, fake = auth()
    fake.on("GET", "/user", body={**GOOGLE_USER, "user_metadata": {"full_name": "Ada Lovelace"}})
    assert c.get("/api/auth/me", headers=sent(jwt())).json()["user"]["display_name"] == "Ada Lovelace"
    _, c2, fake2 = auth()
    fake2.on("GET", "/user", body={**GOOGLE_USER, "user_metadata": {"full_name": "Ada Lovelace", "display_name": "Ada"}})
    assert c2.get("/api/auth/me", headers=sent(jwt())).json()["user"]["display_name"] == "Ada"


# --------------------------------------------------------------------------
# Re-checking with Google from the profile
# --------------------------------------------------------------------------
OTHER_USER = {**GOOGLE_USER, "id": "user-2", "email": "someone.else@example.com"}


def test_a_signed_in_re_check_asks_google_for_this_account(auth):
    from urllib.parse import parse_qs, urlsplit

    _, c, fake = auth()
    google_on(fake)
    fake.on("GET", "/user", body=GOOGLE_USER)
    r = c.get("/auth/google?next=/profile", headers=sent(jwt()), follow_redirects=False)
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert q["prompt"] == ["select_account"]
    assert q["login_hint"] == ["ada@example.com"]
    assert written(r.headers, "claimifi_oauth_for") == "user-1"


def test_a_first_sign_in_sets_no_account_to_match(auth):
    from urllib.parse import parse_qs, urlsplit

    _, c, fake = auth()
    google_on(fake)
    r = c.get("/auth/google", follow_redirects=False)
    assert "prompt" not in parse_qs(urlsplit(r.headers["location"]).query)
    assert written(r.headers, "claimifi_oauth_for") in (None, '""', "")


def test_a_re_check_that_returns_another_account_keeps_the_current_session(auth):
    """Signed in as A, re-checking with Google account B used to switch the
    profile to B, so "delete my account" deleted B while A survived."""
    _, c, fake = auth()
    fake.on("POST", "/token?pkce", body={**session(refresh="rt-b"), "user": OTHER_USER})
    fake.on("POST", "/logout", status=204)
    r = c.get("/auth/callback?code=abc123", follow_redirects=False,
              headers={"Cookie": "claimifi_oauth=verif; claimifi_oauth_next=/profile; claimifi_oauth_for=user-1"})
    assert r.headers["location"] == "/profile?google_error=other_account"
    assert written(r.headers, "claimifi_refresh") is None
    assert written(r.headers, "claimifi_access") is None
    assert fake.last("POST", "/logout")["params"] == {"scope": "local"}


def test_a_re_check_as_the_same_account_refreshes_the_session(auth):
    _, c, fake = auth()
    fake.on("POST", "/token?pkce", body={**session(refresh="rt-a"), "user": GOOGLE_USER})
    r = c.get("/auth/callback?code=abc123", follow_redirects=False,
              headers={"Cookie": "claimifi_oauth=verif; claimifi_oauth_next=/profile; claimifi_oauth_for=user-1"})
    assert r.headers["location"] == "/profile"
    assert written(r.headers, "claimifi_refresh") == "rt-a"
    assert written(r.headers, "claimifi_oauth_for") == '""'


def test_a_cancelled_re_check_says_so_on_the_profile(auth):
    """The log-in page forwards a signed-in visitor straight on, so a failure
    sent there was never seen."""
    _, c, fake = auth()
    r = c.get("/auth/callback?error=access_denied", follow_redirects=False,
              headers={"Cookie": "claimifi_oauth=verif; claimifi_oauth_for=user-1"})
    assert r.headers["location"] == "/profile?google_error=cancelled"
    assert fake.calls == []


# --------------------------------------------------------------------------
# Supabase refusing this server's own key
# --------------------------------------------------------------------------
INVALID_KEY = {"message": "Invalid API key", "hint": "Double check your Supabase `anon` or `service_role` API key."}


def test_a_refused_api_key_is_an_outage_not_a_sign_out(auth):
    """Read as "session over", a revoked or mistyped SUPABASE_SECRET_KEY signed
    every visitor out and cleared their cookies, instead of reporting a fault."""
    _, c, fake = auth()
    fake.on("GET", "/user", status=401, body=INVALID_KEY)
    r = c.get("/api/auth/me", headers=sent(jwt(), "rt-1"))
    assert r.status_code == 502
    assert r.json()["reason"] == "auth_misconfigured"
    assert not set_cookies(r.headers)


def test_a_deleted_user_is_still_read_as_deleted(fresh_main):
    module = fresh_main()
    exc = module._auth_failure(404, {"code": "user_not_found", "msg": "User not found"})
    assert exc.reason == "user_not_found"
    assert module._auth_failure(403, {"code": "bad_jwt", "msg": "invalid JWT"}).reason == "session_expired"
