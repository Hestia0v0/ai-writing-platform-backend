"""
Fallback grading path: agents is forced down, ai_inference must pick up the
work. Runs through GradingClient.score_document() (not the internal
_call_ai_inference directly) so the actual fallback control flow — the part
that changed when agents became the primary tier — is what gets exercised,
against a live ai_inference:8001 service.

The quality bar here is intentionally lower than the agents-primary suite
(test_orchestrator_primary.py): ai_inference is a simpler single-call
grader and is expected to be less rich, just not broken.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from deepeval import assert_test
from deepeval.test_case import LLMTestCase

from conftest import essay_chunks, load_goldens
from judge_model import get_judge
from metrics.rubric_geval import grading_accuracy_metric
from metrics.structural_check import StructuralValidityMetric

pytestmark = pytest.mark.eval

_LOWER_BAR_THRESHOLD = 0.4  # below the primary path's 0.6 — "not broken", not "as rich as agents"


@pytest.mark.parametrize("golden", load_goldens(), ids=lambda g: g["id"])
def test_ai_inference_fallback_quality(golden, grading_client):
    async def run():
        with patch.object(
            grading_client, "_call_agents",
            AsyncMock(side_effect=httpx.RequestError("agents forced down for fallback test")),
        ):
            return await grading_client.score_document(
                document_id=golden["id"],
                chunks=essay_chunks(golden["text"]),
                word_count=len(golden["text"].split()),
            )

    result = asyncio.run(run())
    assert result.source_tier == "ai_inference", (
        f"expected the ai_inference fallback to serve {golden['id']} once agents was forced "
        f"down; got {result.source_tier} — is ai_inference (port 8001) up and reachable?"
    )

    test_case = LLMTestCase(
        input=f"[language={golden['language']}]\n{golden['text']}",
        actual_output=result.model_dump_json(),
    )
    judge = get_judge()
    assert_test(
        test_case,
        [
            StructuralValidityMetric(),
            grading_accuracy_metric(judge, threshold=_LOWER_BAR_THRESHOLD),
        ],
    )
