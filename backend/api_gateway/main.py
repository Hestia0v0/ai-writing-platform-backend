import os
from datetime import date
from typing import Optional

import jwt
import redis as redis_lib
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from auth import decode_token
from db import get_conn
from routers import auth, billing, health, proxy

_CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")

# Paths that don't require a JWT token
_PUBLIC_PATHS = {"/", "/docs", "/openapi.json", "/redoc", "/api/v1/billing/webhook"}
_PUBLIC_PREFIXES = ("/health", "/auth")

_DAILY_LIMITS = {"free": 10, "basic": 100}

_redis_client = redis_lib.from_url(
    os.getenv("REDIS_URL", "redis://redis:6379/0"),
    decode_responses=True,
)

app = FastAPI(title="API Gateway", version="0.1.0")

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
                "SELECT plan FROM subscriptions WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    return row["plan"] if row else "free"


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
app.include_router(proxy.router, prefix="/api/v1", tags=["proxy"])


@app.get("/")
async def root():
    return {"service": "api_gateway", "status": "ok"}
