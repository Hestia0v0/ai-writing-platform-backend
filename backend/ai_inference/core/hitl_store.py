"""
HITLStore — data-access layer for the human-in-the-loop review queue.

State machine for a review item:
  pending ──► in_review ──► approved
                        └──► overridden
                        └──► escalated  (set manually via /hitl/{id}/escalate)

All methods are synchronous SQLAlchemy; call from async handlers via
asyncio.get_running_loop().run_in_executor() if strict non-blocking is required.
For SQLite (dev) the latency is sub-millisecond; for Postgres use asyncpg +
SQLAlchemy async in a future iteration.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from core.models import (
    FlagReason,
    GradingResult,
    ReviewDecision,
    ReviewQueueItem,
    ReviewStatus,
)
from db.models import ReviewQueueORM

logger = logging.getLogger(__name__)


class HITLStore:
    def __init__(self, db: Session) -> None:
        self._db = db

    # ── Write operations ───────────────────────────────────────────────────────

    def enqueue(self, result: GradingResult, original_text: str) -> ReviewQueueItem:
        """
        Add a grading result to the review queue.
        Idempotent: if inference_id already exists the existing row is returned.
        """
        existing = (
            self._db.query(ReviewQueueORM)
            .filter(ReviewQueueORM.inference_id == result.inference_id)
            .first()
        )
        if existing:
            return self._to_pydantic(existing)

        row = ReviewQueueORM(
            review_id=str(uuid.uuid4()),
            inference_id=result.inference_id,
            document_id=result.document_id,
            text_hash=hashlib.sha256(original_text.encode()).hexdigest(),
            text_preview=original_text[:500],
            ai_score=result.score,
            ai_confidence=result.confidence,
            ai_feedback=result.overall_feedback,
            rubric_json=[r.model_dump() for r in result.rubric],
            flag_reason=(
                result.flag_reason.value
                if result.flag_reason
                else FlagReason.LOW_CONFIDENCE.value
            ),
            status=ReviewStatus.PENDING.value,
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        logger.info(
            "Enqueued HITL review  review_id=%s inference_id=%s reason=%s",
            row.review_id,
            row.inference_id,
            row.flag_reason,
        )
        return self._to_pydantic(row)

    def assign_reviewer(
        self, review_id: str, reviewer_id: str
    ) -> ReviewQueueItem | None:
        """
        Claim an item for review.  Transitions pending → in_review.
        No-op if the item is already in another terminal state.
        """
        row = self._get_row(review_id)
        if not row:
            return None
        if row.status != ReviewStatus.PENDING.value:
            return self._to_pydantic(row)
        row.status = ReviewStatus.IN_REVIEW.value
        row.reviewer_id = reviewer_id
        row.assigned_at = datetime.utcnow()
        self._db.commit()
        self._db.refresh(row)
        return self._to_pydantic(row)

    def submit_decision(self, decision: ReviewDecision) -> ReviewQueueItem | None:
        """
        Record a reviewer's approval or score override.
        Transitions in_review → approved | overridden.
        """
        row = self._get_row(decision.review_id)
        if not row:
            return None
        row.status = (
            ReviewStatus.APPROVED.value
            if decision.approved
            else ReviewStatus.OVERRIDDEN.value
        )
        row.reviewer_id = decision.reviewer_id
        row.reviewer_score = decision.reviewer_score
        row.reviewer_notes = decision.reviewer_notes
        row.resolved_at = datetime.utcnow()
        self._db.commit()
        self._db.refresh(row)
        logger.info(
            "Review decided  review_id=%s status=%s reviewer=%s",
            row.review_id,
            row.status,
            row.reviewer_id,
        )
        return self._to_pydantic(row)

    def escalate(self, review_id: str, notes: Optional[str] = None) -> ReviewQueueItem | None:
        """Mark an item as needing senior review (escalated)."""
        row = self._get_row(review_id)
        if not row:
            return None
        row.status = ReviewStatus.ESCALATED.value
        if notes:
            row.reviewer_notes = notes
        self._db.commit()
        self._db.refresh(row)
        return self._to_pydantic(row)

    # ── Read operations ────────────────────────────────────────────────────────

    def get_item(self, review_id: str) -> ReviewQueueItem | None:
        row = self._get_row(review_id)
        return self._to_pydantic(row) if row else None

    def get_queue(
        self,
        status: Optional[ReviewStatus] = None,
        flag_reason: Optional[FlagReason] = None,
        reviewer_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ReviewQueueItem]:
        query = self._db.query(ReviewQueueORM)
        if status:
            query = query.filter(ReviewQueueORM.status == status.value)
        if flag_reason:
            query = query.filter(ReviewQueueORM.flag_reason == flag_reason.value)
        if reviewer_id:
            query = query.filter(ReviewQueueORM.reviewer_id == reviewer_id)
        rows = (
            query.order_by(ReviewQueueORM.created_at.asc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [self._to_pydantic(r) for r in rows]

    def queue_stats(self) -> dict:
        counts = (
            self._db.query(ReviewQueueORM.status, func.count(ReviewQueueORM.id))
            .group_by(ReviewQueueORM.status)
            .all()
        )
        by_status = {status: count for status, count in counts}
        return {
            "by_status": by_status,
            "total": sum(by_status.values()),
            "pending": by_status.get(ReviewStatus.PENDING.value, 0),
        }

    # ── Internal ───────────────────────────────────────────────────────────────

    def _get_row(self, review_id: str) -> ReviewQueueORM | None:
        return (
            self._db.query(ReviewQueueORM)
            .filter(ReviewQueueORM.review_id == review_id)
            .first()
        )

    @staticmethod
    def _to_pydantic(row: ReviewQueueORM) -> ReviewQueueItem:
        return ReviewQueueItem(
            review_id=row.review_id,
            inference_id=row.inference_id,
            document_id=row.document_id,
            ai_score=row.ai_score,
            ai_confidence=row.ai_confidence,
            flag_reason=FlagReason(row.flag_reason),
            status=ReviewStatus(row.status),
            text_preview=row.text_preview or "",
            ai_feedback=row.ai_feedback or "",
            reviewer_id=row.reviewer_id,
            reviewer_score=row.reviewer_score,
            reviewer_notes=row.reviewer_notes,
            created_at=row.created_at,
            resolved_at=row.resolved_at,
        )
