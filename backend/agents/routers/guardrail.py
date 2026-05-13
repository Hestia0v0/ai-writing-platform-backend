"""
POST /agent/guardrail
"""
from fastapi import APIRouter, Depends

from core.models import GuardrailRequest, GuardrailResult
from agents.guardrail import GuardrailAgent
from dependencies import get_guardrail

router = APIRouter()


@router.post("/guardrail", response_model=GuardrailResult, summary="Security Guardrail Screen")
async def guardrail(
    request: GuardrailRequest,
    agent: GuardrailAgent = Depends(get_guardrail),
) -> GuardrailResult:
    """
    Screen user input for prompt injection, jailbreak attempts, and harmful content.

    All text (typed prompts or extracted document text) must pass through this
    endpoint before any other agent processes it.

    - **passed**: `true` → safe to proceed; `false` → reject and show `reason`.
    - **risk_level**: `none | low | medium | high`
    - **categories**: which rules were triggered
    - Target latency: **< 500 ms**
    """
    return await agent.screen(request)
