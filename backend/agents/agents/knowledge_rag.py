"""
Knowledge Retrieval RAG Agent (US-10, US-11)

Role: the "librarian with a dictionary" — retrieves vocabulary, idioms,
and example sentences from the vector knowledge base rather than
generating them from scratch.

Flow:
  1. Forward the student's paragraph to the knowledge_retrieval service
     (POST /retrieval/search) which runs pgvector semantic search.
  2. Optionally re-rank / format the results with a lightweight LLM call.
  3. Return a list of VocabRecommendation items.

The knowledge_retrieval service URL is read from KNOWLEDGE_RETRIEVAL_URL
(default: http://knowledge_retrieval:8002).
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from core.models import Language, RecommendRequest, RecommendResult, VocabRecommendation

logger = logging.getLogger(__name__)

_RETRIEVAL_URL = os.getenv(
    "KNOWLEDGE_RETRIEVAL_URL", "http://knowledge_retrieval:8002"
).rstrip("/")

# Fallback curated list used when neither the retrieval service nor the API
# key is available (development / offline mode).
_MOCK_EN: list[dict[str, Any]] = [
    {
        "term": "ephemeral",
        "type": "advanced_word",
        "example": "The ephemeral beauty of cherry blossoms reminds us to cherish every moment.",
        "relevance_score": 0.85,
    },
    {
        "term": "serendipity",
        "type": "advanced_word",
        "example": "It was pure serendipity that led her to the hidden bookshop on that rainy afternoon.",
        "relevance_score": 0.80,
    },
    {
        "term": "juxtapose",
        "type": "advanced_word",
        "example": "The author juxtaposes the warmth of the fireplace with the cold indifference of the storm outside.",
        "relevance_score": 0.78,
    },
    {
        "term": "如鱼得水",
        "type": "idiom",
        "example": "他加入了这个团队后如鱼得水，才华得到了充分发挥。",
        "relevance_score": 0.75,
    },
    {
        "term": "栩栩如生",
        "type": "idiom",
        "example": "画家用寥寥数笔便把人物描绘得栩栩如生。",
        "relevance_score": 0.72,
    },
]

_MOCK_ZH: list[dict[str, Any]] = [
    {
        "term": "栩栩如生",
        "type": "idiom",
        "example": "画家用寥寥数笔便把人物描绘得栩栩如生，令观者叹为观止。",
        "relevance_score": 0.90,
    },
    {
        "term": "如鱼得水",
        "type": "idiom",
        "example": "他加入了这个团队后如鱼得水，才华得到了充分发挥。",
        "relevance_score": 0.85,
    },
    {
        "term": "惟妙惟肖",
        "type": "idiom",
        "example": "演员将人物的心理变化演绎得惟妙惟肖，赢得了满堂彩。",
        "relevance_score": 0.82,
    },
    {
        "term": "娓娓道来",
        "type": "advanced_word",
        "example": "老人娓娓道来，将那段尘封已久的历史一一呈现在我们眼前。",
        "relevance_score": 0.78,
    },
    {
        "term": "沁人心脾",
        "type": "advanced_word",
        "example": "清晨的空气沁人心脾，令人神清气爽，忘却了所有烦恼。",
        "relevance_score": 0.75,
    },
]


class KnowledgeRAGAgent:
    """
    Retrieves vocabulary recommendations via the knowledge_retrieval service.
    Falls back to a curated mock list when the service is unreachable.
    """

    def __init__(
        self,
        retrieval_url: str | None = None,
        timeout: float = 5.0,
    ) -> None:
        self._url = (retrieval_url or _RETRIEVAL_URL).rstrip("/")
        self._timeout = timeout

    async def recommend(self, request: RecommendRequest) -> RecommendResult:
        try:
            return await self._fetch_from_service(request)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "KnowledgeRAGAgent: retrieval service unreachable (%s) — using mock data", exc
            )
            return self._mock(request)

    async def _fetch_from_service(self, request: RecommendRequest) -> RecommendResult:
        payload = {
            "query": request.paragraph,
            "top_k": request.top_k,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(f"{self._url}/retrieval/search", json=payload)
            resp.raise_for_status()
            items: list[dict] = resp.json()

        recommendations = [
            VocabRecommendation(
                term=item.get("term") or item.get("content", "")[:40],
                type=item.get("type", "advanced_word"),
                example=item.get("example") or item.get("content", ""),
                relevance_score=float(item.get("score", item.get("relevance_score", 0.7))),
            )
            for item in items
            if item
        ]
        return RecommendResult(
            recommendations=recommendations,
            retrieval_source="pgvector",
        )

    @staticmethod
    def _mock(request: RecommendRequest) -> RecommendResult:
        pool = _MOCK_ZH if request.language == Language.CHINESE else _MOCK_EN
        recs = [VocabRecommendation(**item) for item in pool[: request.top_k]]
        return RecommendResult(
            recommendations=recs,
            retrieval_source="mock",
        )
