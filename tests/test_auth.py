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


def jwt(exp_in: int = 3600, **claims) -> str:
    """An unsigned stand-in shaped like a Supabase access token."""

    def segment(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    payload = {"exp": int(time.time()) + exp_in, "sub": USER["id"], "email": USER["email"], **claims}
    return f"{segment({'alg': 'ES256'})}.{segment(payload)}.signature"


def session(refresh: str = "rt-1", exp_in: int = 3600) -> dict:
    return {
        "access_token": jwt(exp_in),
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
    assert not set_cookies(r.headers)

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
    _, c, fake = auth()
    fake.on("GET", "/user", body=USER)
    token = jwt()
    r = c.post("/api/auth/session", json={"access_token": token, "refresh_token": "rt-9"})
    assert r.status_code == 200
    assert fake.last("GET", "/user")["headers"]["Authorization"] == f"Bearer {token}"
    assert written(r.headers, "claimifi_refresh") == "rt-9"


def test_a_forged_link_session_sets_nothing(auth):
    _, c, fake = auth()
    fake.on("GET", "/user", status=403, body={"code": "bad_jwt", "msg": "invalid JWT"})
    r = c.post("/api/auth/session", json={"access_token": jwt(), "refresh_token": "rt-9"})
    assert r.status_code == 401
    assert r.json()["reason"] == "otp_expired"
    assert not set_cookies(r.headers)


def recovered(seconds_ago: int = 60) -> str:
    """An access token from a session a password-reset link opened."""
    return jwt(amr=[{"method": "recovery", "timestamp": int(time.time()) - seconds_ago}])


def test_a_reset_link_session_sets_a_new_password_without_the_old_one(auth):
    _, c, fake = auth()
    fake.on("GET", "/user", body=USER).on("PUT", "/user", body=USER)
    token = recovered()
    r = c.post("/api/auth/password", json={"password": "new correct horse"}, headers=sent(token, "rt-1"))
    assert r.json() == {"status": "updated"}
    call = fake.last("PUT", "/user")
    assert call["json"] == {"password": "new correct horse"}
    assert call["headers"]["Authorization"] == f"Bearer {token}"
    assert not [x for x in fake.calls if x["path"].startswith("/token")]


def test_a_signed_in_password_change_checks_the_current_password(auth):
    _, c, fake = auth()
    fake.on("GET", "/user", body=USER).on("PUT", "/user", body=USER)
    fake.on("POST", "/token?password", body=session())
    r = c.post(
        "/api/auth/password",
        json={"password": "new correct horse", "current_password": "old correct horse"},
        headers=sent(jwt(amr=[{"method": "password", "timestamp": int(time.time())}]), "rt-1"),
    )
    assert r.json() == {"status": "updated"}
    assert fake.last("POST", "/token")["json"] == {"email": USER["email"], "password": "old correct horse"}


@pytest.mark.parametrize("token", [jwt(), recovered(seconds_ago=2 * 3600)])
def test_the_cookie_alone_cannot_change_the_password(auth, token):
    """A session left open on a shared computer must not be enough to take the
    account over - nor, through a new password, to delete it."""
    _, c, fake = auth()
    fake.on("GET", "/user", body=USER).on("PUT", "/user", body=USER)
    r = c.post("/api/auth/password", json={"password": "new correct horse"}, headers=sent(token, "rt-1"))
    assert r.status_code == 400 and r.json()["reason"] == "current_password_required"
    assert not [x for x in fake.calls if x["method"] == "PUT"]


def test_a_wrong_current_password_changes_nothing(auth):
    _, c, fake = auth()
    fake.on("GET", "/user", body=USER).on("PUT", "/user", body=USER)
    fake.on("POST", "/token?password", status=400, body={"code": "invalid_credentials"})
    r = c.post(
        "/api/auth/password",
        json={"password": "new correct horse", "current_password": "guess"},
        headers=sent(jwt(), "rt-1"),
    )
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
    fake.on("POST", "/token?refresh_token", body=session(refresh="rt-2"))
    fake.on("POST", "/token?password", body=session(refresh="rt-3"))
    fake.on("PUT", "/user", status=422, body={"code": "same_password", "msg": "New password should be different."})
    r = c.post(
        "/api/auth/password",
        json={"password": "same old password", "current_password": "same old password"},
        headers=sent(jwt(exp_in=-5), "rt-1"),
    )
    assert r.status_code == 422
    assert r.json()["reason"] == "same_password"
    assert written(r.headers, "claimifi_refresh") == "rt-2"


# --------------------------------------------------------------------------
# token_hash links through /auth/confirm
# --------------------------------------------------------------------------
def test_a_recovery_link_signs_in_and_opens_the_reset_page(auth):
    _, c, fake = auth()
    fake.on("POST", "/verify", body=session())
    r = c.get("/auth/confirm?token_hash=abc123&type=recovery", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/reset-password"
    assert fake.last("POST", "/verify")["json"] == {"type": "recovery", "token_hash": "abc123"}
    assert len(set_cookies(r.headers)) == 2


def test_a_signup_link_opens_the_account_page(auth):
    _, c, fake = auth()
    fake.on("POST", "/verify", body=session())
    r = c.get("/auth/confirm?token_hash=abc123&type=email", follow_redirects=False)
    assert r.headers["location"] == "/account?welcome=1"


@pytest.mark.parametrize(
    "next_value",
    ["//evil.example", "https://evil.example", "/\\evil.example", "javascript:alert(1)", "/ok\nSet-Cookie: x=1"],
)
def test_confirm_never_redirects_off_the_site(auth, next_value):
    _, c, fake = auth()
    fake.on("POST", "/verify", body=session())
    r = c.get(
        "/auth/confirm",
        params={"token_hash": "abc", "type": "email", "next": next_value},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/account?welcome=1"


def test_confirm_follows_a_same_site_next(auth):
    _, c, fake = auth()
    fake.on("POST", "/verify", body=session())
    r = c.get("/auth/confirm?token_hash=abc&type=email&next=/app", follow_redirects=False)
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
