import os
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor

POSTGRES_DSN = os.getenv("POSTGRES_DSN", "postgresql://platform:platform@postgres:5432/platform")


def get_conn():
    """
    Raw connection — the CALLER owns closing it. Prefer db_conn().

    Kept for the `conn = get_conn()` / `try: ... finally: conn.close()` call
    sites in billing.py and main.py, which already close correctly.
    """
    return psycopg2.connect(POSTGRES_DSN, cursor_factory=RealDictCursor)


@contextmanager
def db_conn():
    """
    Connection that is actually closed on exit.

    psycopg2's own `with conn:` is a TRANSACTION context manager, not a
    connection one — it commits or rolls back and leaves the socket open. So
    `with get_conn() as conn:` leaked one connection per request until
    PostgreSQL hit max_connections and every subsequent request 500'd. This
    wrapper keeps the transaction semantics (commit on clean exit, rollback on
    exception) and adds the close.
    """
    conn = get_conn()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


# The gateway's own tables, brought up to date on every startup.
#
# init.sql only runs when PostgreSQL initialises an EMPTY data directory, and
# infrastructure/migrations/*.sql have to be applied by hand. So any database
# provisioned before a schema change keeps the old shape forever — as does
# every managed database, which never sees init.sql at all. That is what broke
# GET /api/v1/billing/status: `subscriptions` predated the Stripe work, so
# `SELECT ... cancel_at_period_end ...` raised UndefinedColumn, and the generic
# handler in main.py turned it into a 500. `user_roles` (added with RBAC) is
# missing from those same databases, which fails login via get_user_roles().
#
# Every statement is idempotent, so this converges new and legacy databases
# alike without anyone remembering to run a migration.
_GATEWAY_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS subscriptions (
    user_id                 TEXT        PRIMARY KEY REFERENCES users(user_id),
    stripe_customer_id      TEXT,
    stripe_subscription_id  TEXT,
    stripe_price_id         TEXT,
    plan                    TEXT        NOT NULL DEFAULT 'free',
    status                  TEXT        NOT NULL DEFAULT 'none',
    current_period_end      TIMESTAMPTZ,
    cancel_at_period_end    BOOLEAN     NOT NULL DEFAULT FALSE,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT,
    ADD COLUMN IF NOT EXISTS stripe_price_id TEXT,
    ADD COLUMN IF NOT EXISTS cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE UNIQUE INDEX IF NOT EXISTS idx_subscriptions_stripe_customer_id
    ON subscriptions (stripe_customer_id)
    WHERE stripe_customer_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_subscriptions_stripe_subscription_id
    ON subscriptions (stripe_subscription_id)
    WHERE stripe_subscription_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS user_roles (
    id         SERIAL      PRIMARY KEY,
    user_id    TEXT        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    role       TEXT        NOT NULL CHECK (role IN ('reviewer', 'admin', 'super_admin')),
    granted_by TEXT,
    granted_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, role)
);

CREATE INDEX IF NOT EXISTS idx_user_roles_user_id ON user_roles (user_id);
"""


def ensure_gateway_schema() -> None:
    """Idempotent — safe to run on every startup, on new and legacy databases."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_GATEWAY_SCHEMA_DDL)


def get_user_roles(user_id: str) -> list[str]:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT role FROM user_roles WHERE user_id = %s", (user_id,))
            return [row["role"] for row in cur.fetchall()]


def get_user_id_by_email(email: str) -> str | None:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users WHERE LOWER(email) = LOWER(%s)", (email,))
            row = cur.fetchone()
    return row["user_id"] if row else None


def grant_role(user_id: str, role: str, granted_by: str) -> None:
    """Idempotent — no-op if the user already holds this role."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_roles (user_id, role, granted_by)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id, role) DO NOTHING
                """,
                (user_id, role, granted_by),
            )
        conn.commit()


def seed_super_admin_by_email(email: str) -> None:
    """
    If a user with this email already exists, ensure they hold super_admin.
    No-op (not an error) if the email hasn't registered yet — the
    register-time check in routers/auth.py covers that ordering instead.
    """
    email = email.strip().lower()
    if not email:
        return
    user_id = get_user_id_by_email(email)
    if user_id:
        grant_role(user_id, "super_admin", "env-seed")
