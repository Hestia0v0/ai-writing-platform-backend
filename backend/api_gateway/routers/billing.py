import os
from typing import Any, Literal

import stripe
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from db import get_conn

_STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
_PRICE_BASIC = os.getenv("STRIPE_PRICE_BASIC", "")
_PRICE_PRO = os.getenv("STRIPE_PRICE_PRO", "")
_FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")

stripe.api_key = _STRIPE_SECRET_KEY

router = APIRouter()

_ACCESS_STATUSES = {"active", "trialing", "past_due"}
_PLAN_BY_PRICE = {
    _PRICE_BASIC: "basic",
    _PRICE_PRO: "pro",
}


class CheckoutBody(BaseModel):
    plan: Literal["basic", "pro"]


def _stripe_dict(value: Any) -> dict[str, Any]:
    """Normalize StripeObject responses across stripe-python versions."""
    if isinstance(value, dict):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    to_dict_recursive = getattr(value, "to_dict_recursive", None)
    if callable(to_dict_recursive):
        return to_dict_recursive()
    return dict(value)


def _require_test_configuration(*, webhook: bool = False) -> None:
    """This project intentionally permits Stripe sandbox credentials only."""
    if not _STRIPE_SECRET_KEY.startswith(("sk_test_", "rk_test_")):
        raise HTTPException(
            status_code=503,
            detail="Stripe sandbox secret key is not configured.",
        )
    if not _PRICE_BASIC or not _PRICE_PRO or _PRICE_BASIC == _PRICE_PRO:
        raise HTTPException(
            status_code=503,
            detail="Stripe Basic and Pro test Price IDs are not configured correctly.",
        )
    if webhook and not _WEBHOOK_SECRET.startswith("whsec_"):
        raise HTTPException(
            status_code=503,
            detail="Stripe webhook signing secret is not configured.",
        )
    stripe.api_key = _STRIPE_SECRET_KEY


def _get_billing_record(user_id: str) -> dict[str, Any] | None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT stripe_customer_id, stripe_subscription_id, plan, status
                FROM subscriptions
                WHERE user_id = %s
                """,
                (user_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def _find_user_by_customer(customer_id: str) -> str | None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_id
                FROM subscriptions
                WHERE stripe_customer_id = %s
                """,
                (customer_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    return row["user_id"] if row else None


def _remember_customer(user_id: str, customer_id: str) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO subscriptions (user_id, stripe_customer_id)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    stripe_customer_id = EXCLUDED.stripe_customer_id,
                    updated_at = NOW()
                """,
                (user_id, customer_id),
            )
        conn.commit()
    finally:
        conn.close()


def _subscription_items(subscription: dict[str, Any]) -> list[dict[str, Any]]:
    items = subscription.get("items") or {}
    return list(items.get("data") or [])


def _plan_from_subscription(subscription: dict[str, Any]) -> tuple[str, str | None]:
    for item in _subscription_items(subscription):
        price = item.get("price") or {}
        price_id = price.get("id")
        plan = _PLAN_BY_PRICE.get(price_id)
        if plan:
            return plan, price_id
    return "free", None


def _subscription_period_end(subscription: dict[str, Any]) -> int | None:
    # Stripe API versions before 2025-03-31 expose this on the subscription.
    period_end = subscription.get("current_period_end")
    if period_end:
        return int(period_end)

    # Newer API versions expose billing periods on each subscription item.
    item_period_ends = [
        int(item["current_period_end"])
        for item in _subscription_items(subscription)
        if item.get("current_period_end")
    ]
    return max(item_period_ends, default=None)


def _sync_subscription(
    subscription: dict[str, Any],
    *,
    fallback_user_id: str | None = None,
) -> bool:
    customer_id = str(subscription.get("customer") or "")
    metadata = subscription.get("metadata") or {}
    user_id = metadata.get("user_id") or fallback_user_id
    if not user_id and customer_id:
        user_id = _find_user_by_customer(customer_id)
    if not user_id:
        return False

    subscribed_plan, price_id = _plan_from_subscription(subscription)
    status = str(subscription.get("status") or "unknown")
    effective_plan = subscribed_plan if status in _ACCESS_STATUSES else "free"
    current_period_end = _subscription_period_end(subscription)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO subscriptions (
                    user_id,
                    stripe_customer_id,
                    stripe_subscription_id,
                    stripe_price_id,
                    plan,
                    status,
                    current_period_end,
                    cancel_at_period_end,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, to_timestamp(%s), %s, NOW())
                ON CONFLICT (user_id) DO UPDATE SET
                    stripe_customer_id = EXCLUDED.stripe_customer_id,
                    stripe_subscription_id = EXCLUDED.stripe_subscription_id,
                    stripe_price_id = EXCLUDED.stripe_price_id,
                    plan = EXCLUDED.plan,
                    status = EXCLUDED.status,
                    current_period_end = EXCLUDED.current_period_end,
                    cancel_at_period_end = EXCLUDED.cancel_at_period_end,
                    updated_at = NOW()
                """,
                (
                    user_id,
                    customer_id or None,
                    subscription.get("id"),
                    price_id,
                    effective_plan,
                    status,
                    current_period_end,
                    bool(subscription.get("cancel_at_period_end", False)),
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return True


def _stripe_error(exc: Exception) -> HTTPException:
    message = getattr(exc, "user_message", None) or "Stripe request failed."
    return HTTPException(status_code=502, detail=message)


@router.post("/checkout")
async def create_checkout(body: CheckoutBody, request: Request):
    _require_test_configuration()
    user_id = request.state.user_id
    record = _get_billing_record(user_id)
    if record and record["plan"] != "free" and record["status"] in _ACCESS_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="An active subscription already exists. Use Manage billing to change it.",
        )

    price_id = _PRICE_BASIC if body.plan == "basic" else _PRICE_PRO
    checkout_args: dict[str, Any] = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "client_reference_id": user_id,
        "metadata": {"user_id": user_id, "plan": body.plan},
        "subscription_data": {"metadata": {"user_id": user_id, "plan": body.plan}},
        "success_url": (
            f"{_FRONTEND_URL}/subscription"
            "?checkout=success&session_id={CHECKOUT_SESSION_ID}"
        ),
        "cancel_url": f"{_FRONTEND_URL}/subscription?checkout=canceled",
    }
    if record and record.get("stripe_customer_id"):
        checkout_args["customer"] = record["stripe_customer_id"]
    else:
        checkout_args["customer_email"] = request.state.email

    try:
        session = stripe.checkout.Session.create(**checkout_args)
    except stripe.error.StripeError as exc:
        raise _stripe_error(exc) from exc
    return {"checkout_url": session.url}


