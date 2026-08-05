"""
Admin management endpoints — user list/deactivate, role grant/revoke,
subscription visibility and manual grants.

Role split (see infrastructure/init.sql user_roles table):
  admin        — user visibility/deactivation, subscription visibility,
                 knowledge base + batch admin (enforced in their own services).
  super_admin  — everything admin can, PLUS anything that grants a
                 privilege or bypasses payment: role changes, manual
                 subscription grants.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from auth import require_role
from db import db_conn

router = APIRouter()

_VALID_ROLES = {"reviewer", "admin", "super_admin"}
_VALID_PLANS = {"free", "basic", "pro"}


# ── Models ───────────────────────────────────────────────────────────────────

class UserSummary(BaseModel):
    user_id: str
    email: str
    is_active: bool
    created_at: datetime
    roles: list[str]
    plan: str


class UserPatch(BaseModel):
    is_active: bool


class RoleGrant(BaseModel):
    role: str


class SubscriptionSummary(BaseModel):
    user_id: str
    email: str
    plan: str
    status: str
    current_period_end: Optional[datetime] = None


class SubscriptionGrant(BaseModel):
    plan: str
    days: int = 30


# ── Internal helpers (plain functions, not route handlers — safe to call directly) ──

def _fetch_user_summary(user_id: str) -> UserSummary:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.user_id, u.email, u.is_active, u.created_at,
                       COALESCE(s.plan, 'free') AS plan,
                       COALESCE(
                           ARRAY_AGG(r.role) FILTER (WHERE r.role IS NOT NULL), '{}'
                       ) AS roles
                FROM users u
                LEFT JOIN subscriptions s ON s.user_id = u.user_id
                LEFT JOIN user_roles r ON r.user_id = u.user_id
                WHERE u.user_id = %s
                GROUP BY u.user_id, u.email, u.is_active, u.created_at, s.plan
                """,
                (user_id,),
            )
            row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return UserSummary(**row)


# ── Users ────────────────────────────────────────────────────────────────────

@router.get("/users", response_model=list[UserSummary])
def list_users(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _roles=Depends(require_role("admin", "super_admin")),
) -> list[UserSummary]:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.user_id, u.email, u.is_active, u.created_at,
                       COALESCE(s.plan, 'free') AS plan,
                       COALESCE(
                           ARRAY_AGG(r.role) FILTER (WHERE r.role IS NOT NULL), '{}'
                       ) AS roles
                FROM users u
                LEFT JOIN subscriptions s ON s.user_id = u.user_id
                LEFT JOIN user_roles r ON r.user_id = u.user_id
                GROUP BY u.user_id, u.email, u.is_active, u.created_at, s.plan
                ORDER BY u.created_at DESC
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            )
            rows = cur.fetchall()
    return [UserSummary(**row) for row in rows]


@router.get("/users/{user_id}", response_model=UserSummary)
def get_user(
    user_id: str,
    _roles=Depends(require_role("admin", "super_admin")),
) -> UserSummary:
    return _fetch_user_summary(user_id)


@router.patch("/users/{user_id}", response_model=UserSummary)
def patch_user(
    user_id: str,
    patch: UserPatch,
    _roles=Depends(require_role("admin", "super_admin")),
) -> UserSummary:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET is_active = %s WHERE user_id = %s RETURNING user_id",
                (patch.is_active, user_id),
            )
            updated = cur.fetchone()
        conn.commit()
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return _fetch_user_summary(user_id)


# ── Roles ────────────────────────────────────────────────────────────────────

@router.post("/users/{user_id}/roles", response_model=UserSummary, status_code=status.HTTP_201_CREATED)
def grant_user_role(
    user_id: str,
    body: RoleGrant,
    request: Request,
    _roles=Depends(require_role("super_admin")),
) -> UserSummary:
    if body.role not in _VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"role must be one of {sorted(_VALID_ROLES)}.",
        )
    granted_by = request.state.user_id  # authenticated caller, never client-supplied
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users WHERE user_id = %s", (user_id,))
            if cur.fetchone() is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
            cur.execute(
                """
                INSERT INTO user_roles (user_id, role, granted_by)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id, role) DO NOTHING
                """,
                (user_id, body.role, granted_by),
            )
        conn.commit()
    return _fetch_user_summary(user_id)


@router.delete("/users/{user_id}/roles/{role}", response_model=UserSummary)
def revoke_user_role(
    user_id: str,
    role: str,
    _roles=Depends(require_role("super_admin")),
) -> UserSummary:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM user_roles WHERE user_id = %s AND role = %s",
                (user_id, role),
            )
        conn.commit()
    return _fetch_user_summary(user_id)


# ── Subscriptions ────────────────────────────────────────────────────────────

@router.get("/subscriptions", response_model=list[SubscriptionSummary])
def list_subscriptions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _roles=Depends(require_role("admin", "super_admin")),
) -> list[SubscriptionSummary]:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.user_id, u.email,
                       COALESCE(s.plan, 'free') AS plan,
                       COALESCE(s.status, 'none') AS status,
                       s.current_period_end
                FROM users u
                LEFT JOIN subscriptions s ON s.user_id = u.user_id
                ORDER BY u.created_at DESC
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            )
            rows = cur.fetchall()
    return [SubscriptionSummary(**row) for row in rows]


@router.post("/subscriptions/{user_id}/grant", response_model=SubscriptionSummary)
def grant_subscription(
    user_id: str,
    body: SubscriptionGrant,
    _roles=Depends(require_role("super_admin")),
) -> SubscriptionSummary:
    """
    Manually grants a plan bypassing Stripe — e.g. comps, trials, refund
    goodwill. super_admin only: this is literally giving away paid access.
    """
    if body.plan not in _VALID_PLANS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"plan must be one of {sorted(_VALID_PLANS)}.",
        )
    period_end = datetime.now(timezone.utc) + timedelta(days=body.days)
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, email FROM users WHERE user_id = %s", (user_id,))
            user_row = cur.fetchone()
            if user_row is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
            cur.execute(
                """
                INSERT INTO subscriptions (user_id, plan, status, current_period_end)
                VALUES (%s, %s, 'active', %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    plan               = EXCLUDED.plan,
                    status             = EXCLUDED.status,
                    current_period_end = EXCLUDED.current_period_end
                """,
                (user_id, body.plan, period_end),
            )
        conn.commit()
    return SubscriptionSummary(
        user_id=user_id,
        email=user_row["email"],
        plan=body.plan,
        status="active",
        current_period_end=period_end,
    )
