import json
import os
import re
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, UploadFile, File, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from doc_processor import (
    AIInferenceClient,
    DocumentProcessor,
    FeedbackItem,
    PipelineResult,
    PipelineStage,
    ScoringResult,
    SUPPORTED_EXTENSIONS,
)
from db import get_pool

router = APIRouter()

_USE_MOCK = os.getenv("AI_INFERENCE_MOCK", "false").lower() == "true"

_DIM_TO_CATEGORY = {
    "content": "evidence",
    "organization": "structure",
    "language": "clarity",
    "conventions": "grammar",
}


class _RubricItem(BaseModel):
    dimension: str
    score: float
    feedback: str = ""


class EditorRecordRequest(BaseModel):
    document_id: str
    text: str
    score: float
    grade: str
    rubric: Optional[list[_RubricItem]] = None
    overall_feedback: str = ""
    improvement_tips: list[str] = []
    model_used: str = "unknown"


_INFERENCE_URL = os.getenv("AI_INFERENCE_URL", "http://ai_inference:8001")

_processor = DocumentProcessor(
    inference_client=AIInferenceClient(
        base_url=_INFERENCE_URL,
        use_mock=_USE_MOCK,
    )
)


async def _save(result: PipelineResult, user_id: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pipeline_results (document_id, user_id, filename, status, result_json)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (document_id) DO UPDATE
                SET user_id     = EXCLUDED.user_id,
                    filename    = EXCLUDED.filename,
                    status      = EXCLUDED.status,
                    result_json = EXCLUDED.result_json
            """,
            result.document_id,
            user_id,
            result.filename,
            result.status,
            json.dumps(result.model_dump()),
        )


async def _load(document_id: str) -> PipelineResult | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT result_json FROM pipeline_results WHERE document_id = $1",
            document_id,
        )
    if row is None:
        return None
    return PipelineResult(**json.loads(row["result_json"]))


async def _list_ids(user_id: str) -> list[str]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT document_id FROM pipeline_results WHERE user_id = $1 ORDER BY created_at DESC",
            user_id,
        )
    return [row["document_id"] for row in rows]


@router.post(
    "/process",
    response_model=PipelineResult,
    status_code=status.HTTP_200_OK,
    summary="Upload and process a document through the full pipeline",
)
async def process_document(
    file: UploadFile = File(...),
    x_user_id: str = Header(default="unknown"),
) -> PipelineResult:
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No filename provided.")

    ext = "." + file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type '{ext}'. Allowed: {sorted(SUPPORTED_EXTENSIONS)}",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")

    result = await _processor.process(filename=file.filename, content=content)
    await _save(result, x_user_id)

    if result.status == "failed":
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=result.model_dump(),
        )
    return result


@router.post(
    "/record",
    response_model=PipelineResult,
    status_code=status.HTTP_200_OK,
    summary="Save an editor grading session to the document history without re-running the pipeline",
)
async def record_editor_result(
    req: EditorRecordRequest,
    x_user_id: str = Header(default="unknown"),
) -> PipelineResult:
    feedback_items: list[FeedbackItem] = []
    for i, r in enumerate(req.rubric or []):
        tip = req.improvement_tips[i] if i < len(req.improvement_tips) else ""
        ratio = r.score / 25.0
        severity = "info" if ratio >= 0.75 else ("warning" if ratio >= 0.5 else "error")
        feedback_items.append(FeedbackItem(
            category=_DIM_TO_CATEGORY.get(r.dimension, r.dimension),
            severity=severity,
            message=r.feedback,
            suggestion=tip,
        ))

    words = req.text.split()
    first_words = " ".join(words[:6])
    safe_name = re.sub(r'[<>:"/\\|?*\n\r]', "", first_words)[:40].strip() or "editor-draft"
    filename = f"{safe_name}.txt"

    scoring = ScoringResult(
        document_id=req.document_id,
        score=req.score,
        grade=req.grade,
        feedback=feedback_items,
        summary=req.overall_feedback,
        model_used=req.model_used,
    )
    result = PipelineResult(
        document_id=req.document_id,
        filename=filename,
        status="success",
        stage_reached=PipelineStage.COMPLETE,
        word_count=len(words),
        chunk_count=1,
        scoring=scoring,
        processing_time_ms=0.0,
    )
    await _save(result, x_user_id)
    return result


@router.get(
    "/{document_id}",
    response_model=PipelineResult,
    summary="Retrieve a previously processed document result",
)
async def get_document_result(document_id: str) -> PipelineResult:
    result = await _load(document_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No result found for document_id '{document_id}'.",
        )
    return result


@router.get("/", summary="List all processed document IDs")
async def list_documents(x_user_id: str = Header(default="unknown")) -> dict:
    ids = await _list_ids(x_user_id)
    return {"document_ids": ids, "count": len(ids)}
