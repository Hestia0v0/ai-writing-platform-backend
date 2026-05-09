import os

import stripe
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from db import get_conn

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
_PRICE_BASIC = os.getenv("STRIPE_PRICE_BASIC", "")
_PRICE_PRO = os.getenv("STRIPE_PRICE_PRO", "")
_FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

router = APIRouter()


class CheckoutBody(BaseModel):
    plan: str  # "basic" | "pro"


@router.post("/checkout")
async def create_checkout(body: CheckoutBody, request: Request):
    user_id = request.state.user_id
    price_id = _PRICE_BASIC if body.plan == "basic" else _PRICE_PRO
    if not price_id:
        raise HTTPException(status_code=500, detail="Price ID not configured.")

    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        subscription_data={"metadata": {"user_id": user_id}},
        success_url=f"{_FRONTEND_URL}/subscription?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{_FRONTEND_URL}/subscription",
    )
    return {"checkout_url": session.url}


@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, _WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid Stripe signature.")

    if event["type"] in ("customer.subscription.created", "customer.subscription.updated"):
        sub = event["data"]["object"]
        customer_id = sub["customer"]
        status = sub["status"]
        current_period_end = sub.get("current_period_end")

        plan = "basic"
        items = sub.get("items", {}).get("data", [])
        if items:
            price_id = items[0]["price"]["id"]
            if price_id == _PRICE_PRO:
                plan = "pro"

        user_id = sub.get("metadata", {}).get("user_id")

        if user_id:
            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO subscriptions
                            (user_id, stripe_customer_id, plan, status, current_period_end)
                        VALUES (%s, %s, %s, %s, to_timestamp(%s))
                        ON CONFLICT (user_id) DO UPDATE SET
                            stripe_customer_id = EXCLUDED.stripe_customer_id,
                            plan               = EXCLUDED.plan,
                            status             = EXCLUDED.status,
                            current_period_end = EXCLUDED.current_period_end
                        """,
                        (user_id, customer_id, plan, status, current_period_end),
                    )
                conn.commit()
            finally:
                conn.close()

    return {"received": True}


@router.get("/status")
async def billing_status(request: Request):
    user_id = request.state.user_id
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT plan, status, current_period_end FROM subscriptions WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        return {"plan": "free", "status": "none"}
    return dict(row)
