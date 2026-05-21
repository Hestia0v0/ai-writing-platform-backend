"""
Reverse proxy — forwards /api/v1/{service}/{path} to the appropriate backend.

Service routing:
  inference/*  → ai_inference:8001/inference/{path}
  batch/*      → ai_inference:8001/batch/{path}
  hitl/*       → ai_inference:8001/hitl/{path}
  retrieval/*  → knowledge_retrieval:8002/retrieval/{path}
  pipelines/*  → pipelines:8003/{path}   (the "pipelines" segment is stripped)
  agent/*      → agents:8004/agent/{path}
"""

import logging
import os

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

logger = logging.getLogger(__name__)

router = APIRouter()

_INFERENCE_URL = os.getenv("AI_INFERENCE_URL", "http://ai_inference:8001").rstrip("/")
_RETRIEVAL_URL = os.getenv("KNOWLEDGE_RETRIEVAL_URL", "http://knowledge_retrieval:8002").rstrip("/")
_PIPELINES_URL = os.getenv("PIPELINES_URL", "http://pipelines:8003").rstrip("/")
_AGENTS_URL = os.getenv("AGENTS_URL", "http://agents:8004").rstrip("/")

# (base_url, preserve_segment)
# preserve_segment=True  → forward as  base/{segment}/{path}
# preserve_segment=False → forward as  base/{path}  (pipelines: strip the prefix)
_SERVICE_MAP: dict[str, tuple[str, bool]] = {
    "inference": (_INFERENCE_URL, True),
    "batch":     (_INFERENCE_URL, True),
    "hitl":      (_INFERENCE_URL, True),
    "retrieval": (_RETRIEVAL_URL, True),
    "pipelines": (_PIPELINES_URL, False),
    "agent":     (_AGENTS_URL, True),
}

# Headers that must not be forwarded to/from the upstream.
_DROP_REQ  = frozenset({"host", "content-length", "transfer-encoding",
                         "connection", "keep-alive", "upgrade", "te", "trailers"})
_DROP_RESP = frozenset({"content-encoding", "content-length", "transfer-encoding",
                         "connection", "keep-alive", "content-type"})


async def _forward(request: Request, url: str) -> Response:
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_REQ}
    body = await request.body()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            upstream = await client.request(
                method=request.method,
                url=url,
                headers=headers,
                content=body,
                params=dict(request.query_params),
                follow_redirects=True,
            )
    except httpx.RequestError as exc:
        logger.error("Upstream unreachable: %s — %s", url, exc)
        raise HTTPException(
            status_code=503,
            detail=f"Upstream service unavailable: {exc}",
        )

    resp_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() not in _DROP_RESP
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )


@router.api_route(
    "/{service}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
async def proxy(service: str, path: str, request: Request) -> Response:
    entry = _SERVICE_MAP.get(service)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown service '{service}'. Valid prefixes: {sorted(_SERVICE_MAP)}",
        )

    base, preserve = entry
    if preserve:
        target = f"{base}/{service}/{path}" if path else f"{base}/{service}"
    else:
        target = f"{base}/{path}" if path else base

    return await _forward(request, target)
