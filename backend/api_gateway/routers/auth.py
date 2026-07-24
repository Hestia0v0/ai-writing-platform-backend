import os
import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr

from db import get_conn, get_user_roles, grant_role
from auth import hash_password, verify_password, create_token

router = APIRouter()

_SUPER_ADMIN_EMAIL = os.getenv("SUPER_ADMIN_EMAIL", "").strip().lower()


def _seed_super_admin_if_match(user_id: str, email: str) -> list[str]:
    """
    If SUPER_ADMIN_EMAIL is set and matches this account, grant super_admin.
    Idempotent. Returns the account's roles after the check. Covers the
    "email registers/logs in AFTER the env var was set" ordering; the
    "env var set AFTER the email already exists" ordering is covered by the
    startup seed in main.py's lifespan.
    """
    if _SUPER_ADMIN_EMAIL and email.lower() == _SUPER_ADMIN_EMAIL:
        grant_role(user_id, "super_admin", "env-seed")
    return get_user_roles(user_id)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(req: RegisterRequest) -> TokenResponse:
    # Self-registration is ALWAYS a plain user — there is no client-controlled
    # path to admin/reviewer/super_admin. The only way in is _seed_super_admin_if_match
    # (env-configured, checked below) or a super_admin granting a role later.
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users WHERE email = %s", (req.email,))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="Email already registered.")
            user_id = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO users (user_id, email, hashed_password) VALUES (%s, %s, %s)",
                (user_id, req.email, hash_password(req.password)),
            )
        conn.commit()
    roles = _seed_super_admin_if_match(user_id, req.email)
    return TokenResponse(access_token=create_token(user_id, req.email, roles))


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest) -> TokenResponse:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, hashed_password FROM users WHERE email = %s AND is_active = TRUE",
                (req.email,),
            )
            row = cur.fetchone()
    if not row or not verify_password(req.password, row["hashed_password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    roles = _seed_super_admin_if_match(row["user_id"], req.email)
    return TokenResponse(access_token=create_token(row["user_id"], req.email, roles))
