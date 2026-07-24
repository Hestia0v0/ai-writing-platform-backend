"""
Compares the agents-tier result against the ai_inference fallback-tier
result for the SAME essay, run through the same LLMTestCase pair
(actual_output = agents, expected_output = ai_inference).

This is not a correctness check against a golden answer — it is a
regression guard against the two grading paths drifting so far apart that
a user perceives the fallback as an obviously worse experience.
"""
import json

from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase


class ScoreParityMetric(BaseMetric):
    def __init__(self, max_score_delta: float = 20.0) -> None:
        self.max_score_delta = max_score_delta
        self.threshold = 1.0
        self.score = 0.0
        self.success = False
        self.reason = ""

    def measure(self, test_case: LLMTestCase) -> float:
        agents_result = json.loads(test_case.actual_output)
        fallback_result = json.loads(test_case.expected_output)

        delta = abs(agents_result["score"] - fallback_result["score"])
        self.success = delta <= self.max_score_delta
        self.score = 1.0 if self.success else 0.0
        self.reason = (
            f"agents={agents_result['score']}, ai_inference={fallback_result['score']}, "
            f"delta={delta:.1f} (max allowed {self.max_score_delta})"
        )
        return self.score

    async def a_measure(self, test_case: LLMTestCase) -> float:
        return self.measure(test_case)

    def is_successful(self) -> bool:
        return self.success

    @property
    def __name__(self) -> str:
        return "Fallback Score Parity"
