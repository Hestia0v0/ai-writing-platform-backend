"""
Shared domain models for the ai_inference service.
All Pydantic v2 models — no ORM dependencies here.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────────────

class ReviewStatus(str, Enum):
    PENDING = "pending"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    OVERRIDDEN = "overridden"
    ESCALATED = "escalated"


class FlagReason(str, Enum):
    LOW_CONFIDENCE = "low_confidence"   # model confidence < threshold
    EDGE_SCORE = "edge_score"           # score falls in ambiguous 45–65 band
    HIGH_VARIANCE = "high_variance"     # rubric dimensions disagree strongly
    MANUAL = "manual"                   # caller explicitly requested review


class RubricDimension(str, Enum):
    CONTENT = "content"
    ORGANIZATION = "organization"
    LANGUAGE = "language"
    CONVENTIONS = "conventions"


# ── Grading Models ─────────────────────────────────────────────────────────────

class RubricScore(BaseModel):
    dimension: RubricDimension
    score: float = Field(ge=0.0, le=25.0)
    max_score: float = 25.0
    feedback: str


class GradingResult(BaseModel):
    inference_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    document_id: str
    score: float = Field(ge=0.0, le=100.0)
    grade: str
    confidence: float = Field(ge=0.0, le=1.0)
    rubric: list[RubricScore]
    overall_feedback: str
    improvement_tips: list[str]
    model_used: str
    cached: bool = False
    cache_hit_similarity: Optional[float] = None
    tokens_used: int = 0
    flagged_for_review: bool = False
    flag_reason: Optional[FlagReason] = None
    review_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ── Single Inference Request ───────────────────────────────────────────────────

class InferenceRequest(BaseModel):
    document_id: str
    text: str = Field(min_length=10, description="Raw student composition text")
    model: str = "deepseek-v4-flash"
    force_review: bool = Field(
        default=False,
        description="Force HITL review regardless of confidence score",
    )


# ── Batch Models ───────────────────────────────────────────────────────────────

class CompositionItem(BaseModel):
    composition_id: str
    document_id: str
    text: str = Field(min_length=10)


class BatchSubmitRequest(BaseModel):
    job_id: str
    compositions: list[CompositionItem] = Field(min_length=1)
    model: str = "deepseek-v4-flash"
    concurrency: int = Field(default=5, ge=1, le=20)


class BatchItemResult(BaseModel):
    composition_id: str
    document_id: str
    status: str   # "success" | "cached" | "failed"
    result: Optional[GradingResult] = None
    error: Optional[str] = None


class BatchJobStatus(BaseModel):
    job_id: str
    status: str   # "queued" | "running" | "completed" | "failed"
    total: int
    completed: int
    cached_hits: int
    flagged_for_review: int
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    results: list[BatchItemResult] = Field(default_factory=list)


# ── HITL Models ────────────────────────────────────────────────────────────────

class ReviewQueueItem(BaseModel):
    review_id: str
    inference_id: str
    document_id: str
    ai_score: float
    ai_confidence: float
    flag_reason: FlagReason
    status: ReviewStatus
    text_preview: str
    ai_feedback: str
    reviewer_id: Optional[str] = None
    reviewer_score: Optional[float] = None
    reviewer_notes: Optional[str] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None


class ReviewDecision(BaseModel):
    review_id: str
    approved: bool
    reviewer_id: str
    reviewer_score: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    reviewer_notes: Optional[str] = None
