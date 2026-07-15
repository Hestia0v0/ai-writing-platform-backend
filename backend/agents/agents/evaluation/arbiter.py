"""
Consistency Arbiter Agent — conditional fourth pass over the evaluation panel.

Only invoked when the four rubric sub-scores (vocab, structure, style,
content — all 0-25) disagree sharply (population stdev over
ARBITRATION_STDEV_THRESHOLD). That pattern usually means one sub-agent's
score is an outlier rather than a genuine reflection of essay quality, so a
fifth LLM call re-reads the essay against the sub-scores and either confirms
the total or adjusts it, and marks the result for human follow-up.
"""
from __future__ import annotations

import logging
import os

from pydantic import BaseModel, Field

from core.models import EvaluationResult
from agents.evaluation._base import DEFAULT_EVAL_MODEL, build_structured_chain

logger = logging.getLogger(__name__)

ARBITRATION_STDEV_THRESHOLD = float(os.getenv("ARBITRATION_STDEV_THRESHOLD", "6.0"))

_SYSTEM_PROMPT = """\
You are the final arbiter for an AI essay-grading panel (students aged 10-18). \
Four sub-scores for this essay disagreed sharply with each other, which usually \
means one of them is an outlier rather than a fair reflection of the essay's \
quality. Re-read the essay against the four sub-scores and decide whether the \
computed total should stand or be adjusted.

Respond with ONLY a valid JSON object (no markdown, no explanation):
{
  "adjusted_total_score": <number 0-100>,
  "rationale": "<1-3 sentences: which sub-score looked wrong and why, or why the disagreement is actually justified>"
}
"""


class _ArbitrationOutput(BaseModel):
    adjusted_total_score: float = Field(ge=0.0, le=100.0)
    rationale: str = ""


class ConsistencyArbiterAgent:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._model = model or os.getenv("EVAL_MODEL", DEFAULT_EVAL_MODEL)
        self._chain = build_structured_chain(
            _ArbitrationOutput, model=self._model, api_key=api_key, temperature=0.0,
        )

    async def arbitrate(self, text: str, result: EvaluationResult) -> EvaluationResult:
        if self._chain is None:
            return result.model_copy(update={
                "flagged_for_review": True,
                "flag_reason": "high_variance (mock mode — no arbitration performed)",
            })

        from langchain_core.messages import HumanMessage, SystemMessage  # noqa: PLC0415

        sub_report = (
            f"Vocabulary & Grammar: {result.vocab_grammar.raw_score}/25\n"
            f"Structure & Logic: {result.structure_logic.raw_score}/25\n"
            f"Style / Show-Don't-Tell: {result.style.raw_score}/25\n"
            f"Content & Ideas: {result.content_score}/25\n"
            f"Panel total: {result.total_score}/100"
        )
        try:
            output: _ArbitrationOutput = await self._chain.ainvoke([
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(
                    content=f"=== SUB-SCORES ===\n{sub_report}\n\n=== ESSAY ===\n{text[:4000]}"
                ),
            ])
            return result.model_copy(update={
                "total_score": round(output.adjusted_total_score, 1),
                "flagged_for_review": True,
                "flag_reason": f"high_variance: {output.rationale}",
            })
        except Exception as exc:  # noqa: BLE001
            logger.error("ConsistencyArbiterAgent LLM call failed: %s", exc)
            return result.model_copy(update={
                "flagged_for_review": True,
                "flag_reason": "high_variance (arbitration call failed — original total kept)",
            })
