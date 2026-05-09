"""
Batch evaluation endpoints.

POST /batch/submit          Enqueue a batch job (returns immediately, 202)
GET  /batch/status/{job_id} Poll job progress
GET  /batch/result/{job_id} Retrieve completed results (409 if still running)
GET  /batch/jobs            List all known jobs
"""

from fastapi import APIRouter, Depends, HTTPException, status

from core.batch_engine import BatchEngine
from core.hitl_store import HITLStore
from core.models import BatchJobStatus, BatchSubmitRequest
from dependencies import get_batch_engine, get_hitl_store

router = APIRouter()


@router.post(
    "/submit",
    response_model=BatchJobStatus,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a batch of compositions for asynchronous evaluation",
)
async def submit_batch(
    request: BatchSubmitRequest,
    batch_engine: BatchEngine = Depends(get_batch_engine),
    hitl_store: HITLStore = Depends(get_hitl_store),
) -> BatchJobStatus:
    """
    Accepts an array of compositions and processes them concurrently.

    Each item is first checked against the cache (exact + fuzzy similarity).
    Cache hits are returned immediately without calling the AI model, reducing
    cost and latency for near-duplicate submissions.

    Items that receive a low-confidence or edge-zone score are automatically
    routed to the human review queue.  The `flagged_for_review` counter in
    the response shows how many items were queued.

    Poll `/batch/status/{job_id}` for progress or
    `/batch/result/{job_id}` once `status == "completed"`.
    """
    return batch_engine.submit(request, hitl_store)


@router.get(
    "/status/{job_id}",
    response_model=BatchJobStatus,
    summary="Poll the progress of a running batch job",
)
async def batch_status(
    job_id: str,
    batch_engine: BatchEngine = Depends(get_batch_engine),
) -> BatchJobStatus:
    job = batch_engine.get_status(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Batch job '{job_id}' not found.",
        )
    return job


@router.get(
    "/result/{job_id}",
    response_model=BatchJobStatus,
    summary="Retrieve the full results of a completed batch job",
)
async def batch_result(
    job_id: str,
    batch_engine: BatchEngine = Depends(get_batch_engine),
) -> BatchJobStatus:
    job = batch_engine.get_status(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Batch job '{job_id}' not found.",
        )
    if job.status not in ("completed", "failed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job '{job_id}' is still {job.status}. Poll /batch/status/{job_id}.",
        )
    return job


@router.get("/jobs", summary="List all batch jobs (in-process state)")
async def list_jobs(
    batch_engine: BatchEngine = Depends(get_batch_engine),
) -> dict:
    jobs = batch_engine.list_jobs()
    return {
        "jobs": jobs,
        "total": len(jobs),
        "by_status": {
            s: sum(1 for j in jobs if j.status == s)
            for s in ("queued", "running", "completed", "failed")
        },
    }
