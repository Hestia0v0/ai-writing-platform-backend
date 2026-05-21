"""
POST /agent/generate
"""
import logging

from fastapi import APIRouter, Depends

from core.models import DraftRequest, DraftResult, RecommendRequest
from agents.drafting import DraftingAgent
from agents.knowledge_rag import KnowledgeRAGAgent
from dependencies import get_drafting, get_knowledge_rag

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/generate", response_model=DraftResult, summary="Generate Essay Draft")
async def generate(
    request: DraftRequest,
    agent: DraftingAgent = Depends(get_drafting),
    rag: KnowledgeRAGAgent = Depends(get_knowledge_rag),
) -> DraftResult:
    """
    Generate a complete essay based on title, language, word count, and framework.

    Supported frameworks:
    - `five_paragraph` — Introduction → 3 Body paragraphs → Conclusion
    - `peel` — Point → Evidence → Explanation → Link (per paragraph)
    - `qczh` — 起承转合 (Chinese classical structure)
    - `argument_counter` — Argument → Counterargument → Rebuttal

    Set `language` to `zh` for Simplified Chinese or `en` for English.

    **Phrase recommendation integration (US-6):** when `phrase_hints` is omitted
    (or null) the endpoint automatically queries the Knowledge RAG agent with the
    essay title and injects the top-5 vocabulary / idiom recommendations into the
    generation prompt.  You may also supply your own list via `phrase_hints`.
    """
    # Auto-fetch phrase recommendations when the caller has not supplied any (US-6)
    if request.phrase_hints is None:
        try:
            rec_result = await rag.recommend(
                RecommendRequest(
                    paragraph=request.title,
                    language=request.language,
                    top_k=5,
                )
            )
            hints = [r.term for r in rec_result.recommendations if r.term]
            if hints:
                request = request.model_copy(update={"phrase_hints": hints})
                logger.info(
                    "Auto phrase-hints injected from RAG (%s): %s",
                    rec_result.retrieval_source,
                    hints,
                )
        except Exception:  # noqa: BLE001
            logger.warning("RAG phrase-hint fetch failed; proceeding without hints")

    return await agent.draft(request)
