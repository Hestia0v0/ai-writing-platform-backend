"""
RubricStore — data-access layer for admin-configurable rubric weights.

Two access patterns live here:
  RubricStore(db)   — request-scoped CRUD for the rubric router (admin writes).
  get_cached_dimensions() / invalidate_cache()
                    — used by GradingEngine, which is a process-wide singleton
                      (no per-request DB session). Reads are cached for
                      _CACHE_TTL_SECONDS so grading doesn't pay a DB round
                      trip on every call; a PUT invalidates the cache
                      immediately so edits are visible on the next grade().
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from core.models import RubricDimensionConfig, RubricDimensionInput
from db.database import SessionLocal
from db.models import RubricDimensionORM

_CACHE_TTL_SECONDS = 30.0
_cache: dict[str, tuple[float, list[RubricDimensionConfig]]] = {}


def _to_pydantic(row: RubricDimensionORM) -> RubricDimensionConfig:
    return RubricDimensionConfig(
        dimension=row.dimension,
        language=row.language,
        max_score=row.max_score,
        description=row.description or "",
        display_order=row.display_order,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
    )


class RubricStore:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list_dimensions(self, language: str = "en") -> list[RubricDimensionConfig]:
        rows = (
            self._db.query(RubricDimensionORM)
            .filter(RubricDimensionORM.language == language, RubricDimensionORM.is_active.is_(True))
            .order_by(RubricDimensionORM.display_order.asc())
            .all()
        )
        return [_to_pydantic(r) for r in rows]

    def replace_dimensions(
        self,
        language: str,
        dimensions: list[RubricDimensionInput],
        updated_by: str,
    ) -> list[RubricDimensionConfig]:
        """
        Bulk-replace the active weights for *language* in one transaction —
        matches the frontend's "adjust all sliders, then Save" interaction.
        Caller must have already validated max_score sums to 100.
        """
        for i, dim_input in enumerate(dimensions):
            row = (
                self._db.query(RubricDimensionORM)
                .filter(
                    RubricDimensionORM.dimension == dim_input.dimension.value,
                    RubricDimensionORM.language == language,
                )
                .first()
            )
            if row is None:
                row = RubricDimensionORM(dimension=dim_input.dimension.value, language=language)
                self._db.add(row)
            row.max_score = dim_input.max_score
            if dim_input.description is not None:
                row.description = dim_input.description
            row.display_order = i
            row.is_active = True
            row.updated_by = updated_by
        self._db.commit()
        invalidate_cache()
        return self.list_dimensions(language)


def get_cached_dimensions(language: str = "en") -> list[RubricDimensionConfig]:
    """
    Returns [] (never raises) if the table is unreachable/not yet created —
    e.g. a test harness that instantiates the app without running the
    startup lifespan. Callers combine this with a hardcoded fallback:
    `get_cached_dimensions("en") or _FALLBACK_DIMENSIONS`.
    """
    now = time.monotonic()
    cached = _cache.get(language)
    if cached is not None and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    db = SessionLocal()
    try:
        dimensions = RubricStore(db).list_dimensions(language)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Rubric config unavailable (%s) — caller should use its fallback", exc)
        return []
    finally:
        db.close()
    _cache[language] = (now, dimensions)
    return dimensions


def invalidate_cache() -> None:
    _cache.clear()
