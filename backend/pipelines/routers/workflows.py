"""
Workflows router — orchestrates document processing pipelines.

POST /workflows/trigger          Start a workflow (returns 202 immediately)
GET  /workflows/status/{id}      Poll workflow progress
GET  /workflows/list             List all workflow runs

Workflow types and required options:
  INGEST   options.filename (str) + options.content_b64 (base64-encoded file bytes)
           Runs the full 5-stage DocumentProcessor pipeline.
  GRADE    options.text (str)  [optional: options.model (str)]
           Calls the AI inference service to grade a composition.
  FEEDBACK Same as GRADE — alias for the feedback-generation use case.
  EXPORT   Not yet implemented.
"""

import asyncio
import base64
import logging
import os
import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from doc_processor import AIInferenceClient, DocumentProcessor, TextChunk

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Models ─────────────────────────────────────────────────────────────────────

class WorkflowType(str, Enum):
    INGEST   = "ingest"
    GRADE    = "grade"
    FEEDBACK = "feedback"
    EXPORT   = "export"


class WorkflowTrigger(BaseModel):
    workflow_type: WorkflowType
    document_id: str
    triggered_by: str
    options: Optional[dict] = None


class WorkflowStatus(BaseModel):
    workflow_id: str
    workflow_type: WorkflowType
    document_id: str
    status: str                   # queued | running | completed | failed
    steps_completed: list[str]
    steps_pending: list[str]
    result: Optional[dict] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


# ── Shared state ───────────────────────────────────────────────────────────────

_jobs: dict[str, WorkflowStatus] = {}

_AI_INFERENCE_URL = os.getenv("AI_INFERENCE_URL", "http://ai_inference:8001")
_processor        = DocumentProcessor(inference_client=AIInferenceClient(base_url=_AI_INFERENCE_URL))
_inference_client = AIInferenceClient(base_url=_AI_INFERENCE_URL)


# ── Background runners ─────────────────────────────────────────────────────────

async def _run_ingest(job: WorkflowStatus, filename: str, content: bytes) -> None:
    job.status = "running"
    job.started_at = datetime.utcnow().isoformat()
    try:
        result = await _processor.process(
            filename=filename,
            content=content,
            document_id=job.document_id,
        )
        job.steps_completed = ["upload", "parse", "clean", "chunk", "score"]
        job.steps_pending   = []
        job.status          = "completed" if result.status == "success" else "failed"
        job.result          = result.model_dump()
        if result.error:
            job.error = result.error
    except Exception as exc:
        logger.error("INGEST failed  workflow_id=%s  error=%s", job.workflow_id, exc)
        job.status = "failed"
        job.error  = str(exc)
    finally:
        job.completed_at = datetime.utcnow().isoformat()


async def _run_grade(job: WorkflowStatus, text: str) -> None:
    job.status = "running"
    job.started_at = datetime.utcnow().isoformat()
    try:
        chunks = [
            TextChunk(
                chunk_index=0,
                text=text,
                word_count=len(text.split()),
                char_count=len(text),
            )
        ]
        result = await _inference_client.score_document(
            document_id=job.document_id,
            chunks=chunks,
            word_count=len(text.split()),
        )
        job.steps_completed = ["score"]
        job.steps_pending   = []
        job.status          = "completed"
        job.result          = result.model_dump()
    except Exception as exc:
        logger.error("GRADE failed  workflow_id=%s  error=%s", job.workflow_id, exc)
        job.status = "failed"
        job.error  = str(exc)
    finally:
        job.completed_at = datetime.utcnow().isoformat()


# ── Route handlers ─────────────────────────────────────────────────────────────

@router.post(
    "/trigger",
    response_model=WorkflowStatus,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a document processing workflow",
)
async def trigger_workflow(trigger: WorkflowTrigger) -> WorkflowStatus:
    workflow_id = str(uuid.uuid4())
    opts = trigger.options or {}

    if trigger.workflow_type == WorkflowType.INGEST:
        filename    = opts.get("filename")
        content_b64 = opts.get("content_b64")
        if not filename or not content_b64:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="INGEST requires options.filename and options.content_b64 (base64-encoded file bytes).",
            )
        try:
            content = base64.b64decode(content_b64)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="options.content_b64 is not valid base64.",
            )

        job = WorkflowStatus(
            workflow_id=workflow_id,
            workflow_type=trigger.workflow_type,
            document_id=trigger.document_id,
            status="queued",
            steps_completed=[],
            steps_pending=["upload", "parse", "clean", "chunk", "score"],
        )
        _jobs[workflow_id] = job
        asyncio.create_task(_run_ingest(job, filename, content), name=f"ingest-{workflow_id}")

    elif trigger.workflow_type in (WorkflowType.GRADE, WorkflowType.FEEDBACK):
        text = opts.get("text")
        if not text:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{trigger.workflow_type.value.upper()} requires options.text.",
            )

        job = WorkflowStatus(
            workflow_id=workflow_id,
            workflow_type=trigger.workflow_type,
            document_id=trigger.document_id,
            status="queued",
            steps_completed=[],
            steps_pending=["score"],
        )
        _jobs[workflow_id] = job
        asyncio.create_task(_run_grade(job, text), name=f"grade-{workflow_id}")

    else:  # EXPORT — not yet implemented
        job = WorkflowStatus(
            workflow_id=workflow_id,
            workflow_type=trigger.workflow_type,
            document_id=trigger.document_id,
            status="failed",
            steps_completed=[],
            steps_pending=[],
            error="EXPORT workflow is not yet implemented.",
        )
        _jobs[workflow_id] = job

    return job


@router.get(
    "/status/{workflow_id}",
    response_model=WorkflowStatus,
    summary="Poll the status of a running workflow",
)
async def workflow_status(workflow_id: str) -> WorkflowStatus:
    job = _jobs.get(workflow_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found.",
        )
    return job


@router.get("/list", summary="List all workflow runs")
async def list_workflows() -> dict:
    jobs = list(_jobs.values())
    return {
        "workflows": [j.model_dump() for j in jobs],
        "total": len(jobs),
        "by_status": {
            s: sum(1 for j in jobs if j.status == s)
            for s in ("queued", "running", "completed", "failed")
        },
    }
