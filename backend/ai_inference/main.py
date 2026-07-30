import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from db.database import init_db
from routers import batch_cache, health, hitl, inference, rubric

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

_CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS", "http://localhost:5173,http://localhost:3001"
    ).split(",")
    if origin.strip()
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create DB tables and warm up singleton dependencies
    init_db()
    from dependencies import _grader, _cache  # noqa: F401 — trigger lru_cache init
    _grader()
    _cache()
    yield
    # Shutdown: nothing to clean up for SQLite / in-memory cache


app = FastAPI(
    title="AI Inference Service",
    version="1.0.0",
    description=(
        "Rubric-based AI grading engine with smart caching "
        "and human-in-the-loop review queue."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response

app.include_router(health.router, prefix="/health", tags=["health"])
app.include_router(inference.router, prefix="/inference", tags=["inference"])
app.include_router(batch_cache.router, prefix="/batch", tags=["batch"])
app.include_router(hitl.router, prefix="/hitl", tags=["human-in-the-loop"])
app.include_router(rubric.router, prefix="/rubric", tags=["rubric"])


@app.get("/")
async def root():
    return {"service": "ai_inference", "status": "ok", "version": "1.0.0"}
