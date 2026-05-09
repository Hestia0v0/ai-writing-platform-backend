"""
BatchEngine — concurrent document evaluation with cache integration.

Processing pipeline per item:
  1. Cache lookup  (exact → fuzzy)
  2. Grade via GradingEngine if cache miss
  3. Store result in cache
  4. Auto-route to HITL queue when flagged

Concurrency is capped per-job via asyncio.Semaphore(concurrency) so a single
large batch cannot saturate the Anthropic rate limit.  Jobs are fired as
background asyncio Tasks; callers poll /batch/status/{job_id} for progress.

Job state is held in-process (dict).  For multi-replica deployments, replace
self._jobs with a Redis-backed store.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from core.models import (
    BatchItemResult,
    BatchJobStatus,
    BatchSubmitRequest,
    CompositionItem,
    GradingResult,
)

if TYPE_CHECKING:
    from core.cache import InMemoryCache, RedisCache
    from core.grader import GradingEngine
    from core.hitl_store import HITLStore

logger = logging.getLogger(__name__)


class BatchEngine:
    def __init__(
        self,
        grader: GradingEngine,
        cache: InMemoryCache | RedisCache,
    ) -> None:
        self._grader = grader
        self._cache = cache
        self._jobs: dict[str, BatchJobStatus] = {}

    # ── Public API ─────────────────────────────────────────────────────────────

    def submit(
        self,
        request: BatchSubmitRequest,
        hitl_store: Optional[HITLStore],
    ) -> BatchJobStatus:
        """
        Enqueue a batch job and return a status object immediately.
        The job runs as a background asyncio Task; poll get_status() for progress.
        Idempotent: submitting the same job_id twice returns the existing status.
        """
        if request.job_id in self._jobs:
            return self._jobs[request.job_id]

        job = BatchJobStatus(
            job_id=request.job_id,
            status="queued",
            total=len(request.compositions),
            completed=0,
            cached_hits=0,
            flagged_for_review=0,
            started_at=datetime.utcnow(),
        )
        self._jobs[request.job_id] = job

        asyncio.create_task(
            self._run_job(job, request, hitl_store),
            name=f"batch-{request.job_id}",
        )
        return job

    def get_status(self, job_id: str) -> BatchJobStatus | None:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[BatchJobStatus]:
        return list(self._jobs.values())

    # ── Job runner ─────────────────────────────────────────────────────────────

    async def _run_job(
        self,
        job: BatchJobStatus,
        request: BatchSubmitRequest,
        hitl_store: Optional[HITLStore],
    ) -> None:
        job.status = "running"
        semaphore = asyncio.Semaphore(request.concurrency)

        async def bounded(item: CompositionItem) -> BatchItemResult:
            async with semaphore:
                return await self._process_item(item, request.model, hitl_store, job)

        results = await asyncio.gather(
            *[bounded(item) for item in request.compositions]
        )
        job.results = list(results)
        job.status = "completed"
        job.completed_at = datetime.utcnow()
        logger.info(
            "Batch complete  job=%s total=%d cached=%d flagged=%d elapsed=%.1fs",
            job.job_id,
            job.total,
            job.cached_hits,
            job.flagged_for_review,
            (job.completed_at - job.started_at).total_seconds(),
        )

    async def _process_item(
        self,
        item: CompositionItem,
        model: str,
        hitl_store: Optional[HITLStore],
        job: BatchJobStatus,
    ) -> BatchItemResult:
        try:
            # ── 1. Cache lookup ────────────────────────────────────────────────
            hit = self._cache.get(item.text)
            if hit:
                cached_result, similarity = hit
                # Patch document_id so the caller gets the right provenance.
                cached_result = cached_result.model_copy(
                    update={
                        "document_id": item.document_id,
                        "cached": True,
                        "cache_hit_similarity": round(similarity, 4),
                    }
                )
                job.cached_hits += 1
                job.completed += 1
                return BatchItemResult(
                    composition_id=item.composition_id,
                    document_id=item.document_id,
                    status="cached",
                    result=cached_result,
                )

            # ── 2. Grade ───────────────────────────────────────────────────────
            result: GradingResult = await self._grader.grade(
                document_id=item.document_id,
                text=item.text,
                model=model,
            )

            # ── 3. Cache the fresh result ──────────────────────────────────────
            self._cache.set(item.text, result)

            # ── 4. HITL routing ────────────────────────────────────────────────
            if result.flagged_for_review and hitl_store is not None:
                review_item = hitl_store.enqueue(result, item.text)
                result = result.model_copy(
                    update={"review_id": review_item.review_id}
                )
                job.flagged_for_review += 1

            job.completed += 1
            return BatchItemResult(
                composition_id=item.composition_id,
                document_id=item.document_id,
                status="success",
                result=result,
            )

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Batch item failed  composition_id=%s error=%s",
                item.composition_id,
                exc,
                exc_info=True,
            )
            job.completed += 1
            return BatchItemResult(
                composition_id=item.composition_id,
                document_id=item.document_id,
                status="failed",
                error=str(exc),
            )
