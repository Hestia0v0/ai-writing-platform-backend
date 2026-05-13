"""
POST /agent/refine
"""
from fastapi import APIRouter, Depends

from core.models import RefinementRequest, RefinementResult
from agents.refinement import RefinementAgent
from dependencies import get_refinement

router = APIRouter()


@router.post("/refine", response_model=RefinementResult, summary="Polish & Refine Essay")
async def refine(
    request: RefinementRequest,
    agent: RefinementAgent = Depends(get_refinement),
) -> RefinementResult:
    """
    Polish a student essay while **preserving the student's voice and meaning**.

    Pass in the `weaknesses` and `suggestions` lists from a prior `/evaluate`
    response so the agent knows exactly what to target.

    Response includes:
    - **refined_text**: the full polished essay
    - **diff_hunks**: sentence-level diff showing what changed and why
      (use this to build a diff/track-changes UI)

    The agent will NEVER rewrite the essay from scratch.
    """
    return await agent.refine(request)
