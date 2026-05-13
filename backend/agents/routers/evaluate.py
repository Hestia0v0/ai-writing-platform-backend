"""
POST /agent/evaluate
"""
from fastapi import APIRouter, Depends

from core.models import EvaluationRequest, EvaluationResult
from agents.evaluation import EvaluationPanel
from dependencies import get_evaluation_panel

router = APIRouter()


@router.post("/evaluate", response_model=EvaluationResult, summary="Evaluate & Score Essay")
async def evaluate(
    request: EvaluationRequest,
    panel: EvaluationPanel = Depends(get_evaluation_panel),
) -> EvaluationResult:
    """
    Run the full **multi-agent evaluation panel** on a student essay.

    Internally, three specialist sub-agents run **concurrently** (asyncio):

    1. **Vocabulary & Grammar Agent** — errors, richness, score/25
    2. **Structure & Logic Agent** — intro/conclusion, topic adherence, score/25
    3. **Show-Don't-Tell Style Agent** — tell sentences, descriptive quality, score/25
    4. **Master Judge** — adds Content/Ideas (score/25) and produces the final
       verdict: total score 0–100, grade, strengths, weaknesses, evidence, suggestions.

    Target latency: **< 8 seconds** (parallel LLM calls).

    The `document_id` is echoed back in the response for correlation.
    """
    return await panel.evaluate(request)
