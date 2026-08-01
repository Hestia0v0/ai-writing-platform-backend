import logging
import os
from contextlib import asynccontextmanager
from datetime import date
from typing import Optional

import jwt
import redis as redis_lib
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from auth import decode_token
from db import get_conn, seed_super_admin_by_email
from routers import admin, auth, billing, health, proxy

logger = logging.getLogger(__name__)

_CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS", "http://localhost:5173,http://localhost:3001"
    ).split(",")
    if origin.strip()
]
_SUPER_ADMIN_EMAIL = os.getenv("SUPER_ADMIN_EMAIL", "")

# Paths that don't require a JWT token
_PUBLIC_PATHS = {"/", "/docs", "/openapi.json", "/redoc", "/api/v1/billing/webhook"}
_PUBLIC_PREFIXES = ("/health", "/auth")

_DAILY_LIMITS = {"free": 10, "basic": 100}

_redis_client = redis_lib.from_url(
    os.getenv("REDIS_URL", "redis://redis:6379/0"),
    decode_responses=True,
)

# lifespan is a context manager that is used to manage the lifespan of the application
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Covers the "SUPER_ADMIN_EMAIL set AFTER that email already registered"
    # ordering — the register/login-time check in routers/auth.py covers the
    # reverse ordering. No-op (and safe to retry) if the email hasn't
    # registered yet, or the DB isn't reachable at cold-start.
    if _SUPER_ADMIN_EMAIL:
        try:
            seed_super_admin_by_email(_SUPER_ADMIN_EMAIL)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Super-admin seed check failed at startup: %s", exc)
    yield


app = FastAPI(title="API Gateway", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _get_user_plan(user_id: str) -> str:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT plan, status FROM subscriptions WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row or row["status"] not in {"active", "trialing", "past_due"}:
        return "free"
    return row["plan"]


def _check_quota(user_id: str) -> Optional[JSONResponse]:
    plan = _get_user_plan(user_id)
    if plan == "pro":
        return None
    limit = _DAILY_LIMITS.get(plan, 10)
    today = date.today().isoformat()
    key = f"quota:{user_id}:{today}"
    count = _redis_client.incr(key)
    if count == 1:
        _redis_client.expire(key, 86400)
    if count > limit:
        return JSONResponse(
            status_code=429,
            content={"detail": "Daily quota exceeded. Please upgrade your plan."},
        )
    return None


@app.middleware("http")
async def jwt_middleware(request: Request, call_next):
    # Always pass CORS preflight requests
    if request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path
    if path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES):
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(
            status_code=401,
            content={"detail": "Missing or invalid Authorization header."},
        )

    token = auth_header[len("Bearer "):]
    try:
        payload = decode_token(token)
        request.state.user_id = payload["sub"]
        request.state.email = payload["email"]
        # payload.get(...) rather than payload["roles"]: tokens issued before
        # this feature shipped don't have the claim — treat them as plain
        # users rather than rejecting them outright.
        request.state.roles = payload.get("roles", [])
    except jwt.ExpiredSignatureError:
        return JSONResponse(status_code=401, content={"detail": "Token expired."})
    except jwt.InvalidTokenError:
        return JSONResponse(status_code=401, content={"detail": "Invalid token."})

    # Quota enforcement: POST /api/v1/inference/* only
    if request.method == "POST" and path.startswith("/api/v1/inference/"):
        quota_response = _check_quota(request.state.user_id)
        if quota_response is not None:
            return quota_response

    return await call_next(request)


app.include_router(health.router, prefix="/health", tags=["health"])
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(billing.router, prefix="/api/v1/billing", tags=["billing"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"])
app.include_router(proxy.router, prefix="/api/v1", tags=["proxy"])


@app.get("/")
async def root():
    return {"service": "api_gateway", "status": "ok"}