@router.get("/checkout-session/{session_id}")
async def checkout_session_status(session_id: str, request: Request):
    _require_test_configuration()
    if not session_id.startswith("cs_test_"):
        raise HTTPException(status_code=400, detail="Invalid sandbox Checkout Session ID.")

    try:
        session = _stripe_dict(
            stripe.checkout.Session.retrieve(
                session_id,
                expand=["subscription"],
            )
        )
    except stripe.error.StripeError as exc:
        raise _stripe_error(exc) from exc

    if bool(session.get("livemode")):
        raise HTTPException(status_code=400, detail="Live Stripe sessions are not accepted.")
    if session.get("client_reference_id") != request.state.user_id:
        raise HTTPException(status_code=403, detail="Checkout Session does not belong to this user.")

    subscription = session.get("subscription")
    if session.get("status") == "complete" and subscription:
        if isinstance(subscription, str):
            try:
                subscription = _stripe_dict(
                    stripe.Subscription.retrieve(subscription)
                )
            except stripe.error.StripeError as exc:
                raise _stripe_error(exc) from exc
        _sync_subscription(
            _stripe_dict(subscription),
            fallback_user_id=request.state.user_id,
        )

    checkout_complete = (
        session.get("status") == "complete"
        and session.get("payment_status") in {"paid", "no_payment_required"}
    )
    return {
        "status": session.get("status"),
        "payment_status": session.get("payment_status"),
        "complete": checkout_complete,
    }


@router.post("/portal")
async def create_portal_session(request: Request):
    _require_test_configuration()
    record = _get_billing_record(request.state.user_id)
    customer_id = record.get("stripe_customer_id") if record else None
    if not customer_id:
        raise HTTPException(
            status_code=409,
            detail="No Stripe customer exists for this account yet.",
        )

    try:
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{_FRONTEND_URL}/subscription",
        )
    except stripe.error.StripeError as exc:
        raise _stripe_error(exc) from exc
    return {"portal_url": session.url}


@router.post("/webhook")
async def stripe_webhook(request: Request):
    _require_test_configuration(webhook=True)
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = _stripe_dict(
            stripe.Webhook.construct_event(payload, signature, _WEBHOOK_SECRET)
        )
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature.") from exc

    if bool(event.get("livemode")):
        raise HTTPException(status_code=400, detail="Live Stripe events are not accepted.")

    event_type = event["type"]
    obj = _stripe_dict(event["data"]["object"])
    if event_type in {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }:
        _sync_subscription(obj)
    elif event_type == "checkout.session.completed" and obj.get("mode") == "subscription":
        user_id = obj.get("client_reference_id") or (obj.get("metadata") or {}).get("user_id")
        customer_id = obj.get("customer")
        if user_id and customer_id:
            _remember_customer(str(user_id), str(customer_id))

    return {"received": True}


@router.get("/status")
async def billing_status(request: Request):
    user_id = request.state.user_id
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    plan,
                    status,
                    current_period_end,
                    cancel_at_period_end,
                    stripe_customer_id IS NOT NULL AS can_manage_billing
                FROM subscriptions
                WHERE user_id = %s
                """,
                (user_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        return {
            "plan": "free",
            "status": "none",
            "current_period_end": None,
            "cancel_at_period_end": False,
            "can_manage_billing": False,
        }
    return dict(row)
