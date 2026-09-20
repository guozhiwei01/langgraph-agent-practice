\set ON_ERROR_STOP on

-- Run this file against the email_agent database:
-- psql -U postgres -d email_agent -f sql/001_schema.sql

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS knowledge_documents (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    locale TEXT NOT NULL DEFAULT 'en-US',
    version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'published'
        CHECK (status IN ('draft', 'published', 'archived')),
    published_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
    chunk_key TEXT NOT NULL,
    heading TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding VECTOR(1024),
    embedding_model TEXT,
    embedded_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (document_id, chunk_key)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_documents_category
    ON knowledge_documents (category);
CREATE INDEX IF NOT EXISTS idx_knowledge_documents_metadata
    ON knowledge_documents USING GIN (metadata);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_document_id
    ON knowledge_chunks (document_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_embedding_hnsw
    ON knowledge_chunks USING HNSW (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS inbound_emails (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider TEXT NOT NULL,
    provider_message_id TEXT NOT NULL,
    provider_thread_id TEXT,
    sender_email TEXT NOT NULL,
    recipient_email TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    plain_text_body TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    raw_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider, provider_message_id)
);

CREATE TABLE IF NOT EXISTS agent_tasks (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    inbound_email_id BIGINT NOT NULL UNIQUE
        REFERENCES inbound_emails(id) ON DELETE RESTRICT,
    langgraph_thread_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN (
            'pending', 'processing', 'waiting_for_review', 'approved',
            'rejected', 'sending', 'sent', 'failed'
        )),
    classification JSONB,
    draft_response TEXT,
    error_code TEXT,
    error_message TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_tasks_status ON agent_tasks (status);

CREATE TABLE IF NOT EXISTS human_reviews (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id BIGINT NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
    reviewer_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'edited', 'rejected')),
    original_draft TEXT,
    final_response TEXT,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS outbound_emails (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id BIGINT NOT NULL UNIQUE REFERENCES agent_tasks(id) ON DELETE RESTRICT,
    provider_message_id TEXT,
    recipient_email TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sending', 'sent', 'failed')),
    sent_at TIMESTAMPTZ,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tool_executions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id BIGINT NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    response_payload JSONB,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'succeeded', 'failed')),
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS follow_up_jobs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id BIGINT NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
    scheduled_for TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled'
        CHECK (status IN ('scheduled', 'running', 'completed', 'cancelled', 'failed')),
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE OR REPLACE FUNCTION search_knowledge_chunks(
    query_embedding VECTOR(1024),
    match_count INTEGER DEFAULT 5,
    category_filter TEXT DEFAULT NULL
)
RETURNS TABLE (
    chunk_id BIGINT,
    document_slug TEXT,
    title TEXT,
    category TEXT,
    heading TEXT,
    content TEXT,
    source_uri TEXT,
    similarity DOUBLE PRECISION
)
LANGUAGE SQL
STABLE
AS $$
    SELECT
        chunk.id,
        document.slug,
        document.title,
        document.category,
        chunk.heading,
        chunk.content,
        document.source_uri,
        1 - (chunk.embedding <=> query_embedding) AS similarity
    FROM knowledge_chunks AS chunk
    JOIN knowledge_documents AS document ON document.id = chunk.document_id
    WHERE chunk.embedding IS NOT NULL
      AND document.status = 'published'
      AND (category_filter IS NULL OR document.category = category_filter)
    ORDER BY chunk.embedding <=> query_embedding
    LIMIT GREATEST(match_count, 0);
$$;

COMMIT;

