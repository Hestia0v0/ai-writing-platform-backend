"""
GEval metrics that judge grading quality against the essay itself.

test_case.actual_output is expected to be a JSON-serialized ScoringResult
(doc_processor.ScoringResult.model_dump_json()) — GEval reads JSON fine, and
keeping a single serialization format lets the same LLMTestCase feed both
these metrics and StructuralValidityMetric.
"""
from deepeval.metrics import GEval
from deepeval.test_case import SingleTurnParams


def grading_accuracy_metric(judge, threshold: float = 0.6) -> GEval:
    return GEval(
        name="Grading Accuracy",
        model=judge,
        threshold=threshold,
        evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT],
        evaluation_steps=[
            "The 'input' is a student essay, in English or Chinese. The 'actual output' is a "
            "JSON grading result with fields: score (0-100), grade, feedback (a list of "
            "{category, severity, message, suggestion} covering content/evidence, structure, "
            "grammar, vocabulary, and clarity/style), summary, model_used, source_tier.",
            "Judge whether 'score' and 'grade' are a fair, well-calibrated assessment of the "
            "essay's content, organization, language, and conventions.",
            "Penalize scores that are clearly too generous for an essay with obvious grammar "
            "errors, weak structure, or thin/generic content, and penalize scores that are "
            "unfairly harsh for a clearly strong, well-argued essay.",
            "Do not penalize a score merely for landing on a reasonable boundary between two "
            "adjacent letter grades.",
        ],
    )


def feedback_groundedness_metric(judge, threshold: float = 0.6) -> GEval:
    return GEval(
        name="Feedback Groundedness",
        model=judge,
        threshold=threshold,
        evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT],
        evaluation_steps=[
            "The 'actual output' is a JSON grading result for the essay in 'input', containing "
            "a 'feedback' list and a 'summary' string.",
            "Judge whether each feedback message and the summary describe something that is "
            "actually true of the specific essay in 'input' — not a generic, boilerplate, or "
            "hallucinated observation that could apply to any essay on any topic.",
            "A feedback item that names a concrete issue or strength grounded in the essay's "
            "actual wording or structure should score higher than a vague statement such as "
            "'good job' or 'needs improvement' with no supporting detail.",
        ],
    )
