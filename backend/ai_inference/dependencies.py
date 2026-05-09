"""
FastAPI dependency factories for the ai_inference service.

Singletons (grader, cache, batch engine) are created once at startup via
functools.lru_cache.  The HITL store is per-request because it wraps a
database session that must be closed after each request.
"""

from __future__ import annotations

import os
from functools import lru_cache

from fastapi import Depends
from sqlalchemy.orm import Session

from core.batch_engine import BatchEngine
from core.cache import InMemoryCache, RedisCache
from core.grader import GradingEngine
from core.hitl_store import HITLStore
from db.database import get_db


# ── Singleton factories ────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _grader() -> GradingEngine:
    return GradingEngine()


@lru_cache(maxsize=1)
def _cache() -> InMemoryCache | RedisCache:
    redis_url = os.getenv("REDIS_URL", "")
    return RedisCache(redis_url=redis_url) if redis_url else InMemoryCache()


@lru_cache(maxsize=1)
def _batch_engine() -> BatchEngine:
    return BatchEngine(grader=_grader(), cache=_cache())


# ── FastAPI Depends callables ──────────────────────────────────────────────────

def get_grader() -> GradingEngine:
    return _grader()


def get_cache() -> InMemoryCache | RedisCache:
    return _cache()


def get_batch_engine() -> BatchEngine:
    return _batch_engine()


def get_hitl_store(db: Session = Depends(get_db)) -> HITLStore:
    return HITLStore(db)
