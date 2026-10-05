"""Subscriptions through Stripe: checkout, the portal, and the webhook that grants a plan.

Stripe and Supabase are both stubbed at the clients this app keeps for them, so
each route runs for real - the signed-in check, the same-origin check, the
signature check and what gets written to the user. Only the network hop is replaced.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import httpx
import pytest

from test_auth import SUPABASE, FakeSupabase, jwt, sent

WHSEC = "whsec_test"
USER_ID = "3f1c2a9e-8b7d-4c6e-9f0a-1b2c3d4e5f60"
OTHER_ID = "9a8b7c6d-5e4f-4a3b-2c1d-0e9f8a7b6c5d"
PRICES = {
    "object": "list",
    "data": [
        {"id": "price_m", "lookup_key": "pro_monthly", "unit_amount": 799, "currency": "usd"},
        {"id": "price_y", "lookup_key": "pro_yearly", "unit_amount": 7099, "currency": "usd"},
    ],
}


def user(billing=None) -> dict:
    meta = {"provider": "email", "providers": ["email"]}
    if billing is not None:
        meta["billing"] = billing
    return {"id": USER_ID, "email": "ada@example.com", "created_at": "2026-09-16T10:00:00Z", "app_metadata": meta}


def subscription(sub_id="sub_1", status="active", interval="year", period_end=1_800_000_000, metadata=None):
    """Shaped like a subscription from a current Stripe API version: the period is on the item."""
    return {
        "id": sub_id,
        "object": "subscription",
        "status": status,
        "cancel_at_period_end": False,
        "metadata": {"user_id": USER_ID} if metadata is None else metadata,
        "customer": {"id": "cus_1", "metadata": {"user_id": USER_ID}},
        "items": {"data": [{"current_period_end": period_end, "price": {"id": "price_y", "recurring": {"interval": interval}}}]},
    }


class FakeStripe:
    """Answers from a table keyed by method and path, and records every call."""

    def __init__(self):
        self.calls = []
        self.routes = {}

    def on(self, method, path, status=200, body=None):
        self.routes[(method, path)] = (status, body)
        return self

    async def request(self, method, url, data=None, params=None, headers=None):
        path = url.split("/v1", 1)[1]
        self.calls.append({"method": method, "path": path, "data": data, "params": params, "headers": dict(headers or {})})
        if (method, path) not in self.routes:
            raise AssertionError(f"unexpected Stripe call: {(method, path)}")
        status, body = self.routes[(method, path)]
        if isinstance(body, Exception):
            raise body
        return httpx.Response(status, json=body if body is not None else {})

    def paths(self):
        return [(c["method"], c["path"]) for c in self.calls]

    def last(self, method, path):
        matching = [c for c in self.calls if c["method"] == method and c["path"] == path]
        assert matching, f"Stripe was never asked {method} {path}"
        return matching[-1]


@pytest.fixture
def billing(client):
    """The app with accounts and payments on, Supabase and Stripe both faked."""

    def _make(**env):
        env = {
            "SUPABASE_URL": SUPABASE,
            "SUPABASE_SECRET_KEY": "sb_secret_test",
            "STRIPE_SECRET_KEY": "sk_test_123",
            "STRIPE_WEBHOOK_SECRET": WHSEC,
            "SITE_URL": "https://claimifi.biz",
            **env,
        }
        module, c = client(**env)
        supa, stripe = FakeSupabase(), FakeStripe()
        module._auth_client = supa
        module._stripe_client = stripe
        stripe.on("GET", "/prices", body=PRICES)
        stripe.on("GET", "/checkout/sessions", body={"object": "list", "data": []})
        return module, c, supa, stripe

    return _make


def signed(event: dict, secret: str = WHSEC, at: int | None = None) -> tuple:
    payload = json.dumps(event).encode()
    t = str(int(time.time()) if at is None else at)
    sig = hmac.new(secret.encode(), t.encode() + b"." + payload, hashlib.sha256).hexdigest()
    return payload, {"Stripe-Signature": f"t={t},v1={sig}", "Content-Type": "application/json"}


def event(kind: str, obj: dict) -> dict:
    return {"id": "evt_1", "type": kind, "data": {"object": obj}}


def signed_in():
    return sent(jwt(), "rt-1")


# --------------------------------------------------------------------------
# Off until configured
# --------------------------------------------------------------------------
def test_payments_are_off_without_stripe(client):
    _, c = client(SUPABASE_URL=SUPABASE, SUPABASE_SECRET_KEY="sb_secret_test")
    body = c.get("/api/billing/status").json()
    assert body["enabled"] is False
    assert body["plan"] == "free"
    r = c.post("/api/billing/checkout", json={"interval": "monthly"})
    assert r.status_code == 503
    assert r.json()["reason"] == "billing_unavailable"
    assert c.post("/api/stripe/webhook", content=b"{}").status_code == 404
    assert c.get("/health").json()["billing_configured"] is False


def test_stripe_without_accounts_is_still_off(client):
    _, c = client(STRIPE_SECRET_KEY="sk_test_123", STRIPE_WEBHOOK_SECRET=WHSEC)
    assert c.get("/api/billing/status").json()["enabled"] is False


def test_health_says_payments_are_on(billing):
    _, c, _, _ = billing()
    assert c.get("/health").json()["billing_configured"] is True


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------
def test_status_shows_prices_from_stripe_to_anyone(billing):
    _, c, _, stripe = billing()
    body = c.get("/api/billing/status").json()
    assert body["enabled"] is True and body["signed_in"] is False and body["plan"] == "free"
    assert body["prices"] == {
        "monthly": {"amount": 799, "currency": "usd"},
        "yearly": {"amount": 7099, "currency": "usd"},
    }
    params = stripe.last("GET", "/prices")["params"]
    assert ("lookup_keys[]", "pro_monthly") in params and ("lookup_keys[]", "pro_yearly") in params


def test_prices_are_asked_for_once_then_reused(billing):
    _, c, _, stripe = billing()
    for _ in range(3):
        c.get("/api/billing/status")
    assert stripe.paths().count(("GET", "/prices")) == 1


def test_status_shows_a_subscribers_plan(billing):
    _, c, supa, _ = billing()
    supa.on("GET", "/user", body=user({"status": "active", "interval": "yearly", "customer_id": "cus_1",
                                       "current_period_end": 1_800_000_000, "cancel_at_period_end": False}))
    body = c.get("/api/billing/status", headers=signed_in()).json()
    assert body["signed_in"] is True
    assert body["plan"] == "pro" and body["interval"] == "yearly"
    assert body["current_period_end"] == 1_800_000_000
    assert "customer_id" not in body


def test_status_still_answers_when_stripe_is_down(billing):
    _, c, _, stripe = billing()
    stripe.on("GET", "/prices", body=httpx.ConnectError("down"))
    r = c.get("/api/billing/status")
    assert r.status_code == 200
    assert r.json()["prices"] == {}


# --------------------------------------------------------------------------
# Checkout
# --------------------------------------------------------------------------
def test_checkout_needs_a_signed_in_user(billing):
    _, c, _, stripe = billing()
    r = c.post("/api/billing/checkout", json={"interval": "monthly"})
    assert r.status_code == 401
    assert r.json()["reason"] == "signed_out"
    assert stripe.calls == []


def test_checkout_refuses_an_unknown_interval(billing):
    _, c, _, stripe = billing()
    r = c.post("/api/billing/checkout", json={"interval": "weekly"}, headers=signed_in())
    assert r.status_code == 400
    assert r.json()["reason"] == "invalid_interval"
    assert stripe.calls == []


def test_first_checkout_creates_one_customer_and_a_session(billing):
    _, c, supa, stripe = billing()
    supa.on("GET", "/user", body=user())
    supa.on("PUT", f"/admin/users/{USER_ID}", body=user({"customer_id": "cus_new"}))
    stripe.on("POST", "/customers", body={"id": "cus_new"})
    stripe.on("POST", "/checkout/sessions", body={"id": "cs_1", "url": "https://checkout.stripe.com/c/pay/cs_1"})

    r = c.post("/api/billing/checkout", json={"interval": "yearly"}, headers=signed_in())
    assert r.status_code == 200
    assert r.json() == {"url": "https://checkout.stripe.com/c/pay/cs_1"}

    customer = stripe.last("POST", "/customers")
    assert customer["data"]["metadata[user_id]"] == USER_ID
    assert customer["headers"]["Idempotency-Key"] == f"claimifi-customer-{USER_ID}"
    assert customer["headers"]["Authorization"] == "Bearer sk_test_123"
    assert supa.last("PUT", f"/admin/users/{USER_ID}")["json"] == {"app_metadata": {"billing": {"customer_id": "cus_new"}}}

    data = stripe.last("POST", "/checkout/sessions")["data"]
    assert data["mode"] == "subscription"
    assert data["customer"] == "cus_new"
    assert data["line_items[0][price]"] == "price_y"
    assert data["client_reference_id"] == USER_ID
    assert data["subscription_data[metadata][user_id]"] == USER_ID
    assert data["success_url"] == "https://claimifi.biz/account?checkout=success"
    assert data["cancel_url"] == "https://claimifi.biz/pricing?checkout=cancelled"


def test_a_returning_customer_is_reused(billing):
    _, c, supa, stripe = billing()
    supa.on("GET", "/user", body=user({"customer_id": "cus_old", "status": "canceled"}))
    stripe.on("POST", "/checkout/sessions", body={"url": "https://checkout.stripe.com/c/pay/cs_2"})
    r = c.post("/api/billing/checkout", json={"interval": "monthly"}, headers=signed_in())
    assert r.status_code == 200
    assert ("POST", "/customers") not in stripe.paths()
    data = stripe.last("POST", "/checkout/sessions")["data"]
    assert data["customer"] == "cus_old" and data["line_items[0][price]"] == "price_m"


def test_a_subscriber_cannot_start_a_second_subscription(billing):
    _, c, supa, stripe = billing()
    supa.on("GET", "/user", body=user({"customer_id": "cus_1", "status": "active"}))
    r = c.post("/api/billing/checkout", json={"interval": "monthly"}, headers=signed_in())
    assert r.status_code == 409
    assert r.json()["reason"] == "already_subscribed"
    assert stripe.calls == []


def test_a_missing_price_is_reported_not_guessed(billing):
    _, c, supa, stripe = billing()
    supa.on("GET", "/user", body=user())
    stripe.on("GET", "/prices", body={"data": [PRICES["data"][0]]})
    r = c.post("/api/billing/checkout", json={"interval": "yearly"}, headers=signed_in())
    assert r.status_code == 503
    assert r.json()["reason"] == "price_missing"


def test_stripes_own_error_text_never_reaches_the_visitor(billing):
    _, c, supa, stripe = billing()
    supa.on("GET", "/user", body=user({"customer_id": "cus_1"}))
    stripe.on("POST", "/checkout/sessions", status=400,
              body={"error": {"type": "invalid_request_error", "message": "No such customer: 'cus_1'; a similar object exists in live mode"}})
    r = c.post("/api/billing/checkout", json={"interval": "monthly"}, headers=signed_in())
    assert r.status_code == 502
    assert "cus_1" not in r.text and "live mode" not in r.text


@pytest.mark.parametrize("path", ["/api/billing/checkout", "/api/billing/portal"])
def test_billing_posted_from_another_site_is_refused(billing, path):
    _, c, supa, stripe = billing()
    r = c.post(path, json={"interval": "monthly"}, headers={"Origin": "https://evil.example", **signed_in()})
    assert r.status_code == 403
    assert supa.calls == [] and stripe.calls == []


# --------------------------------------------------------------------------
# Portal
# --------------------------------------------------------------------------
def test_portal_needs_a_customer(billing):
    _, c, supa, stripe = billing()
    supa.on("GET", "/user", body=user())
    r = c.post("/api/billing/portal", headers=signed_in())
    assert r.status_code == 409
    assert r.json()["reason"] == "no_subscription"
    assert stripe.calls == []


def test_portal_opens_for_the_users_own_customer(billing):
    _, c, supa, stripe = billing()
    supa.on("GET", "/user", body=user({"customer_id": "cus_1", "status": "active"}))
    stripe.on("POST", "/billing_portal/sessions", body={"url": "https://billing.stripe.com/p/session/x"})
    r = c.post("/api/billing/portal", headers=signed_in())
    assert r.json() == {"url": "https://billing.stripe.com/p/session/x"}
    data = stripe.last("POST", "/billing_portal/sessions")["data"]
    assert data == {"customer": "cus_1", "return_url": "https://claimifi.biz/account"}


# --------------------------------------------------------------------------
# Webhook
# --------------------------------------------------------------------------
def webhook_ready(supa, stripe, current=None, sub=None):
    stripe.on("GET", "/subscriptions/sub_1", body=sub or subscription())
    supa.on("GET", f"/admin/users/{USER_ID}", body=user(current))
    supa.on("PUT", f"/admin/users/{USER_ID}", body=user())


def test_a_completed_checkout_grants_the_plan(billing):
    _, c, supa, stripe = billing()
    webhook_ready(supa, stripe, current={"customer_id": "cus_1"})
    payload, headers = signed(event("checkout.session.completed",
                                    {"mode": "subscription", "subscription": "sub_1", "client_reference_id": USER_ID}))
    r = c.post("/api/stripe/webhook", content=payload, headers=headers)
    assert r.status_code == 200
    assert stripe.last("GET", "/subscriptions/sub_1")["params"] == [("expand[]", "customer")]
    assert supa.last("PUT", f"/admin/users/{USER_ID}")["json"] == {"app_metadata": {"billing": {
        "customer_id": "cus_1",
        "subscription_id": "sub_1",
        "status": "active",
        "interval": "yearly",
        "current_period_end": 1_800_000_000,
        "cancel_at_period_end": False,
    }}}


def test_the_new_plan_shows_at_once_not_after_the_cache(billing):
    _, c, supa, stripe = billing()
    supa.on("GET", "/user", body=user())
    headers = signed_in()
    assert c.get("/api/billing/status", headers=headers).json()["plan"] == "free"

    webhook_ready(supa, stripe)
    supa.on("GET", "/user", body=user({"status": "active", "customer_id": "cus_1"}))
    payload, sig = signed(event("customer.subscription.created", subscription()))
    assert c.post("/api/stripe/webhook", content=payload, headers=sig).status_code == 200
    assert c.get("/api/billing/status", headers=headers).json()["plan"] == "pro"


def test_the_event_body_is_not_trusted_stripe_is_asked(billing):
    """An event can arrive late or twice. What gets written is the subscription now."""
    _, c, supa, stripe = billing()
    webhook_ready(supa, stripe, sub=subscription(status="canceled"))
    payload, headers = signed(event("customer.subscription.updated", {**subscription(), "status": "active"}))
    c.post("/api/stripe/webhook", content=payload, headers=headers)
    assert supa.last("PUT", f"/admin/users/{USER_ID}")["json"]["app_metadata"]["billing"]["status"] == "canceled"


def test_a_cancelled_subscription_removes_the_plan(billing):
    module, c, supa, stripe = billing()
    webhook_ready(supa, stripe, current={"subscription_id": "sub_1", "status": "active"},
                  sub=subscription(status="canceled"))
    payload, headers = signed(event("customer.subscription.deleted", subscription(status="canceled")))
    assert c.post("/api/stripe/webhook", content=payload, headers=headers).status_code == 200
    written = supa.last("PUT", f"/admin/users/{USER_ID}")["json"]["app_metadata"]["billing"]
    assert module._plan_for(user(written)) == "free"


def test_monthly_billing_is_recorded_as_monthly(billing):
    _, c, supa, stripe = billing()
    webhook_ready(supa, stripe, sub=subscription(interval="month"))
    payload, headers = signed(event("customer.subscription.created", subscription()))
    c.post("/api/stripe/webhook", content=payload, headers=headers)
    assert supa.last("PUT", f"/admin/users/{USER_ID}")["json"]["app_metadata"]["billing"]["interval"] == "monthly"


def test_an_old_subscription_ending_does_not_cancel_a_newer_one(billing):
    _, c, supa, stripe = billing()
    webhook_ready(supa, stripe, current={"subscription_id": "sub_2", "status": "active"},
                  sub=subscription(status="canceled"))
    payload, headers = signed(event("customer.subscription.deleted", subscription(status="canceled")))
    assert c.post("/api/stripe/webhook", content=payload, headers=headers).status_code == 200
    assert not [call for call in supa.calls if call["method"] == "PUT"]


def test_a_subscription_naming_no_user_is_ignored(billing):
    _, c, supa, stripe = billing()
    sub = subscription(metadata={})
    sub["customer"] = {"id": "cus_x", "metadata": {}}
    stripe.on("GET", "/subscriptions/sub_1", body=sub)
    payload, headers = signed(event("customer.subscription.created", sub))
    assert c.post("/api/stripe/webhook", content=payload, headers=headers).status_code == 200
    assert supa.calls == []


def test_a_forged_signature_is_refused(billing):
    _, c, supa, stripe = billing()
    payload, headers = signed(event("customer.subscription.created", subscription()), secret="whsec_wrong")
    r = c.post("/api/stripe/webhook", content=payload, headers=headers)
    assert r.status_code == 400
    assert stripe.calls == [] and supa.calls == []


def test_a_replayed_old_event_is_refused(billing):
    _, c, _, stripe = billing()
    payload, headers = signed(event("customer.subscription.created", subscription()), at=int(time.time()) - 3600)
    assert c.post("/api/stripe/webhook", content=payload, headers=headers).status_code == 400
    assert stripe.calls == []


def test_a_tampered_body_is_refused(billing):
    _, c, _, stripe = billing()
    payload, headers = signed(event("customer.subscription.created", subscription()))
    tampered = payload.replace(USER_ID.encode(), OTHER_ID.encode())
    assert c.post("/api/stripe/webhook", content=tampered, headers=headers).status_code == 400
    assert stripe.calls == []


def test_events_this_site_does_not_use_are_acknowledged(billing):
    _, c, supa, stripe = billing()
    payload, headers = signed(event("invoice.paid", {"id": "in_1"}))
    assert c.post("/api/stripe/webhook", content=payload, headers=headers).status_code == 200
    assert stripe.calls == [] and supa.calls == []


def test_an_outage_mid_webhook_asks_stripe_to_retry(billing):
    _, c, supa, stripe = billing()
    stripe.on("GET", "/subscriptions/sub_1", body=httpx.ConnectError("down"))
    payload, headers = signed(event("customer.subscription.updated", subscription()))
    assert c.post("/api/stripe/webhook", content=payload, headers=headers).status_code == 500


def test_older_api_versions_keep_the_period_on_the_subscription(billing):
    module, _, _, _ = billing()
    sub = subscription()
    sub["items"]["data"][0].pop("current_period_end")
    sub["current_period_end"] = 1_700_000_000
    assert module._subscription_state(sub)["current_period_end"] == 1_700_000_000
