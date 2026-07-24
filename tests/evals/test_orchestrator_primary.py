"""
Primary grading path: the agents multi-agent evaluation panel, healthy.

Exercises GradingClient.score_document() end-to-end against a live
agents:8004 service (no mocking) — this is what real users get when the
panel is up, which is the common case now that agents is the primary tier
and ai_inference is only the fallback.
"""
import asyncio

import pytest
from deepeval import assert_test
from deepeval.test_case import LLMTestCase

from conftest import TIER_SANITY_BANDS, essay_chunks, load_goldens
from judge_model import get_judge
from metrics.rubric_geval import feedback_groundedness_metric, grading_accuracy_metric
from metrics.structural_check import StructuralValidityMetric

pytestmark = pytest.mark.eval


@pytest.mark.parametrize("golden", load_goldens(), ids=lambda g: g["id"])
def test_agents_primary_grading_quality(golden, grading_client):
    result = asyncio.run(
        grading_client._call_agents(document_id=golden["id"], text=golden["text"])
    )
    assert result.source_tier == "agents", (
        f"expected the agents tier to serve {golden['id']}; got {result.source_tier} — "
        "is the agents service (port 8004) up and reachable?"
    )

    lo, hi = TIER_SANITY_BANDS[golden["tier"]]
    assert lo <= result.score <= hi, (
        f"{golden['id']} ({golden['tier']}) scored {result.score}, outside the loose "
        f"gross-sanity band [{lo}, {hi}] — likely a grading regression, not essay-quality noise"
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
            grading_accuracy_metric(judge),
            feedback_groundedness_metric(judge),
        ],
    )
