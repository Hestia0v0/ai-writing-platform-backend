-- Run once on first PostgreSQL startup (mounted via docker-entrypoint-initdb.d).
-- Creates all tables used by every microservice.

CREATE EXTENSION IF NOT EXISTS vector;

-- ── Knowledge Retrieval ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS document_embeddings (
    id          SERIAL       PRIMARY KEY,
    document_id TEXT         UNIQUE NOT NULL,
    content     TEXT         NOT NULL,
    embedding   vector(384),
    metadata    JSONB        DEFAULT '{}',
    created_at  TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw
    ON document_embeddings USING hnsw (embedding vector_cosine_ops);

-- ── Pipelines ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pipeline_results (
    document_id TEXT        PRIMARY KEY,
    filename    TEXT        NOT NULL,
    status      TEXT        NOT NULL,
    result_json JSONB       NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ── Users (API Gateway auth) ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id              SERIAL      PRIMARY KEY,
    user_id         TEXT        UNIQUE NOT NULL,
    email           TEXT        UNIQUE NOT NULL,
    hashed_password TEXT        NOT NULL,
    is_active       BOOLEAN     DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── AI Inference: HITL review queue ─────────────────────────────────────────
-- SQLAlchemy creates this table via init_db(); defined here as reference only.
-- (Uncomment and remove the SQLAlchemy auto-create if you prefer pure SQL migrations.)
-- CREATE TABLE IF NOT EXISTS review_queue ( ... );

-- ── Billing: Subscriptions ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS subscriptions (
    user_id            TEXT        PRIMARY KEY REFERENCES users(user_id),
    stripe_customer_id TEXT,
    plan               TEXT        NOT NULL DEFAULT 'free',
    status             TEXT        NOT NULL DEFAULT 'none',
    current_period_end TIMESTAMPTZ
);
