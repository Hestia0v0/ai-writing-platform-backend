"""
SQLAlchemy ORM models for the ai_inference service.
Only the review_queue table lives here; all domain logic stays in core/.
"""

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, JSON, String, Text, UniqueConstraint

from db.database import Base


class ReviewQueueORM(Base):
    __tablename__ = "review_queue"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # ── Identity ──────────────────────────────────────────────────────────────
    review_id = Column(String(36), unique=True, nullable=False, index=True)
    inference_id = Column(String(36), unique=True, nullable=False, index=True)
    document_id = Column(String(255), nullable=False, index=True)

    # ── Content snapshot ──────────────────────────────────────────────────────
    text_hash = Column(String(64), nullable=False)
    text_preview = Column(Text)            # first 500 chars for quick display

    # ── AI output ─────────────────────────────────────────────────────────────
    ai_score = Column(Float, nullable=False)
    ai_confidence = Column(Float, nullable=False)
    ai_feedback = Column(Text)
    rubric_json = Column(JSON)             # list[RubricScore] serialised

    # ── Review metadata ───────────────────────────────────────────────────────
    flag_reason = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False, default="pending")

    # ── Reviewer fields (null until assigned) ─────────────────────────────────
    reviewer_id = Column(String(255), nullable=True)
    reviewer_score = Column(Float, nullable=True)
    reviewer_notes = Column(Text, nullable=True)

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    assigned_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)


class RubricDimensionORM(Base):
    """
    Admin-configurable rubric weights, read by GradingEngine (this service)
    and, via HTTP, by the agents service's Master Judge (content dimension
    only — see agents/agents/evaluation/rubric_client.py).
    """
    __tablename__ = "rubric_dimensions"
    __table_args__ = (UniqueConstraint("dimension", "language", name="uq_rubric_dim_lang"),)

    id = Column(Integer, primary_key=True, autoincrement=True)

    dimension = Column(String(50), nullable=False)     # content | organization | language | conventions
    language = Column(String(10), nullable=False, default="en")
    max_score = Column(Float, nullable=False, default=25.0)
    description = Column(Text, default="")
    display_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = Column(String(255), nullable=True)
