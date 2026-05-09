"""
Single-document inference endpoint.

POST /inference/generate
  – Cache lookup first (returns immediately on hit)
  – Grades via GradingEngine on miss
  – Stores result in cache
  – Routes to HITL queue when flagged

POST /inference/refine
  – Rewrites original_text applying feedback and improvement_tips
"""

import logging
import os
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.cache import InMemoryCache, RedisCache
from core.grader import GradingEngine
from core.hitl_store import HITLStore
from core.models import GradingResult, InferenceRequest
from dependencies import get_cache, get_grader, get_hitl_store

logger = logging.getLogger(__name__)

_INJECTION_RE = re.compile(
    r"ignore\s+(previous|all)\s+instructions"
    r"|disregard\s+your\s+prompt"
    r"|you\s+are\s+now"
    r"|forget\s+everything"
    r"|^\s*system\s*:"
    r"|^\s*assistant\s*:",
    re.IGNORECASE | re.MULTILINE,
)
_EXCESSIVE_NEWLINES = re.compile(r"\n{4,}")


def detect_prompt_injection(text: str) -> bool:
    return bool(_INJECTION_RE.search(text)) or bool(_EXCESSIVE_NEWLINES.search(text))


class RefineRequest(BaseModel):
    document_id: str
    original_text: str
    feedback: str
    improvement_tips: list[str]
    model: str | None = None


class RefineResult(BaseModel):
    refined_text: str
    model_used: str
    tokens_used: int

router = APIRouter()


@router.post("/generate", response_model=GradingResult)
async def generate(
    request: InferenceRequest,
    grader: GradingEngine = Depends(get_grader),
    cache: InMemoryCache | RedisCache = Depends(get_cache),
    hitl_store: HITLStore = Depends(get_hitl_store),
) -> GradingResult:
    """
    Grade a single composition.

    Returns a cached result immediately if an exact or near-identical text
    (Jaccard ≥ 0.95) was submitted before.  Otherwise calls the AI model and
    caches the result for future identical submissions.

    Low-confidence results (< 0.75) and edge-zone scores (45–65) are
    automatically flagged and added to the human review queue.  The returned
    `review_id` field references the queue entry.
    """
    if detect_prompt_injection(request.text):
        raise HTTPException(
            status_code=400,
            detail="Input rejected: potential prompt injection detected.",
        )

    # ── 1. Cache lookup ────────────────────────────────────────────────────────
    hit = cache.get(request.text)
    if hit:
        cached_result, similarity = hit
        return cached_result.model_copy(
            update={
                "document_id": request.document_id,
                "cached": True,
                "cache_hit_similarity": round(similarity, 4),
            }
        )

    # ── 2. Grade ───────────────────────────────────────────────────────────────
    result = await grader.grade(
        document_id=request.document_id,
        text=request.text,
        model=request.model,
        force_review=request.force_review,
    )

    # ── 3. Cache the result ────────────────────────────────────────────────────
    cache.set(request.text, result)

    # ── 4. HITL routing ────────────────────────────────────────────────────────
    if result.flagged_for_review:
        review_item = hitl_store.enqueue(result, request.text)
        result = result.model_copy(update={"review_id": review_item.review_id})

    return result


@router.post("/cache/invalidate")
async def invalidate_cache(
    text: str,
    cache: InMemoryCache | RedisCache = Depends(get_cache),
) -> dict:
    """Remove a specific text entry from the cache (e.g. after a human override)."""
    removed = cache.invalidate(text)
    return {"invalidated": removed}


@router.get("/cache/stats")
async def cache_stats(
    cache: InMemoryCache | RedisCache = Depends(get_cache),
) -> dict:
    return cache.stats()


@router.post("/refine", response_model=RefineResult)
async def refine(request: RefineRequest) -> RefineResult:
    """
    Rewrite original_text applying the provided feedback and improvement_tips
    while preserving the author's voice.
    """
    if detect_prompt_injection(request.original_text):
        raise HTTPException(
            status_code=400,
            detail="Input rejected: potential prompt injection detected.",
        )

    model = request.model or "deepseek-v4-flash"
    api_key = os.getenv("DEEPSEEK_API_KEY", "")

    tips_block = "\n".join(f"- {tip}" for tip in request.improvement_tips)

    if not api_key:
        logger.warning("No DEEPSEEK_API_KEY — returning mock refinement")
        return RefineResult(
            refined_text=f"[mock refinement]\n\n{request.original_text}",
            model_used="mock",
            tokens_used=0,
        )

    try:
        from openai import AsyncOpenAI  # noqa: PLC0415
        from core.grader import DEEPSEEK_BASE_URL  # noqa: PLC0415

        client = AsyncOpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)
        response = await client.chat.completions.create(
            model=model,
            max_tokens=4096,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert writing coach. Rewrite the essay below, faithfully "
                        "applying the feedback and every improvement tip provided. Preserve the "
                        "author's voice, tone, and intent — improve clarity and quality, do not "
                        "change the topic. Write only the refined essay — no preamble, no commentary."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"### Original essay\n{request.original_text}\n\n"
                        f"### Feedback\n{request.feedback}\n\n"
                        f"### Improvement tips\n{tips_block}"
                    ),
                },
            ],
        )
        refined_text = response.choices[0].message.content or ""
        return RefineResult(
            refined_text=refined_text,
            model_used=model,
            tokens_used=response.usage.prompt_tokens + response.usage.completion_tokens,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Refine call failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"AI refinement failed: {exc}") from exc
