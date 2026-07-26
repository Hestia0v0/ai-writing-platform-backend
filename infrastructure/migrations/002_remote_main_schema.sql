-- Apply once to databases created before the RBAC and per-user pipeline update.
ALTER TABLE pipeline_results
    ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'unknown';

CREATE INDEX IF NOT EXISTS idx_pipeline_results_user_id
    ON pipeline_results (user_id);

CREATE TABLE IF NOT EXISTS user_roles (
    user_id    TEXT        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    role       TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, role),
    CONSTRAINT user_roles_role_check
        CHECK (role IN ('user', 'admin', 'super_admin'))
);

CREATE INDEX IF NOT EXISTS idx_user_roles_user_id
    ON user_roles (user_id);
