"""
GradingEngine — calls the DeepSeek API (OpenAI-compatible) to produce
structured, rubric-aligned evaluations.

Key design choices
──────────────────
• AsyncOpenAI client pointing at DeepSeek's base URL; grade() is a coroutine.
• Structured output via JSON mode: the system prompt instructs the model to
  return a strict JSON object. DeepSeek reasoner models do not support
  tool_choice, so we use response_format={"type":"json_object"} instead.
• Graceful degradation: no API key → deterministic mock; API error → mock.
  The mock score is derived from an MD5 hash of the text so it is reproducible
  across repeated calls for the same content.
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from datetime import datetime

from core.models import (
    FlagReason,
    GradingResult,
    RubricDimension,
    RubricScore,
)

logger = logging.getLogger(__name__)

# ── Thresholds (overridable via env) ──────────────────────────────────────────

CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.75"))
EDGE_SCORE_LOW = float(os.getenv("EDGE_SCORE_LOW", "45.0"))
EDGE_SCORE_HIGH = float(os.getenv("EDGE_SCORE_HIGH", "65.0"))

_GRADE_MAP: list[tuple[float, str]] = [
    (90.0, "A"),
    (80.0, "B"),
    (70.0, "C"),
    (60.0, "D"),
]

# ── Prompt Definition ─────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are an expert academic writing evaluator with 15 years of university-level \
grading experience. Your evaluations are objective, consistent, constructive, \
and free of demographic bias.

Rubric (25 points each, 100 total):
1. Content & Ideas       – argument clarity, analytical depth, evidence relevance
2. Organization & Structure – logical flow, paragraph cohesion, intro/conclusion
3. Language & Style      – vocabulary richness, sentence variety, academic register
4. Grammar & Conventions – spelling, punctuation, syntax accuracy

Confidence guidelines:
• 0.90–1.00  Clear-cut case (exemplary or clearly failing work)
• 0.75–0.89  Solid evaluation with minor ambiguity
• 0.50–0.74  Borderline — human review recommended
• < 0.50     Text is too short, off-topic, or unintelligible

Set flag_for_review=true when:
  – confidence < 0.75, OR
  – overall score is between 45 and 65 (pass/fail boundary zone)

You MUST respond with a single valid JSON object and nothing else. \
No markdown fences, no explanation outside the JSON. \
Required schema:
{
  "overall_score": <number 0-100>,
  "confidence": <number 0.0-1.0>,
  "rubric_scores": [
    {"dimension": "content",       "score": <0-25>, "feedback": "<1-2 sentences>"},
    {"dimension": "organization",  "score": <0-25>, "feedback": "<1-2 sentences>"},
    {"dimension": "language",      "score": <0-25>, "feedback": "<1-2 sentences>"},
    {"dimension": "conventions",   "score": <0-25>, "feedback": "<1-2 sentences>"}
  ],
  "overall_feedback": "<2-3 sentence holistic assessment>",
  "improvement_tips": ["<tip 1>", "<tip 2>", "<tip 3>"],
  "flag_for_review": <true|false>
}\
"""


# ── Helper functions ───────────────────────────────────────────────────────────

def _score_to_grade(score: float) -> str:
    for threshold, grade in _GRADE_MAP:
        if score >= threshold:
            return grade
    return "F"


def _auto_flag_reason(score: float, confidence: float) -> FlagReason | None:
    if confidence < CONFIDENCE_THRESHOLD:
        return FlagReason.LOW_CONFIDENCE
    if EDGE_SCORE_LOW <= score <= EDGE_SCORE_HIGH:
        return FlagReason.EDGE_SCORE
    return None


# ── Engine ─────────────────────────────────────────────────────────────────────

DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class GradingEngine:
    """
    Async grading engine backed by the DeepSeek API (OpenAI-compatible).
    Instantiate once at application startup via the DI layer.
    """

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "deepseek-v4-flash",
    ) -> None:
        self._default_model = default_model
        self._api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        self._client = None
        if self._api_key:
            try:
                from openai import AsyncOpenAI  # noqa: PLC0415
                self._client = AsyncOpenAI(
                    api_key=self._api_key,
                    base_url=DEEPSEEK_BASE_URL,
                )
            except ImportError:
                logger.warning("openai package not installed — mock mode active")

    async def grade(
        self,
        document_id: str,
        text: str,
        model: str | None = None,
        force_review: bool = False,
    ) -> GradingResult:
        if self._client is None:
            logger.debug("No DeepSeek client — using mock grader")
            return self._mock_grade(document_id, text, force_review)

        try:
            return await self._live_grade(document_id, text, model, force_review)
        except Exception as exc:   # noqa: BLE001
            # DeepSeek API error (rate limit, auth, network) falls back to mock
            # so the pipeline can continue degraded rather than fail completely.
            logger.error("DeepSeek call failed (%s: %s) — falling back to mock", type(exc).__name__, exc)
            return self._mock_grade(document_id, text, force_review)

    async def _live_grade(
        self,
        document_id: str,
        text: str,
        model: str | None,
        force_review: bool,
    ) -> GradingResult:
        import json  # noqa: PLC0415
        import re   # noqa: PLC0415

        used_model = model or self._default_model
        inference_id = str(uuid.uuid4())

        response = await self._client.chat.completions.create(
            model=used_model,
            max_tokens=2048,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Please grade the following student composition:\n\n{text}",
                },
            ],
        )

        raw = response.choices[0].message.content or ""
        # Strip accidental markdown fences if the model wraps output
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
        data: dict = json.loads(raw)
        score = float(data["overall_score"])
        confidence = float(data["confidence"])

        rubric = [
            RubricScore(
                dimension=RubricDimension(r["dimension"]),
                score=float(r["score"]),
                feedback=r["feedback"],
            )
            for r in data["rubric_scores"]
        ]

        auto_reason = _auto_flag_reason(score, confidence)
        flagged = force_review or data.get("flag_for_review", False) or auto_reason is not None
        flag_reason = FlagReason.MANUAL if force_review else auto_reason

        return GradingResult(
            inference_id=inference_id,
            document_id=document_id,
            score=round(score, 1),
            grade=_score_to_grade(score),
            confidence=round(confidence, 3),
            rubric=rubric,
            overall_feedback=data["overall_feedback"],
            improvement_tips=data["improvement_tips"],
            model_used=used_model,
            tokens_used=(
                response.usage.prompt_tokens + response.usage.completion_tokens
            ),
            flagged_for_review=flagged,
            flag_reason=flag_reason,
            created_at=datetime.utcnow(),
        )

    def _mock_grade(
        self, document_id: str, text: str, force_review: bool = False
    ) -> GradingResult:
        """
        Deterministic mock: score is derived from an MD5 hash of the first
        200 characters, giving a stable result for the same text across runs.
        Range is bounded to 55–94 to exercise a variety of grades in tests.
        """
        seed = int(hashlib.md5(text[:200].encode()).hexdigest(), 16) % 100
        score = round(55.0 + (seed % 40), 1)
        confidence = round(0.60 + (seed % 35) / 100, 3)

        per_dim = round(score / 4, 1)
        rubric = [
            RubricScore(
                dimension=dim,
                score=per_dim,
                feedback=f"[mock] {dim.value} evaluation placeholder.",
            )
            for dim in RubricDimension
        ]

        auto_reason = _auto_flag_reason(score, confidence)
        flagged = force_review or auto_reason is not None
        flag_reason = FlagReason.MANUAL if force_review else auto_reason

        return GradingResult(
            inference_id=str(uuid.uuid4()),
            document_id=document_id,
            score=score,
            grade=_score_to_grade(score),
            confidence=confidence,
            rubric=rubric,
            overall_feedback=(
                "[mock] Set DEEPSEEK_API_KEY to enable real AI grading. "
                "This mock score is deterministic based on text content."
            ),
            improvement_tips=[
                "Add more specific evidence to support your claims.",
                "Strengthen the thesis statement in the introduction.",
                "Vary sentence length to improve rhythm and readability.",
            ],
            model_used="mock",
            tokens_used=0,
            flagged_for_review=flagged,
            flag_reason=flag_reason,
            created_at=datetime.utcnow(),
        )
