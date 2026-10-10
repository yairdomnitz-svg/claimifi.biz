"""The profile: read, update name and email, cancel or resume the subscription,
and delete the account. Supabase and Stripe are faked at their clients."""

from __future__ import annotations

import pytest

from test_auth import SUPABASE, FakeSupabase, jwt, sent
from test_billing import USER_ID, FakeStripe, PRICES, WHSEC, event, signed, subscription, user


@pytest.fixture
def acct(client):
    def _make(billing=True, **env):
        env = {"SUPABASE_URL": SUPABASE, "SUPABASE_SECRET_KEY": "sb_secret_test", "SITE_URL": "https://claimifi.biz", **env}
        if billing:
            env.update(STRIPE_SECRET_KEY="sk_test_123", STRIPE_WEBHOOK_SECRET=WHSEC)
        module, c = client(**env)
        supa, stripe = FakeSupabase(), FakeStripe()
        module._auth_client = supa
        module._stripe_client = stripe
        stripe.on("GET", "/prices", body=PRICES)
        return module, c, supa, stripe

    return _make


def signed_in():
    return sent(jwt(), "rt-1")


PRO = {"customer_id": "cus_1", "subscription_id": "sub_1", "status": "active", "interval": "monthly",
       "current_period_end": 1_800_000_000, "cancel_at_period_end": False}
# Cancelled, with paid time left: the only Pro plan an account can be deleted with.
CANCELLED_PRO = {**PRO, "cancel_at_period_end": True}


# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------
def test_me_returns_the_profile(acct):
    _, c, supa, _ = acct()
    u = user()
    u["user_metadata"] = {"display_name": "Ada"}
    u["new_email"] = "ada@new.example"
    supa.on("GET", "/user", body=u)
    body = c.get("/api/auth/me", headers=signed_in()).json()
    assert body["user"] == {"email": "ada@example.com", "created_at": u["created_at"],
                            "display_name": "Ada", "new_email": "ada@new.example"}


# --------------------------------------------------------------------------
# Update
# --------------------------------------------------------------------------
def test_the_display_name_is_saved_trimmed_and_bounded(acct):
    _, c, supa, _ = acct()
    supa.on("GET", "/user", body=user())
    saved = user()
    saved["user_metadata"] = {"display_name": "Ada Lovelace"}
    supa.on("PUT", "/user", body=saved)
    r = c.post("/api/auth/profile", json={"display_name": "  Ada\nLovelace\x00 " + "x" * 150}, headers=signed_in())
    assert r.status_code == 200
    sent_name = supa.last("PUT", "/user")["json"]["data"]["display_name"]
    assert "\n" not in sent_name and "\x00" not in sent_name and len(sent_name) <= 80
    assert r.json()["user"]["display_name"] == "Ada Lovelace"


def test_profile_changes_need_a_session(acct):
    _, c, _, _ = acct()
    r = c.post("/api/auth/profile", json={"display_name": "Ada"})
    assert r.status_code == 401


def test_profile_changes_from_another_site_are_refused(acct):
    _, c, supa, _ = acct()
    supa.on("GET", "/user", body=user())
    r = c.post("/api/auth/profile", json={"display_name": "x"},
               headers={**signed_in(), "Origin": "https://evil.example"})
    assert r.status_code == 403


def test_an_email_change_sends_a_confirmation_link(acct):
    _, c, supa, _ = acct()
    supa.on("GET", "/user", body=user())
    supa.on("PUT", "/user", body=user())
    r = c.post("/api/auth/email", json={"email": "ada@new.example"}, headers=signed_in())
    assert r.status_code == 200 and r.json()["status"] == "confirmation_sent"
    call = supa.last("PUT", "/user")
    assert call["json"] == {"email": "ada@new.example"}
    assert call["params"] == {"redirect_to": "https://claimifi.biz/auth/callback"}


def test_the_same_email_is_refused(acct):
    _, c, supa, _ = acct()
    supa.on("GET", "/user", body=user())
    r = c.post("/api/auth/email", json={"email": "ADA@example.com"}, headers=signed_in())
    assert r.status_code == 400 and r.json()["reason"] == "same_email"


