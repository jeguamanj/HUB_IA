-- Rename the new evaluation counter table so it does not collide with the
-- legacy submission tracker table, which still uses the name submission_groups.
-- Apply once, after migration 001, as one transaction.

BEGIN;

ALTER TABLE submission_groups
    RENAME TO evaluation_submission_groups;

ALTER TABLE evaluation_submission_groups
    RENAME CONSTRAINT uq_submission_groups_identity
    TO uq_evaluation_submission_groups_identity;

INSERT INTO hub_schema_migrations (version, name, checksum)
VALUES (
    '002',
    'rename_evaluation_submission_groups',
    'fa6811bcea9de921d249bf59d3499ad203d04bc90298dc8a92cace9c3ba5fa7e'
);

COMMIT;
