"""
Two-tier evaluation cache.

Tier 1 — Exact match
  Key: SHA-256 of NFC-normalised, lowercased, whitespace-collapsed text.
  O(1) lookup.

Tier 2 — Fuzzy match
  Jaccard similarity over word token sets.
  Catches trivial paraphrasing (synonym swaps, article changes) that would
  otherwise bypass the exact cache and waste LLM tokens.
  O(n) scan; acceptable for corpora up to ~10k entries.  For larger corpora,
  replace with MinHash LSH (datasketch) without changing the public interface.

Redis variant
  Same interface as InMemoryCache.  Automatically falls back to in-memory if
  Redis is unreachable so the service starts cleanly in environments where
  Redis is not available (local dev, CI).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import unicodedata
from typing import Optional

from core.models import GradingResult

logger = logging.getLogger(__name__)


# ── Constants (overridable via env) ───────────────────────────────────────────

import os

CACHE_TTL: int = int(os.getenv("CACHE_TTL_SECONDS", str(24 * 3600)))
SIMILARITY_THRESHOLD: float = float(os.getenv("CACHE_SIMILARITY_THRESHOLD", "0.95"))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _fingerprint(text: str) -> str:
    """Canonical SHA-256 key for a piece of text."""
    normalised = unicodedata.normalize("NFC", text).lower()
    collapsed = " ".join(normalised.split())
    return hashlib.sha256(collapsed.encode()).hexdigest()


def _jaccard(a: str, b: str) -> float:
    """Token-level Jaccard similarity: |A ∩ B| / |A ∪ B|."""
    set_a = set(a.lower().split())
    set_b = set(b.lower().split())
    if not set_a and not set_b:
        return 1.0
    union = len(set_a | set_b)
    return len(set_a & set_b) / union if union else 0.0


# ── In-Memory Cache ────────────────────────────────────────────────────────────

class _Entry:
    __slots__ = ("result", "text", "expires_at")

    def __init__(self, result: GradingResult, text: str, ttl: int) -> None:
        self.result = result
        self.text = text
        self.expires_at = time.monotonic() + ttl

    def is_expired(self) -> bool:
        return time.monotonic() > self.expires_at


class InMemoryCache:
    def __init__(
        self,
        ttl: int = CACHE_TTL,
        similarity_threshold: float = SIMILARITY_THRESHOLD,
    ) -> None:
        self._store: dict[str, _Entry] = {}
        self._ttl = ttl
        self._sim_threshold = similarity_threshold

    # ── Public interface ───────────────────────────────────────────────────────

    def get(self, text: str) -> tuple[GradingResult, float] | None:
        """
        Return (GradingResult, similarity) on hit, None on miss.
        similarity == 1.0 for exact hits, < 1.0 for fuzzy hits.
        """
        self._evict()

        # Exact hit
        key = _fingerprint(text)
        entry = self._store.get(key)
        if entry:
            logger.debug("Cache exact hit  key=%s…", key[:8])
            return entry.result, 1.0

        # Fuzzy hit
        for entry in self._store.values():
            sim = _jaccard(text, entry.text)
            if sim >= self._sim_threshold:
                logger.debug("Cache fuzzy hit  sim=%.3f", sim)
                return entry.result, sim

        return None

    def set(self, text: str, result: GradingResult) -> None:
        key = _fingerprint(text)
        self._store[key] = _Entry(result, text, self._ttl)
        logger.debug("Cached result  key=%s… ttl=%ds", key[:8], self._ttl)

    def invalidate(self, text: str) -> bool:
        key = _fingerprint(text)
        return bool(self._store.pop(key, None))

    def stats(self) -> dict:
        self._evict()
        return {
            "backend": "memory",
            "entries": len(self._store),
            "ttl_seconds": self._ttl,
            "similarity_threshold": self._sim_threshold,
        }

    # ── Internal ───────────────────────────────────────────────────────────────

    def _evict(self) -> None:
        expired = [k for k, v in self._store.items() if v.is_expired()]
        for k in expired:
            del self._store[k]


# ── Redis Cache ────────────────────────────────────────────────────────────────

class RedisCache:
    """
    Redis-backed cache.  Stores:
      • grade:<hash>       → serialised GradingResult JSON
      • grade:text:<hash>  → first 2 000 chars of the original text (fuzzy matching)

    Falls back to InMemoryCache transparently if Redis is unreachable.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        ttl: int = CACHE_TTL,
        similarity_threshold: float = SIMILARITY_THRESHOLD,
    ) -> None:
        self._ttl = ttl
        self._sim_threshold = similarity_threshold
        self._fallback = InMemoryCache(ttl, similarity_threshold)
        self._redis = None

        try:
            import redis  # noqa: PLC0415
            client = redis.from_url(
                redis_url, decode_responses=True, socket_connect_timeout=2
            )
            client.ping()
            self._redis = client
            logger.info("Redis cache connected  url=%s", redis_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis unavailable (%s) — using in-memory fallback", exc)

    # ── Public interface ───────────────────────────────────────────────────────

    def get(self, text: str) -> tuple[GradingResult, float] | None:
        if self._redis is None:
            return self._fallback.get(text)

        key = _fingerprint(text)
        try:
            # Exact hit
            raw = self._redis.get(f"grade:{key}")
            if raw:
                return GradingResult.model_validate_json(raw), 1.0

            # Fuzzy hit — scan stored text snippets (capped at last 1 000 entries)
            text_keys = self._redis.keys("grade:text:*")
            for tkey in text_keys[-1000:]:
                stored_text = self._redis.get(tkey)
                if not stored_text:
                    continue
                sim = _jaccard(text, stored_text)
                if sim >= self._sim_threshold:
                    result_key = tkey.replace("grade:text:", "grade:")
                    raw_result = self._redis.get(result_key)
                    if raw_result:
                        logger.debug("Redis fuzzy hit  sim=%.3f", sim)
                        return GradingResult.model_validate_json(raw_result), sim

        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis get error: %s — falling back to memory", exc)
            return self._fallback.get(text)

        return None

    def set(self, text: str, result: GradingResult) -> None:
        if self._redis is None:
            self._fallback.set(text, result)
            return
        key = _fingerprint(text)
        try:
            self._redis.setex(f"grade:{key}", self._ttl, result.model_dump_json())
            # Store a trimmed copy of the text for fuzzy matching on cache reads
            self._redis.setex(f"grade:text:{key}", self._ttl, text[:2000])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis set error: %s", exc)
            self._fallback.set(text, result)

    def invalidate(self, text: str) -> bool:
        if self._redis is None:
            return self._fallback.invalidate(text)
        key = _fingerprint(text)
        try:
            deleted = self._redis.delete(f"grade:{key}", f"grade:text:{key}")
            return deleted > 0
        except Exception:  # noqa: BLE001
            return self._fallback.invalidate(text)

    def stats(self) -> dict:
        base: dict = {
            "backend": "redis" if self._redis else "memory (fallback)",
            "ttl_seconds": self._ttl,
            "similarity_threshold": self._sim_threshold,
        }
        if self._redis:
            try:
                info = self._redis.info("memory")
                grade_keys = self._redis.keys("grade:*")
                base["redis_used_memory"] = info.get("used_memory_human")
                base["entries"] = len(grade_keys) // 2  # each entry = 2 keys
            except Exception:  # noqa: BLE001
                pass
        else:
            base.update(self._fallback.stats())
        return base
