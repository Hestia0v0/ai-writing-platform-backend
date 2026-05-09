import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from db.database import init_db
from routers import batch_cache, health, hitl, inference

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)


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
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/health", tags=["health"])
app.include_router(inference.router, prefix="/inference", tags=["inference"])
app.include_router(batch_cache.router, prefix="/batch", tags=["batch"])
app.include_router(hitl.router, prefix="/hitl", tags=["human-in-the-loop"])


@app.get("/")
async def root():
    return {"service": "ai_inference", "status": "ok", "version": "1.0.0"}
