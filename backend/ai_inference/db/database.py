"""
SQLAlchemy engine, session factory, and Base for the ai_inference service.
Defaults to SQLite for zero-config development; swap DATABASE_URL for Postgres.
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://platform:platform@postgres:5432/platform")

# check_same_thread=False is only needed for SQLite when multiple threads share
# a single connection; harmless (and ignored) on Postgres.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    DATABASE_URL,
    connect_args=_connect_args,
    pool_pre_ping=True,   # detect stale connections before use
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


# ── FastAPI dependency ─────────────────────────────────────────────────────────

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


_DEFAULT_RUBRIC_DIMENSIONS = [
    # (dimension, max_score, description, display_order) — mirrors the 4-dimension,
    # 25-points-each rubric that used to be hardcoded in core/grader.py's system prompt.
    ("content",      25.0, "Argument clarity, analytical depth, evidence relevance.", 0),
    ("organization", 25.0, "Logical flow, paragraph cohesion, intro/conclusion.", 1),
    ("language",     25.0, "Vocabulary richness, sentence variety, academic register.", 2),
    ("conventions",  25.0, "Spelling, punctuation, syntax accuracy.", 3),
]


def init_db() -> None:
    from db.models import ReviewQueueORM, RubricDimensionORM  # noqa: F401 — registers tables with Base
    Base.metadata.create_all(bind=engine)
    _seed_default_rubric()


def _seed_default_rubric() -> None:
    from db.models import RubricDimensionORM  # noqa: PLC0415

    db = SessionLocal()
    try:
        exists = db.query(RubricDimensionORM).filter(RubricDimensionORM.language == "en").first()
        if exists:
            return
        for dimension, max_score, description, order in _DEFAULT_RUBRIC_DIMENSIONS:
            db.add(RubricDimensionORM(
                dimension=dimension,
                language="en",
                max_score=max_score,
                description=description,
                display_order=order,
            ))
        db.commit()
    finally:
        db.close()
