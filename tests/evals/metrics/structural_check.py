"""
Non-LLM, deterministic structural validation for a ScoringResult.

Runs alongside the GEval metrics inside the same deepeval test case so CI
output shows both "is this well-formed" and "is this good grading" in one
report, but this metric never calls an LLM and is free/instant to run.
"""
import json

from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase

_VALID_GRADES = {"A", "B", "C", "D", "F"}
_VALID_SEVERITIES = {"info", "warning", "error"}
_LLM_TIER_CATEGORIES = {"evidence", "structure", "grammar", "vocabulary", "clarity"}


class StructuralValidityMetric(BaseMetric):
    """
    Expects test_case.actual_output to be a JSON-serialized ScoringResult
    (doc_processor.ScoringResult.model_dump_json()).
    """

    def __init__(self, threshold: float = 1.0) -> None:
        self.threshold = threshold
        self.score = 0.0
        self.success = False
        self.reason = ""

    def measure(self, test_case: LLMTestCase) -> float:
        data = json.loads(test_case.actual_output)
        problems: list[str] = []

        score = data.get("score")
        if score is None or not (0.0 <= score <= 100.0):
            problems.append(f"score {score!r} out of range [0, 100]")

        grade = data.get("grade")
        if grade not in _VALID_GRADES:
            problems.append(f"unexpected grade {grade!r}")

        feedback = data.get("feedback", [])
        if not feedback:
            problems.append("feedback list is empty")

        for item in feedback:
            if item.get("severity") not in _VALID_SEVERITIES:
                problems.append(f"invalid severity {item.get('severity')!r}")

        if data.get("source_tier") in ("agents", "ai_inference"):
            categories = {item.get("category") for item in feedback}
            missing = _LLM_TIER_CATEGORIES - categories
            if missing:
                problems.append(f"missing feedback categories: {sorted(missing)}")

        self.success = not problems
        self.score = 1.0 if self.success else 0.0
        self.reason = "structurally valid" if self.success else "; ".join(problems)
        return self.score

    async def a_measure(self, test_case: LLMTestCase) -> float:
        return self.measure(test_case)

    def is_successful(self) -> bool:
        return self.success

    @property
    def __name__(self) -> str:
        return "Structural Validity"
