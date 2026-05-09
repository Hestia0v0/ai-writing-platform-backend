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


def init_db() -> None:
    from db.models import ReviewQueueORM  # noqa: F401 — registers table with Base
    Base.metadata.create_all(bind=engine)
