\set ON_ERROR_STOP on

BEGIN;

CREATE TABLE IF NOT EXISTS job_queue (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id BIGINT REFERENCES agent_tasks(id) ON DELETE CASCADE,
    job_type TEXT NOT NULL CHECK (job_type IN (
        'sync_gmail', 'process_email', 'approve_reply', 'reject_reply'
    )),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    deduplication_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'retry', 'succeeded', 'dead')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    locked_at TIMESTAMPTZ,
    locked_by TEXT,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_job_queue_claim
    ON job_queue (available_at, id)
    WHERE status IN ('queued', 'retry');
CREATE INDEX IF NOT EXISTS idx_job_queue_task ON job_queue (task_id, created_at DESC);

ALTER TABLE outbound_emails
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
ALTER TABLE outbound_emails
    ADD COLUMN IF NOT EXISTS rfc_message_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_outbound_emails_idempotency_key
    ON outbound_emails (idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_outbound_emails_rfc_message_id
    ON outbound_emails (rfc_message_id) WHERE rfc_message_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS audit_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor TEXT,
    action TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    request_id TEXT,
    outcome TEXT NOT NULL DEFAULT 'success'
        CHECK (outcome IN ('success', 'failure')),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_events_created_at
    ON audit_events (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_entity
    ON audit_events (entity_type, entity_id, created_at DESC);

COMMIT;
