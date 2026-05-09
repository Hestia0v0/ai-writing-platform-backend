"""
SQLAlchemy ORM models for the ai_inference service.
Only the review_queue table lives here; all domain logic stays in core/.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, JSON, String, Text

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
