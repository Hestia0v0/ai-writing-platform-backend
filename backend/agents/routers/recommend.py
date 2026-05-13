"""
POST /agent/recommend
"""
from fastapi import APIRouter, Depends

from core.models import RecommendRequest, RecommendResult
from agents.knowledge_rag import KnowledgeRAGAgent
from dependencies import get_knowledge_rag

router = APIRouter()


@router.post(
    "/recommend",
    response_model=RecommendResult,
    summary="Vocabulary & Idiom Recommendations (RAG)",
)
async def recommend(
    request: RecommendRequest,
    agent: KnowledgeRAGAgent = Depends(get_knowledge_rag),
) -> RecommendResult:
    """
    Retrieve vocabulary, idioms, and example sentences relevant to the
    student's current paragraph using **RAG (Retrieval-Augmented Generation)**.

    The agent embeds the paragraph and performs a **semantic search** against
    the pgvector knowledge base — it does NOT generate content from scratch.

    Response includes:
    - **recommendations**: ranked list of `{term, type, example, relevance_score}`
    - **retrieval_source**: `pgvector` (live) or `mock` (fallback)

    Set `language` to `zh` for Chinese idiom recommendations or `en` for
    English advanced vocabulary.
    """
    return await agent.recommend(request)