def test_an_email_change_link_lands_on_the_account_page(acct):
    _, c, supa, _ = acct()
    supa.on("POST", "/verify", body={"access_token": jwt(), "refresh_token": "rt-9", "expires_in": 3600, "user": user()})
    r = c.get("/auth/confirm?token_hash=abc&type=email_change", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/account?email_changed=1"


# --------------------------------------------------------------------------
# Subscription: cancel and resume
# --------------------------------------------------------------------------
@pytest.mark.parametrize("path,flag", [("/api/billing/cancel", "true"), ("/api/billing/resume", "false")])
def test_cancel_and_resume_set_cancel_at_period_end(acct, path, flag):
    _, c, supa, stripe = acct()
    supa.on("GET", "/user", body=user(PRO))
    after = {**PRO, "cancel_at_period_end": flag == "true"}
    supa.on("GET", f"/admin/users/{USER_ID}", body=user(after))
    supa.on("PUT", f"/admin/users/{USER_ID}", body=user(after))
    sub = subscription(interval="month")
    sub["cancel_at_period_end"] = flag == "true"
    stripe.on("POST", "/subscriptions/sub_1", body=sub)
    stripe.on("GET", "/subscriptions/sub_1", body=sub)

    r = c.post(path, headers=signed_in())
    assert r.status_code == 200, r.text
    assert stripe.last("POST", "/subscriptions/sub_1")["data"] == {"cancel_at_period_end": flag}
    assert r.json()["cancel_at_period_end"] is (flag == "true")
    assert r.json()["plan"] == "pro"  # Pro stays until the period ends
    # Written at once, not left to the webhook.
    assert supa.last("PUT", f"/admin/users/{USER_ID}")["json"]["app_metadata"]["billing"]["cancel_at_period_end"] is (flag == "true")


def test_cancel_without_a_subscription_is_409(acct):
    _, c, supa, stripe = acct()
    supa.on("GET", "/user", body=user())
    r = c.post("/api/billing/cancel", headers=signed_in())
    assert r.status_code == 409 and r.json()["reason"] == "no_subscription"
    assert stripe.calls == []


def test_cancel_needs_a_session(acct):
    _, c, _, _ = acct()
    assert c.post("/api/billing/cancel").status_code == 401


# --------------------------------------------------------------------------
# Delete
# --------------------------------------------------------------------------
def _deletable(acct, billing_state=None, billing=True):
    module, c, supa, stripe = acct(billing=billing)
    supa.on("GET", "/user", body=user(billing_state))
    supa.on("POST", "/token?password", body={"access_token": jwt(), "refresh_token": "rt-2", "user": user()})
    supa.on("DELETE", f"/admin/users/{USER_ID}", body={})
    return module, c, supa, stripe


def test_deleting_the_account_cancels_billing_and_erases_the_user(acct):
    _, c, supa, stripe = _deletable(acct, CANCELLED_PRO)
    stripe.on("DELETE", "/customers/cus_1", body={"id": "cus_1", "deleted": True})
    r = c.post("/api/auth/delete", json={"password": "correct horse"}, headers=signed_in())
    assert r.status_code == 200 and r.json() == {"status": "deleted"}
    assert ("DELETE", "/customers/cus_1") in stripe.paths()
    supa.last("DELETE", f"/admin/users/{USER_ID}")
    assert supa.last("POST", "/token")["json"] == {"email": "ada@example.com", "password": "correct horse"}
    cleared = [h for h in r.headers.get_list("set-cookie") if h.startswith("claimifi_")]
    assert len(cleared) == 2 and all("Max-Age=0" in h or "expires=" in h.lower() for h in cleared)


def test_a_wrong_password_deletes_nothing(acct):
    _, c, supa, stripe = _deletable(acct, CANCELLED_PRO)
    supa.on("POST", "/token?password", status=400, body={"code": "invalid_credentials"})
    r = c.post("/api/auth/delete", json={"password": "nope"}, headers=signed_in())
    assert r.status_code == 403 and r.json()["reason"] == "invalid_credentials"
    assert stripe.calls == []
    assert not [x for x in supa.calls if x["method"] == "DELETE"]


def test_if_billing_cannot_be_stopped_the_account_stays(acct):
    _, c, supa, stripe = _deletable(acct, CANCELLED_PRO)
    stripe.on("DELETE", "/customers/cus_1", status=500, body={"error": {"type": "api_error"}})
    r = c.post("/api/auth/delete", json={"password": "correct horse"}, headers=signed_in())
    assert r.status_code == 502
    assert not [x for x in supa.calls if x["method"] == "DELETE"]


@pytest.mark.parametrize("status", ["active", "trialing", "past_due"])
def test_a_plan_that_still_renews_blocks_deletion(acct, status):
    """Cancelling comes first. Nothing is checked or deleted, not even the password."""
    _, c, supa, stripe = _deletable(acct, {**PRO, "status": status})
    r = c.post("/api/auth/delete", json={"password": "correct horse"}, headers=signed_in())
    assert r.status_code == 409 and r.json()["reason"] == "active_subscription"
    assert stripe.calls == []
    assert not [x for x in supa.calls if x["method"] == "DELETE" or x["path"].startswith("/token")]


def test_a_cancelled_plan_that_has_ended_does_not_block_deletion(acct):
    _, c, supa, stripe = _deletable(acct, {**PRO, "status": "canceled"})
    stripe.on("DELETE", "/customers/cus_1", body={"id": "cus_1", "deleted": True})
    r = c.post("/api/auth/delete", json={"password": "correct horse"}, headers=signed_in())
    assert r.status_code == 200


def test_a_free_user_is_deleted_without_touching_stripe(acct):
    _, c, supa, stripe = _deletable(acct, None)
    r = c.post("/api/auth/delete", json={"password": "correct horse"}, headers=signed_in())
    assert r.status_code == 200
    assert stripe.calls == []


def test_delete_needs_a_password_and_a_session(acct):
    _, c, supa, _ = _deletable(acct, None)
    assert c.post("/api/auth/delete", json={"password": ""}, headers=signed_in()).status_code == 400
    _, c2, _, _ = acct()
    assert c2.post("/api/auth/delete", json={"password": "x"}).status_code == 401


def test_the_webhook_after_a_deletion_is_acknowledged(acct):
    """Deleting the customer cancels its subscription, and Stripe then reports
    it. The user is gone; failing here made Stripe retry for three days."""
    _, c, supa, stripe = acct()
    stripe.on("GET", "/subscriptions/sub_1", body=subscription(status="canceled"))
    supa.on("GET", f"/admin/users/{USER_ID}", status=404, body={"code": "user_not_found"})
    payload, headers = signed(event("customer.subscription.deleted", {"id": "sub_1"}))
    r = c.post("/api/stripe/webhook", content=payload, headers=headers)
    assert r.status_code == 200
