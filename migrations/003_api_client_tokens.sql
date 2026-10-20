-- version: 003
-- checksum: SHA-256 of the migration body before its ledger INSERT.
BEGIN;

CREATE TABLE hub_api_clients (
    client_id       UUID PRIMARY KEY,
    client_name     TEXT NOT NULL,
    institution_id  TEXT NOT NULL,
    tenant_id       TEXT,
    token_hash      CHAR(64) NOT NULL UNIQUE
        CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    scopes          JSONB NOT NULL
        CHECK (jsonb_typeof(scopes) = 'array'),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      TEXT NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    revoked_at      TIMESTAMPTZ,
    last_used_at    TIMESTAMPTZ
);

CREATE TABLE hub_api_client_events (
    event_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id       UUID NOT NULL REFERENCES hub_api_clients(client_id),
    actor           TEXT NOT NULL,
    action          TEXT NOT NULL CHECK (action IN ('created', 'rotated', 'revoked')),
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_hub_api_clients_active
    ON hub_api_clients (client_id)
    WHERE revoked_at IS NULL;

INSERT INTO hub_schema_migrations (version, name, checksum)
VALUES (
    '003',
    'api_client_tokens',
    'd42ebc2da6647603c7ee706211e93c541b6b8555c0d85bee6b6eab8cad012b50'
);

COMMIT;
