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
-- Mirrors db/models.py ReviewQueueORM. SQLAlchemy would also create this via
-- init_db() on first service startup; defined here so a manually-provisioned
-- database (e.g. cloud RDS) doesn't need the service to run first.
CREATE TABLE IF NOT EXISTS review_queue (
    id              SERIAL      PRIMARY KEY,
    review_id       VARCHAR(36)  UNIQUE NOT NULL,
    inference_id    VARCHAR(36)  UNIQUE NOT NULL,
    document_id     VARCHAR(255) NOT NULL,
    text_hash       VARCHAR(64)  NOT NULL,
    text_preview    TEXT,
    ai_score        DOUBLE PRECISION NOT NULL,
    ai_confidence   DOUBLE PRECISION NOT NULL,
    ai_feedback     TEXT,
    rubric_json     JSON,
    flag_reason     VARCHAR(50) NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',
    reviewer_id     VARCHAR(255),
    reviewer_score  DOUBLE PRECISION,
    reviewer_notes  TEXT,
    created_at      TIMESTAMP   NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    assigned_at     TIMESTAMP,
    resolved_at     TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_review_queue_review_id    ON review_queue (review_id);
CREATE INDEX IF NOT EXISTS idx_review_queue_inference_id ON review_queue (inference_id);
CREATE INDEX IF NOT EXISTS idx_review_queue_document_id  ON review_queue (document_id);

-- ── AI Inference: Rubric configuration ───────────────────────────────────────
-- Mirrors db/models.py RubricDimensionORM. init_db() also seeds the default
-- 4 dimensions/25 points each on first startup against any fresh database;
-- the INSERT below reproduces that seed for manually-provisioned databases.
CREATE TABLE IF NOT EXISTS rubric_dimensions (
    id             SERIAL      PRIMARY KEY,
    dimension      VARCHAR(50) NOT NULL,
    language       VARCHAR(10) NOT NULL DEFAULT 'en',
    max_score      DOUBLE PRECISION NOT NULL DEFAULT 25.0,
    description    TEXT        DEFAULT '',
    display_order  INTEGER     DEFAULT 0,
    is_active      BOOLEAN     DEFAULT TRUE,
    updated_at     TIMESTAMP   DEFAULT (NOW() AT TIME ZONE 'utc'),
    updated_by     VARCHAR(255),
    CONSTRAINT uq_rubric_dim_lang UNIQUE (dimension, language)
);

INSERT INTO rubric_dimensions (dimension, language, max_score, description, display_order)
VALUES
    ('content',      'en', 25.0, 'Argument clarity, analytical depth, evidence relevance.', 0),
    ('organization', 'en', 25.0, 'Logical flow, paragraph cohesion, intro/conclusion.', 1),
    ('language',     'en', 25.0, 'Vocabulary richness, sentence variety, academic register.', 2),
    ('conventions',  'en', 25.0, 'Spelling, punctuation, syntax accuracy.', 3)
ON CONFLICT ON CONSTRAINT uq_rubric_dim_lang DO NOTHING;

-- ── Billing: Subscriptions ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS subscriptions (
    user_id            TEXT        PRIMARY KEY REFERENCES users(user_id),
    stripe_customer_id TEXT,
    plan               TEXT        NOT NULL DEFAULT 'free',
    status             TEXT        NOT NULL DEFAULT 'none',
    current_period_end TIMESTAMPTZ
);
