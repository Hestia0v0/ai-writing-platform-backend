"""
Fetches the admin-configured "content" dimension weight from ai_inference's
rubric config (GET /rubric/dimensions?language=en), so the Master Judge's
Content & Ideas scoring stays in sync with the same rubric an admin edits
via the Rubric Management page.

agents has no direct Postgres access by design (see dependencies.py) — this
is a small HTTP client + TTL cache rather than a second copy of the DB
connection. Any failure (network, ai_inference down, bad response) falls
back to the historical fixed weight (25.0) — rubric sync is a nice-to-have,
never a reason to fail or degrade grading.
"""
from __future__ import annotations

import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

DEFAULT_CONTENT_MAX_SCORE = 25.0
_CACHE_TTL_SECONDS = 60.0
_AI_INFERENCE_URL = os.getenv("AI_INFERENCE_URL", "http://ai_inference:8001").rstrip("/")

_cache: dict[str, tuple[float, float]] = {}  # language -> (fetched_at, content_max_score)


async def get_content_max_score(language: str = "en") -> float:
    now = time.monotonic()
    cached = _cache.get(language)
    if cached is not None and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{_AI_INFERENCE_URL}/rubric/dimensions", params={"language": language}
            )
            response.raise_for_status()
            dimensions = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "Could not fetch rubric config from ai_inference (%s); using default content weight %.1f",
            exc, DEFAULT_CONTENT_MAX_SCORE,
        )
        return DEFAULT_CONTENT_MAX_SCORE

    content_max = next(
        (float(d["max_score"]) for d in dimensions if d.get("dimension") == "content"),
        DEFAULT_CONTENT_MAX_SCORE,
    )
    _cache[language] = (now, content_max)
    return content_max
