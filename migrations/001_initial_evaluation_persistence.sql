-- Initial PostgreSQL schema for durable essay evaluations.
-- Target: PostgreSQL 15+ (uses UNIQUE NULLS NOT DISTINCT), validated with PG 17.
-- Apply as one transaction. Do not edit after it has been applied. Add a new
-- numbered migration for future schema changes.

BEGIN;

CREATE TABLE hub_schema_migrations (
    version        VARCHAR(32) PRIMARY KEY,
    name           TEXT NOT NULL,
    checksum       CHAR(64) NOT NULL,
    applied_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE submission_groups (
    submission_group_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    institution_id      TEXT,
    lms                 TEXT NOT NULL,
    course_id           TEXT NOT NULL,
    assignment_id       TEXT NOT NULL,
    user_id             TEXT NOT NULL,
    consumed_attempts   INTEGER NOT NULL DEFAULT 0 CHECK (consumed_attempts >= 0),
    reserved_attempts   INTEGER NOT NULL DEFAULT 0 CHECK (reserved_attempts >= 0),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_submission_groups_identity
        UNIQUE NULLS NOT DISTINCT (
            institution_id, lms, course_id, assignment_id, user_id
        )
);

CREATE TABLE evaluation_jobs (
    evaluation_id          VARCHAR(128) PRIMARY KEY,
    status                 VARCHAR(16) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'EVALUATING', 'EVALUATED', 'ERROR')),
    source                 TEXT NOT NULL DEFAULT 'unknown',
    lms                    TEXT,
    institution_id         TEXT,
    course_id              TEXT,
    assignment_id          TEXT,
    user_id                TEXT,
    submission_id          TEXT,
    context_id             TEXT,
    submission_group_id    BIGINT REFERENCES submission_groups(submission_group_id)
        ON DELETE RESTRICT,
    count_attempt          BOOLEAN NOT NULL DEFAULT TRUE,
    attempt_tracking       VARCHAR(32) NOT NULL DEFAULT 'NOT_APPLICABLE'
        CHECK (attempt_tracking IN (
            'NOT_APPLICABLE',
            'RESERVED',
            'CONSUMED',
            'RELEASED',
            'SKIPPED_MISSING_IDENTITY'
        )),
    attempt_number         INTEGER CHECK (attempt_number IS NULL OR attempt_number > 0),
    max_attempts_snapshot  INTEGER CHECK (max_attempts_snapshot IS NULL OR max_attempts_snapshot >= 0),
    payload                JSONB NOT NULL,
    result                 JSONB,
    error_code             TEXT,
    error_message          TEXT,
    idempotency_client_id  TEXT,
    idempotency_key        TEXT,
    request_sha256         CHAR(64),
    processing_cycles      INTEGER NOT NULL DEFAULT 0 CHECK (processing_cycles >= 0),
    worker_attempts        INTEGER NOT NULL DEFAULT 0 CHECK (worker_attempts >= 0),
    max_processing_attempts SMALLINT NOT NULL DEFAULT 3
        CHECK (max_processing_attempts > 0),
    available_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_owner            TEXT,
    lease_expires_at       TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at             TIMESTAMPTZ,
    finished_at            TIMESTAMPTZ,
    CONSTRAINT ck_evaluation_idempotency_pair
        CHECK (
            (idempotency_client_id IS NULL AND idempotency_key IS NULL)
            OR
            (idempotency_client_id IS NOT NULL AND idempotency_key IS NOT NULL)
        ),
    CONSTRAINT ck_evaluation_request_hash
        CHECK (request_sha256 IS NULL OR request_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_evaluation_attempt_tracking
        CHECK (
            (attempt_tracking = 'NOT_APPLICABLE' AND count_attempt = FALSE
                AND submission_group_id IS NULL)
            OR
            (attempt_tracking = 'SKIPPED_MISSING_IDENTITY' AND count_attempt = TRUE
                AND submission_group_id IS NULL)
            OR
            (attempt_tracking IN ('RESERVED', 'CONSUMED', 'RELEASED')
                AND count_attempt = TRUE AND submission_group_id IS NOT NULL)
        ),
    CONSTRAINT ck_evaluated_job_has_result
        CHECK (status <> 'EVALUATED' OR result IS NOT NULL),
    CONSTRAINT ck_evaluated_job_attempt_consumed
        CHECK (
            status <> 'EVALUATED'
            OR attempt_tracking IN ('CONSUMED', 'NOT_APPLICABLE', 'SKIPPED_MISSING_IDENTITY')
        ),
    CONSTRAINT ck_failed_job_attempt_released
        CHECK (
            status <> 'ERROR'
            OR attempt_tracking IN ('RELEASED', 'NOT_APPLICABLE', 'SKIPPED_MISSING_IDENTITY')
        ),
    CONSTRAINT ck_active_job_has_no_finish_time
        CHECK (status IN ('PENDING', 'EVALUATING') OR finished_at IS NOT NULL)
);

