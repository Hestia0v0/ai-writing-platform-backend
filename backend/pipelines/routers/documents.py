import json
import os

from fastapi import APIRouter, HTTPException, UploadFile, File, status
from fastapi.responses import JSONResponse

from doc_processor import (
    AIInferenceClient,
    DocumentProcessor,
    PipelineResult,
    SUPPORTED_EXTENSIONS,
)
from db import get_pool

router = APIRouter()

_USE_MOCK = os.getenv("AI_INFERENCE_MOCK", "false").lower() == "true"
_INFERENCE_URL = os.getenv("AI_INFERENCE_URL", "http://ai_inference:8001")

_processor = DocumentProcessor(
    inference_client=AIInferenceClient(
        base_url=_INFERENCE_URL,
        use_mock=_USE_MOCK,
    )
)


async def _save(result: PipelineResult) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pipeline_results (document_id, filename, status, result_json)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (document_id) DO UPDATE
                SET filename    = EXCLUDED.filename,
                    status      = EXCLUDED.status,
                    result_json = EXCLUDED.result_json
            """,
            result.document_id,
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


async def _list_ids() -> list[str]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT document_id FROM pipeline_results ORDER BY created_at DESC"
        )
    return [row["document_id"] for row in rows]


@router.post(
    "/process",
    response_model=PipelineResult,
    status_code=status.HTTP_200_OK,
    summary="Upload and process a document through the full pipeline",
)
async def process_document(file: UploadFile = File(...)) -> PipelineResult:
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
    await _save(result)

    if result.status == "failed":
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=result.model_dump(),
        )
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
async def list_documents() -> dict:
    ids = await _list_ids()
    return {"document_ids": ids, "count": len(ids)}
