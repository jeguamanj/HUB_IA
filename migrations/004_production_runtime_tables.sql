-- Tables used by the SQLAlchemy context store and legacy attempt tracker.
-- Keeping them in versioned migrations lets production runtime roles remain
-- free of DDL privileges.
BEGIN;

CREATE TABLE contexts (
    context_id   TEXT PRIMARY KEY,
    created_at   DOUBLE PRECISION NOT NULL,
    ttl_seconds  INTEGER NOT NULL,
    data         TEXT NOT NULL
);

CREATE TABLE submission_groups (
    course_id          TEXT NOT NULL DEFAULT '',
    assignment_id      TEXT NOT NULL DEFAULT '',
    user_id            TEXT NOT NULL DEFAULT '',
    submission_id      TEXT NOT NULL DEFAULT '',
    nota               DOUBLE PRECISION,
    escala_maxima      DOUBLE PRECISION,
    modelo             TEXT,
    intentos           INTEGER NOT NULL DEFAULT 0,
    created_at         DOUBLE PRECISION NOT NULL,
    last_evaluated_at  DOUBLE PRECISION NOT NULL,
    CONSTRAINT pk_submission_groups PRIMARY KEY (
        course_id, assignment_id, user_id
    )
);

INSERT INTO hub_schema_migrations (version, name, checksum)
VALUES (
    '004',
    'production_runtime_tables',
    '74ffe9d991ddcdaf4df9876e9099eff84accb8f928210677423092d4dcf43fdb'
);

COMMIT;