CREATE UNIQUE INDEX uq_evaluation_idempotency
    ON evaluation_jobs (idempotency_client_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX ix_evaluation_jobs_pending
    ON evaluation_jobs (available_at, created_at)
    WHERE status = 'PENDING';

CREATE INDEX ix_evaluation_jobs_tenant_status_created
    ON evaluation_jobs (institution_id, status, created_at DESC);

CREATE INDEX ix_evaluation_jobs_submission
    ON evaluation_jobs (lms, submission_id)
    WHERE submission_id IS NOT NULL;

CREATE INDEX ix_evaluation_jobs_group_status
    ON evaluation_jobs (submission_group_id, status)
    WHERE submission_group_id IS NOT NULL;

CREATE TABLE evaluation_attempts (
    evaluation_attempt_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    evaluation_id         VARCHAR(128) NOT NULL
        REFERENCES evaluation_jobs(evaluation_id) ON DELETE CASCADE,
    processing_cycle      INTEGER NOT NULL CHECK (processing_cycle > 0),
    attempt_number        SMALLINT NOT NULL CHECK (attempt_number > 0),
    status                VARCHAR(16) NOT NULL
        CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'ABANDONED')),
    started_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at           TIMESTAMPTZ,
    error_code            TEXT,
    error_message         TEXT,
    CONSTRAINT uq_evaluation_attempt_number
        UNIQUE (evaluation_id, processing_cycle, attempt_number),
    CONSTRAINT ck_evaluation_attempt_finished
        CHECK (
            (status = 'RUNNING' AND finished_at IS NULL)
            OR
            (status <> 'RUNNING' AND finished_at IS NOT NULL)
        )
);

CREATE INDEX ix_evaluation_attempts_job_started
    ON evaluation_attempts (evaluation_id, started_at);

CREATE TABLE evaluation_status_events (
    evaluation_status_event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    evaluation_id              VARCHAR(128) NOT NULL
        REFERENCES evaluation_jobs(evaluation_id) ON DELETE CASCADE,
    event_number               INTEGER NOT NULL CHECK (event_number > 0),
    event_type                 TEXT NOT NULL,
    from_status                VARCHAR(16),
    to_status                  VARCHAR(16),
    actor_type                 TEXT NOT NULL DEFAULT 'system',
    actor_id                   TEXT,
    processing_cycle           INTEGER,
    attempt_number             SMALLINT,
    occurred_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata                   JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_evaluation_event_number
        UNIQUE (evaluation_id, event_number),
    CONSTRAINT ck_evaluation_event_from_status
        CHECK (from_status IS NULL OR from_status IN (
            'PENDING', 'EVALUATING', 'EVALUATED', 'ERROR'
        )),
    CONSTRAINT ck_evaluation_event_to_status
        CHECK (to_status IS NULL OR to_status IN (
            'PENDING', 'EVALUATING', 'EVALUATED', 'ERROR'
        ))
);

CREATE INDEX ix_evaluation_status_events_history
    ON evaluation_status_events (evaluation_id, occurred_at);

CREATE TABLE llm_invocations (
    llm_invocation_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    evaluation_id        VARCHAR(128) NOT NULL
        REFERENCES evaluation_jobs(evaluation_id) ON DELETE CASCADE,
    evaluation_attempt_id BIGINT NOT NULL
        REFERENCES evaluation_attempts(evaluation_attempt_id) ON DELETE CASCADE,
    call_number          SMALLINT NOT NULL CHECK (call_number > 0),
    provider             TEXT NOT NULL,
    model                TEXT,
    is_fallback          BOOLEAN NOT NULL DEFAULT FALSE,
    status               VARCHAR(16) NOT NULL
        CHECK (status IN ('SUCCESS', 'ERROR', 'TIMEOUT', 'CANCELLED')),
    started_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at          TIMESTAMPTZ,
    duration_ms          BIGINT CHECK (duration_ms IS NULL OR duration_ms >= 0),
    input_tokens         BIGINT CHECK (input_tokens IS NULL OR input_tokens >= 0),
    output_tokens        BIGINT CHECK (output_tokens IS NULL OR output_tokens >= 0),
    token_source         VARCHAR(16) NOT NULL DEFAULT 'unknown'
        CHECK (token_source IN ('provider', 'estimated', 'unknown')),
    cost_amount          NUMERIC(20, 10) CHECK (cost_amount IS NULL OR cost_amount >= 0),
    cost_currency        CHAR(3),
    error_code           TEXT,
    error_message        TEXT,
    CONSTRAINT uq_llm_call_number
        UNIQUE (evaluation_id, evaluation_attempt_id, call_number)
);

CREATE INDEX ix_llm_invocations_job_started
    ON llm_invocations (evaluation_id, started_at);

CREATE TABLE mcp_invocations (
    mcp_invocation_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    evaluation_id     VARCHAR(128)
        REFERENCES evaluation_jobs(evaluation_id) ON DELETE SET NULL,
    client_id         TEXT,
    institution_id    TEXT,
    tool_name         TEXT NOT NULL,
    status            VARCHAR(16) NOT NULL
        CHECK (status IN ('SUCCESS', 'ERROR', 'TIMEOUT', 'CANCELLED')),
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ,
    duration_ms       BIGINT CHECK (duration_ms IS NULL OR duration_ms >= 0),
    correlation_id    TEXT,
    error_code        TEXT,
    error_message     TEXT
);

CREATE INDEX ix_mcp_invocations_tenant_started
    ON mcp_invocations (institution_id, started_at DESC);

CREATE INDEX ix_mcp_invocations_evaluation
    ON mcp_invocations (evaluation_id, started_at)
    WHERE evaluation_id IS NOT NULL;

-- SHA-256 covers the migration definition above, excluding this registration
-- footer and the BEGIN/COMMIT wrappers.
INSERT INTO hub_schema_migrations (version, name, checksum)
VALUES (
    '001',
    'initial_evaluation_persistence',
    '6e03e8297f615ab2c92e0e46de75ceaa860f124c09058b5521a086671be68093'
);

COMMIT;
