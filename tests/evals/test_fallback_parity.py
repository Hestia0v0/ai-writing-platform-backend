"""
Runs the SAME essay through both grading tiers directly (bypassing
score_document()'s short-circuit) and checks the two results don't diverge
so far that the fallback would feel like an obviously worse experience.

This is a regression guard, not a correctness check — there is no "right"
answer being asserted, only "these two paths shouldn't drift too far apart."
"""
import asyncio

import pytest

from conftest import load_goldens
from metrics.fallback_parity import ScoreParityMetric
from deepeval.test_case import LLMTestCase

pytestmark = pytest.mark.eval


@pytest.mark.parametrize("golden", load_goldens(), ids=lambda g: g["id"])
def test_agents_and_ai_inference_score_parity(golden, grading_client):
    async def run():
        agents_result = await grading_client._call_agents(
            document_id=golden["id"], text=golden["text"]
        )
        ai_inference_result = await grading_client._call_ai_inference(
            document_id=golden["id"],
            text=golden["text"],
            word_count=len(golden["text"].split()),
        )
        return agents_result, ai_inference_result

    agents_result, ai_inference_result = asyncio.run(run())

    test_case = LLMTestCase(
        input=golden["text"],
        actual_output=agents_result.model_dump_json(),
        expected_output=ai_inference_result.model_dump_json(),
    )

    metric = ScoreParityMetric()
    metric.measure(test_case)
    assert metric.is_successful(), metric.reason
