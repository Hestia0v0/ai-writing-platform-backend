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
