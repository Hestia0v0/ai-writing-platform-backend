import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional

from db import get_pool
from embedder import embed

router = APIRouter()


class IndexRequest(BaseModel):
    document_id: str
    content: str
    metadata: Optional[dict] = None


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    filters: Optional[dict] = None


class SearchResult(BaseModel):
    document_id: str
    score: float
    snippet: str
    metadata: Optional[dict] = None


class TechniqueResult(BaseModel):
    title: str
    content: str
    score: float


@router.post("/index")
async def index_document(request: IndexRequest):
    vector = await embed(request.content)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO document_embeddings (document_id, content, embedding, metadata)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (document_id) DO UPDATE
                SET content   = EXCLUDED.content,
                    embedding = EXCLUDED.embedding,
                    metadata  = EXCLUDED.metadata
            """,
            request.document_id,
            request.content,
            np.array(vector),
            request.metadata or {},
        )
    return {"document_id": request.document_id, "status": "indexed", "vector_db": "pgvector"}


@router.post("/search", response_model=List[SearchResult])
async def semantic_search(request: SearchRequest):
    query_vector = await embed(request.query)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT document_id,
                   content,
                   1 - (embedding <=> $1) AS score,
                   metadata
            FROM   document_embeddings
            ORDER  BY embedding <=> $1
            LIMIT  $2
            """,
            np.array(query_vector),
            request.top_k,
        )
    return [
        SearchResult(
            document_id=row["document_id"],
            score=float(row["score"]),
            snippet=row["content"][:300],
            metadata=dict(row["metadata"]) if row["metadata"] else None,
        )
        for row in rows
    ]


@router.get("/techniques", response_model=List[TechniqueResult])
async def search_techniques(query: str = "writing"):
    query_vector = await embed(query)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT document_id,
                   content,
                   1 - (embedding <=> $1) AS score,
                   metadata
            FROM   document_embeddings
            WHERE  metadata->>'type' = 'technique'
            ORDER  BY embedding <=> $1
            LIMIT  5
            """,
            np.array(query_vector),
        )
    return [
        TechniqueResult(
            title=dict(row["metadata"]).get("title", row["document_id"]),
            content=row["content"],
            score=float(row["score"]),
        )
        for row in rows
    ]


@router.delete("/index/{document_id}")
async def delete_document(document_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM document_embeddings WHERE document_id = $1",
            document_id,
        )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail=f"Document '{document_id}' not found.")
    return {"document_id": document_id, "status": "deleted"}
