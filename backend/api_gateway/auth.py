import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import HTTPException, Request, status

JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = 60 * 24  # 24 hours


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_token(user_id: str, email: str, roles: list[str] | None = None) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "roles": roles or [],
        "exp": datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


def require_role(*roles: str):
    """
    FastAPI dependency factory. A role change only takes effect on the
    caller's NEXT login (roles are baked into the JWT at issuance, tokens
    live up to 24h) — acceptable staleness for this project's scale.
    """
    allowed = set(roles)

    def _dependency(request: Request) -> set[str]:
        caller_roles = set(getattr(request.state, "roles", []))
        if not caller_roles & allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {sorted(allowed)}.",
            )
        return caller_roles

    return _dependency
