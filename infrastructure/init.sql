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
    user_id     TEXT        NOT NULL DEFAULT 'unknown',
    filename    TEXT        NOT NULL,
    status      TEXT        NOT NULL,
    result_json JSONB       NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_results_user_id ON pipeline_results (user_id);

-- ── Users (API Gateway auth) ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id              SERIAL      PRIMARY KEY,
    user_id         TEXT        UNIQUE NOT NULL,
    email           TEXT        UNIQUE NOT NULL,
    hashed_password TEXT        NOT NULL,
    is_active       BOOLEAN     DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── User Roles (API Gateway RBAC) ────────────────────────────────────────────
-- Multi-role: a user can hold zero or more of these simultaneously. Absence of
-- any row means "plain user" — the default, implicit role.
CREATE TABLE IF NOT EXISTS user_roles (
    id         SERIAL      PRIMARY KEY,
    user_id    TEXT        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    role       TEXT        NOT NULL CHECK (role IN ('reviewer', 'admin', 'super_admin')),
    granted_by TEXT,
    granted_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, role)
);

CREATE INDEX IF NOT EXISTS idx_user_roles_user_id ON user_roles (user_id);

-- ── AI Inference: HITL review queue ─────────────────────────────────────────
-- SQLAlchemy creates this table via init_db(); defined here as reference only.
-- (Uncomment and remove the SQLAlchemy auto-create if you prefer pure SQL migrations.)
-- CREATE TABLE IF NOT EXISTS review_queue ( ... );

-- ── AI Inference: Rubric configuration ───────────────────────────────────────
-- Also SQLAlchemy-managed (db/models.py RubricDimensionORM) — ai_inference's
-- init_db() creates this table AND seeds the default 4 dimensions/25 points
-- each on first startup against any fresh database, cloud or local. Listed
-- here as reference only, same as review_queue above.
-- CREATE TABLE IF NOT EXISTS rubric_dimensions ( ... );

-- ── Billing: Subscriptions ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS subscriptions (
    user_id                 TEXT        PRIMARY KEY REFERENCES users(user_id),
    stripe_customer_id      TEXT        UNIQUE,
    stripe_subscription_id  TEXT        UNIQUE,
    stripe_price_id         TEXT,
    plan                    TEXT        NOT NULL DEFAULT 'free',
    status                  TEXT        NOT NULL DEFAULT 'none',
    current_period_end      TIMESTAMPTZ,
    cancel_at_period_end    BOOLEAN     NOT NULL DEFAULT FALSE,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
