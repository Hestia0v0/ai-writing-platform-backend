"""
US-17 — Smart Evaluation Result Caching

Composition hashing + result caching so identical essays are never scored twice.

Backends (selected automatically at startup):
  1. Redis  — when REDIS_URL is set (production / docker-compose)
  2. In-memory LRU  — fallback for local development / testing

Cache key = SHA-256 of (text + "|" + language + "|" + framework + "|" + technique)
TTL defaults to 24 h (configurable via EVAL_CACHE_TTL_SECONDS env var).
Max in-memory entries defaults to 512 (configurable via EVAL_CACHE_MAX_SIZE).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from collections import OrderedDict
from typing import Optional

logger = logging.getLogger(__name__)

_TTL = int(os.getenv("EVAL_CACHE_TTL_SECONDS", "86400"))      # 24 h
_MAX_SIZE = int(os.getenv("EVAL_CACHE_MAX_SIZE", "512"))


def _make_key(
    text: str,
    language: str,
    framework: str | None,
    technique: str | None,
) -> str:
    raw = f"{text}|{language}|{framework or ''}|{technique or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── In-memory LRU cache (thread-safe enough for asyncio single-threaded event loop) ──

class _LRUCache:
    def __init__(self, maxsize: int) -> None:
        self._store: OrderedDict[str, str] = OrderedDict()
        self._maxsize = maxsize

    def get(self, key: str) -> Optional[str]:
        if key not in self._store:
            return None
        self._store.move_to_end(key)
        return self._store[key]

    def set(self, key: str, value: str) -> None:
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = value
        if len(self._store) > self._maxsize:
            self._store.popitem(last=False)

    def __len__(self) -> int:
        return len(self._store)


# ── Cache service facade ───────────────────────────────────────────────────────

class EvalCacheService:
    """
    Facade that hides the backend choice (Redis vs in-memory).
    Instantiate once at startup via get_eval_cache() in dependencies.py.
    """

    def __init__(self, redis_url: str | None = None) -> None:
        self._redis: object | None = None
        self._lru = _LRUCache(maxsize=_MAX_SIZE)

        url = redis_url or os.getenv("REDIS_URL", "")
        if url:
            try:
                import redis.asyncio as aioredis  # noqa: PLC0415
                self._redis = aioredis.from_url(url, decode_responses=True)
                logger.info("EvalCacheService: Redis backend connected (%s)", url)
            except ImportError:
                logger.warning(
                    "redis package not installed — EvalCacheService using in-memory LRU cache"
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "EvalCacheService: Redis connection failed (%s) — falling back to in-memory", exc
                )
        else:
            logger.info("EvalCacheService: no REDIS_URL — using in-memory LRU cache")

    def make_key(
        self,
        text: str,
        language: str,
        framework: str | None,
        technique: str | None,
    ) -> str:
        return _make_key(text, language, framework, technique)

    async def get(self, key: str) -> Optional[dict]:
        """Return cached EvaluationResult dict, or None on miss."""
        try:
            if self._redis is not None:
                raw = await self._redis.get(f"eval:{key}")  # type: ignore[union-attr]
            else:
                raw = self._lru.get(key)
            if raw:
                return json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("EvalCacheService.get failed: %s", exc)
        return None

    async def set(self, key: str, value: dict) -> None:
        """Store an EvaluationResult dict (serialised to JSON)."""
        try:
            serialised = json.dumps(value)
            if self._redis is not None:
                await self._redis.setex(f"eval:{key}", _TTL, serialised)  # type: ignore[union-attr]
            else:
                self._lru.set(key, serialised)
        except Exception as exc:  # noqa: BLE001
            logger.warning("EvalCacheService.set failed: %s", exc)

    @property
    def backend(self) -> str:
        return "redis" if self._redis is not None else "memory"
