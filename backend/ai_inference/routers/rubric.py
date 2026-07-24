"""
Rubric configuration endpoints.

GET  /rubric/dimensions   Public read — grading itself reads this (via
                          core/rubric_store.get_cached_dimensions), and the
                          agents service polls it over HTTP for its
                          content-dimension weight (agents has no direct DB
                          access by design — see agents/.../rubric_client.py).
PUT  /rubric/dimensions   admin/super_admin only — bulk-replaces the active
                          weight set; weights must sum to 100, matching the
                          frontend Rubric Management page's slider UX.
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from auth import require_role
from core.models import RubricDimension, RubricDimensionConfig, RubricDimensionsUpdateRequest
from core.rubric_store import RubricStore
from dependencies import get_rubric_store

router = APIRouter()

_WEIGHT_SUM_TOLERANCE = 0.01


@router.get("/dimensions", response_model=list[RubricDimensionConfig])
async def get_dimensions(
    language: str = Query(default="en"),
    rubric_store: RubricStore = Depends(get_rubric_store),
) -> list[RubricDimensionConfig]:
    return rubric_store.list_dimensions(language)


@router.put(
    "/dimensions",
    response_model=list[RubricDimensionConfig],
    dependencies=[Depends(require_role("admin", "super_admin"))],
)
async def update_dimensions(
    body: RubricDimensionsUpdateRequest,
    x_user_id: str = Header(default="unknown"),
    rubric_store: RubricStore = Depends(get_rubric_store),
) -> list[RubricDimensionConfig]:
    submitted = {d.dimension for d in body.dimensions}
    expected = set(RubricDimension)
    if submitted != expected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Must provide exactly these dimensions: {sorted(d.value for d in expected)}.",
        )

    total = sum(d.max_score for d in body.dimensions)
    if abs(total - 100.0) > _WEIGHT_SUM_TOLERANCE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Dimension weights must sum to 100 (got {total}).",
        )

    return rubric_store.replace_dimensions(body.language, body.dimensions, updated_by=x_user_id)
