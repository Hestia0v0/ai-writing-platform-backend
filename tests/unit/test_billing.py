"""Unit tests for the Stripe sandbox billing integration."""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "../../backend/api_gateway"),
)

from routers import billing


class FakeCursor:
    def __init__(self, row=None):
        self.row = row
        self.query = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, query, params):
        self.query = query
        self.params = params

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, row=None):
        self.cursor_instance = FakeCursor(row)
        self.committed = False
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


class FakeStripeObject:
    """Matches StripeObject's conversion API without providing dict.get()."""

    def __init__(self, data):
        self.data = data

    def to_dict(self):
        return self.data


@pytest.fixture
def stripe_test_config(monkeypatch):
    monkeypatch.setattr(billing, "_STRIPE_SECRET_KEY", "sk_test_example")
    monkeypatch.setattr(billing, "_WEBHOOK_SECRET", "whsec_example")
    monkeypatch.setattr(billing, "_PRICE_BASIC", "price_basic")
    monkeypatch.setattr(billing, "_PRICE_PRO", "price_pro")
    monkeypatch.setattr(
        billing,
        "_PLAN_BY_PRICE",
        {"price_basic": "basic", "price_pro": "pro"},
    )


def test_checkout_body_rejects_unknown_plan():
    with pytest.raises(ValidationError):
        billing.CheckoutBody(plan="enterprise")


def test_sandbox_guard_rejects_live_secret(monkeypatch):
    monkeypatch.setattr(billing, "_STRIPE_SECRET_KEY", "sk_live_example")

    with pytest.raises(HTTPException) as exc_info:
        billing._require_test_configuration()

    assert exc_info.value.status_code == 503


def test_period_end_supports_current_stripe_item_shape():
    subscription = {
        "items": {
            "data": [
                {"current_period_end": 100},
                {"current_period_end": 200},
            ]
        }
    }

    assert billing._subscription_period_end(subscription) == 200


def test_canceled_subscription_revokes_paid_plan(monkeypatch, stripe_test_config):
    connection = FakeConnection()
    monkeypatch.setattr(billing, "get_conn", lambda: connection)
    subscription = {
        "id": "sub_test",
        "customer": "cus_test",
        "status": "canceled",
        "metadata": {"user_id": "user-1"},
        "items": {
            "data": [
                {
                    "price": {"id": "price_pro"},
                    "current_period_end": 2_000_000_000,
                }
            ]
        },
    }

    assert billing._sync_subscription(subscription) is True
    assert connection.committed is True
    assert connection.cursor_instance.params[4] == "free"
    assert connection.cursor_instance.params[5] == "canceled"


def test_checkout_uses_authenticated_user_metadata(monkeypatch, stripe_test_config):
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(url="https://checkout.stripe.test/session")

    monkeypatch.setattr(billing, "_get_billing_record", lambda _user_id: None)
    monkeypatch.setattr(billing.stripe.checkout.Session, "create", fake_create)
    request = SimpleNamespace(
        state=SimpleNamespace(user_id="user-1", email="test@example.com")
    )

    result = asyncio.run(
        billing.create_checkout(billing.CheckoutBody(plan="basic"), request)
    )

    assert result["checkout_url"] == "https://checkout.stripe.test/session"
    assert captured["client_reference_id"] == "user-1"
    assert captured["customer_email"] == "test@example.com"
    assert captured["subscription_data"]["metadata"]["user_id"] == "user-1"
    assert captured["line_items"] == [{"price": "price_basic", "quantity": 1}]


def test_checkout_blocks_a_second_active_subscription(
    monkeypatch,
    stripe_test_config,
):
    monkeypatch.setattr(
        billing,
        "_get_billing_record",
        lambda _user_id: {
            "stripe_customer_id": "cus_test",
            "plan": "basic",
            "status": "active",
        },
    )
    request = SimpleNamespace(
        state=SimpleNamespace(user_id="user-1", email="test@example.com")
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            billing.create_checkout(billing.CheckoutBody(plan="pro"), request)
        )

    assert exc_info.value.status_code == 409


def test_checkout_confirmation_normalizes_stripe_object(
    monkeypatch,
    stripe_test_config,
):
    session = FakeStripeObject(
        {
            "livemode": False,
            "client_reference_id": "user-1",
            "status": "complete",
            "payment_status": "paid",
            "subscription": {
                "id": "sub_test",
                "customer": "cus_test",
                "status": "active",
                "metadata": {"user_id": "user-1"},
                "items": {
                    "data": [
                        {
                            "price": {"id": "price_basic"},
                            "current_period_end": 2_000_000_000,
                        }
                    ]
                },
            },
        }
    )
    synced = {}

    monkeypatch.setattr(
        billing.stripe.checkout.Session,
        "retrieve",
        lambda *_args, **_kwargs: session,
    )
    monkeypatch.setattr(
        billing,
        "_sync_subscription",
        lambda subscription, **kwargs: synced.update(
            subscription=subscription,
            kwargs=kwargs,
        ),
    )
    request = SimpleNamespace(state=SimpleNamespace(user_id="user-1"))

    result = asyncio.run(
        billing.checkout_session_status("cs_test_completed", request)
    )

    assert result == {
        "status": "complete",
        "payment_status": "paid",
        "complete": True,
    }
    assert synced["subscription"]["id"] == "sub_test"
    assert synced["kwargs"]["fallback_user_id"] == "user-1"
