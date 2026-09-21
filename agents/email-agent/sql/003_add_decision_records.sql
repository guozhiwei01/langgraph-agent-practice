-- Persist decision checkpoints added after the initial schema was created.
ALTER TABLE agent_tasks
    ADD COLUMN IF NOT EXISTS evidence_evaluation JSONB;

ALTER TABLE agent_tasks
    ADD COLUMN IF NOT EXISTS response_validation JSONB;
