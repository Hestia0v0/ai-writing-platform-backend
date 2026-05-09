"""
Human-in-the-loop (HITL) review queue endpoints.

GET  /hitl/queue                  List review items (filterable)
GET  /hitl/stats                  Queue statistics
GET  /hitl/{review_id}            Retrieve a single review item
POST /hitl/{review_id}/assign     Claim an item for review
POST /hitl/{review_id}/escalate   Escalate to senior reviewer
POST /hitl/decide                 Submit a reviewer's decision
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from core.hitl_store import HITLStore
from core.models import (
    FlagReason,
    ReviewDecision,
    ReviewQueueItem,
    ReviewStatus,
)
from dependencies import get_hitl_store

router = APIRouter()


@router.get(
    "/queue",
    response_model=list[ReviewQueueItem],
    summary="List items in the human review queue",
)
async def review_queue(
    review_status: Optional[ReviewStatus] = Query(
        default=None, alias="status", description="Filter by review status"
    ),
    flag_reason: Optional[FlagReason] = Query(
        default=None, description="Filter by flag reason"
    ),
    reviewer_id: Optional[str] = Query(
        default=None, description="Filter by assigned reviewer"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    hitl_store: HITLStore = Depends(get_hitl_store),
) -> list[ReviewQueueItem]:
    """
    Returns review queue items, newest first.
    Defaults to all statuses; filter by `status=pending` to see unassigned work.
    """
    return hitl_store.get_queue(
        status=review_status,
        flag_reason=flag_reason,
        reviewer_id=reviewer_id,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/stats",
    summary="Review queue statistics broken down by status",
)
async def queue_stats(
    hitl_store: HITLStore = Depends(get_hitl_store),
) -> dict:
    return hitl_store.queue_stats()


@router.get(
    "/{review_id}",
    response_model=ReviewQueueItem,
    summary="Retrieve a single review item",
)
async def get_review_item(
    review_id: str,
    hitl_store: HITLStore = Depends(get_hitl_store),
) -> ReviewQueueItem:
    item = hitl_store.get_item(review_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Review item '{review_id}' not found.",
        )
    return item


@router.post(
    "/{review_id}/assign",
    response_model=ReviewQueueItem,
    summary="Claim a pending review item for a specific reviewer",
)
async def assign_reviewer(
    review_id: str,
    reviewer_id: str = Query(..., description="ID of the reviewer claiming this item"),
    hitl_store: HITLStore = Depends(get_hitl_store),
) -> ReviewQueueItem:
    """
    Transitions the item from `pending` → `in_review` and records the reviewer.
    Idempotent: returns the current state if already assigned.
    """
    item = hitl_store.assign_reviewer(review_id, reviewer_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Review item '{review_id}' not found.",
        )
    return item


@router.post(
    "/{review_id}/escalate",
    response_model=ReviewQueueItem,
    summary="Escalate a review item to a senior reviewer",
)
async def escalate(
    review_id: str,
    notes: Optional[str] = Query(default=None),
    hitl_store: HITLStore = Depends(get_hitl_store),
) -> ReviewQueueItem:
    item = hitl_store.escalate(review_id, notes)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Review item '{review_id}' not found.",
        )
    return item


@router.post(
    "/decide",
    response_model=ReviewQueueItem,
    summary="Submit a reviewer's approval or score override",
)
async def submit_decision(
    decision: ReviewDecision,
    hitl_store: HITLStore = Depends(get_hitl_store),
) -> ReviewQueueItem:
    """
    Set `approved=true` to accept the AI score unchanged.
    Set `approved=false` and provide `reviewer_score` to override the AI grade.
    In both cases, the item transitions to `approved` or `overridden` and is
    removed from the active review queue.
    """
    item = hitl_store.submit_decision(decision)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Review item '{decision.review_id}' not found.",
        )
    return item
