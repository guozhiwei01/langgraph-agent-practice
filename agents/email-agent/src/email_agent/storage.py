"""PostgreSQL persistence for inbound mail, workflow tasks and reviews."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row

from email_agent.config import get_settings


def _database_url() -> str:
    value = get_settings().database_url
    if not value:
        raise RuntimeError("DATABASE_URL is missing from the Email Agent environment.")
    return value


def create_task_for_email(email: dict[str, Any]) -> tuple[int, bool]:
    """Persist one Gmail message and create one idempotent processing task."""
    with psycopg.connect(_database_url()) as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                INSERT INTO inbound_emails (
                    provider, provider_message_id, provider_thread_id,
                    sender_email, recipient_email, subject, plain_text_body,
                    received_at, raw_metadata
                )
                VALUES ('gmail', %(message_id)s, %(thread_id)s, %(sender)s,
                        %(recipient)s, %(subject)s, %(body)s, %(received_at)s,
                        %(metadata)s::jsonb)
                ON CONFLICT (provider, provider_message_id) DO NOTHING
                RETURNING id
                """,
                email,
            )
            inserted = cursor.fetchone()
            if inserted:
                inbound_id = inserted["id"]
                cursor.execute(
                    """
                    INSERT INTO agent_tasks (inbound_email_id, langgraph_thread_id)
                    VALUES (%s, %s)
                    RETURNING id
                    """,
                    (inbound_id, f"gmail:{email['message_id']}"),
                )
                return cursor.fetchone()["id"], True

            cursor.execute(
                """
                SELECT task.id
                FROM agent_tasks AS task
                JOIN inbound_emails AS email ON email.id = task.inbound_email_id
                WHERE email.provider = 'gmail' AND email.provider_message_id = %s
                """,
                (email["message_id"],),
            )
            return cursor.fetchone()["id"], False


def get_task(task_id: int) -> dict[str, Any] | None:
    with psycopg.connect(_database_url()) as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT task.*, email.provider_message_id, email.provider_thread_id,
                       email.sender_email, email.recipient_email, email.subject,
                       email.plain_text_body, email.received_at, email.raw_metadata
                FROM agent_tasks AS task
                JOIN inbound_emails AS email ON email.id = task.inbound_email_id
                WHERE task.id = %s
                """,
                (task_id,),
            )
            return cursor.fetchone()


def list_review_tasks() -> list[dict[str, Any]]:
    with psycopg.connect(_database_url()) as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT task.id, task.status, task.classification, task.draft_response,
                       task.evidence_evaluation, task.response_validation,
                       task.created_at, email.sender_email, email.subject,
                       email.plain_text_body, latest_job.id AS latest_job_id,
                       latest_job.status AS latest_job_status,
                       latest_job.last_error AS latest_job_error
                FROM agent_tasks AS task
                JOIN inbound_emails AS email ON email.id = task.inbound_email_id
                LEFT JOIN LATERAL (
                    SELECT id, status, last_error FROM job_queue
                    WHERE task_id = task.id ORDER BY id DESC LIMIT 1
                ) AS latest_job ON TRUE
                WHERE task.status IN (
                    'pending', 'processing', 'waiting_for_review', 'sending', 'failed'
                )
                ORDER BY task.created_at DESC
                """
            )
            return list(cursor.fetchall())


def update_task_draft(
    task_id: int,
    *,
    status: str,
    classification: dict[str, Any],
    evidence_evaluation: dict[str, Any],
    response_validation: dict[str, Any],
    draft: str | None,
) -> None:
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            UPDATE agent_tasks
            SET status = %s, classification = %s::jsonb,
                evidence_evaluation = %s::jsonb, response_validation = %s::jsonb,
                draft_response = %s,
                error_code = NULL, error_message = NULL, updated_at = NOW()
            WHERE id = %s
            """,
            (
                status,
                psycopg.types.json.Jsonb(classification),
                psycopg.types.json.Jsonb(evidence_evaluation),
                psycopg.types.json.Jsonb(response_validation),
                draft,
                task_id,
            ),
        )


def set_task_status(task_id: int, status: str) -> None:
    """Move a task into a worker-owned transient state."""
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            UPDATE agent_tasks
            SET status = %s, error_code = NULL, error_message = NULL, updated_at = NOW()
            WHERE id = %s
            """,
            (status, task_id),
        )


def record_task_attempt(task_id: int, error: Exception, *, terminal: bool) -> None:
    """Persist a worker failure while preserving automatic retry state."""
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            UPDATE agent_tasks
            SET status = CASE WHEN %s THEN 'failed' ELSE status END,
                error_code = %s, error_message = %s,
                attempt_count = attempt_count + 1, updated_at = NOW()
            WHERE id = %s
            """,
            (terminal, type(error).__name__, str(error)[:2000], task_id),
        )


def record_review(
    task_id: int,
    *,
    reviewer_id: str,
    decision: str,
    original_draft: str | None,
    final_response: str | None,
    reason: str | None = None,
) -> None:
    """Persist the single terminal review for a task without duplicating replays."""
    status = {"approved": "approved", "edited": "approved", "rejected": "rejected"}[decision]
    with psycopg.connect(_database_url()) as connection:
        with connection.cursor() as cursor:
            # Serialize review writes for one task. A graph node can be replayed if
            # the process stops after this transaction but before its checkpoint.
            cursor.execute("SELECT id FROM agent_tasks WHERE id = %s FOR UPDATE", (task_id,))
            if cursor.fetchone() is None:
                raise LookupError(f"Task {task_id} does not exist.")
            cursor.execute(
                "SELECT id FROM human_reviews WHERE task_id = %s ORDER BY id LIMIT 1",
                (task_id,),
            )
            existing = cursor.fetchone()
            if existing is None:
                cursor.execute(
                    """
                    INSERT INTO human_reviews (
                        task_id, reviewer_id, decision, original_draft,
                        final_response, reason
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        task_id,
                        reviewer_id,
                        decision,
                        original_draft,
                        final_response,
                        reason,
                    ),
                )
            cursor.execute(
                """
                UPDATE agent_tasks
                SET status = %s, draft_response = COALESCE(%s, draft_response),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (status, final_response, task_id),
            )


def get_sent_outbound(task_id: int) -> dict[str, Any] | None:
    """Return a completed outbound record so replay can skip a known send."""
    with psycopg.connect(_database_url()) as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT provider_message_id, recipient_email, subject, body, sent_at,
                       status, idempotency_key, rfc_message_id
                FROM outbound_emails
                WHERE task_id = %s AND status = 'sent'
                """,
                (task_id,),
            )
            return cursor.fetchone()


def prepare_outbound(
    task_id: int,
    *,
    recipient: str,
    subject: str,
    body: str,
    idempotency_key: str,
    rfc_message_id: str,
) -> dict[str, Any]:
    """Create the durable outbox record before contacting Gmail."""
    with psycopg.connect(_database_url()) as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                INSERT INTO outbound_emails (
                    task_id, recipient_email, subject, body, status,
                    idempotency_key, rfc_message_id
                ) VALUES (%s, %s, %s, %s, 'pending', %s, %s)
                ON CONFLICT (task_id) DO NOTHING
                RETURNING *
                """,
                (task_id, recipient, subject, body, idempotency_key, rfc_message_id),
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute("SELECT * FROM outbound_emails WHERE task_id = %s", (task_id,))
                row = cursor.fetchone()
            if row is None:
                raise RuntimeError(f"Could not prepare outbound email for task {task_id}.")
            return row


def mark_outbound_sending(task_id: int) -> None:
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            UPDATE outbound_emails
            SET status = 'sending', error_message = NULL, updated_at = NOW()
            WHERE task_id = %s AND status <> 'sent'
            """,
            (task_id,),
        )


def mark_task_sent(
    task_id: int, *, recipient: str, subject: str, body: str, provider_message_id: str
) -> None:
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            INSERT INTO outbound_emails (
                task_id, provider_message_id, recipient_email, subject, body,
                status, sent_at
            ) VALUES (%s, %s, %s, %s, %s, 'sent', NOW())
            ON CONFLICT (task_id) DO UPDATE SET
                provider_message_id = EXCLUDED.provider_message_id,
                recipient_email = EXCLUDED.recipient_email,
                subject = EXCLUDED.subject, body = EXCLUDED.body,
                status = 'sent', error_message = NULL,
                sent_at = NOW(), updated_at = NOW()
            """,
            (task_id, provider_message_id, recipient, subject, body),
        )
        connection.execute(
            "UPDATE agent_tasks SET status = 'sent', updated_at = NOW() WHERE id = %s",
            (task_id,),
        )


def record_tool_success(
    task_id: int, *, tool_name: str, idempotency_key: str, response: dict[str, Any]
) -> None:
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            INSERT INTO tool_executions (
                task_id, tool_name, idempotency_key, response_payload,
                status, completed_at
            ) VALUES (%s, %s, %s, %s, 'succeeded', NOW())
            ON CONFLICT (idempotency_key) DO NOTHING
            """,
            (task_id, tool_name, idempotency_key, psycopg.types.json.Jsonb(response)),
        )


def get_tool_success(idempotency_key: str) -> dict[str, Any] | None:
    with psycopg.connect(_database_url()) as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT response_payload FROM tool_executions
                WHERE idempotency_key = %s AND status = 'succeeded'
                """,
                (idempotency_key,),
            )
            row = cursor.fetchone()
            return row["response_payload"] if row else None


def enqueue_job(
    job_type: str,
    *,
    deduplication_key: str,
    task_id: int | None = None,
    payload: dict[str, Any] | None = None,
    max_attempts: int = 5,
) -> tuple[int, bool]:
    """Insert a durable job, returning the existing job for duplicate requests."""
    with psycopg.connect(_database_url()) as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                INSERT INTO job_queue (
                    task_id, job_type, payload, deduplication_key, max_attempts
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (deduplication_key) DO NOTHING
                RETURNING id
                """,
                (
                    task_id,
                    job_type,
                    psycopg.types.json.Jsonb(payload or {}),
                    deduplication_key,
                    max_attempts,
                ),
            )
            inserted = cursor.fetchone()
            if inserted:
                return inserted["id"], True
            cursor.execute(
                "SELECT id FROM job_queue WHERE deduplication_key = %s",
                (deduplication_key,),
            )
            return cursor.fetchone()["id"], False


def enqueue_review_job(
    task_id: int,
    *,
    approved: bool,
    reviewer_id: str,
    response_text: str | None = None,
    reason: str | None = None,
) -> tuple[int, bool]:
    """Atomically consume a waiting review and enqueue its graph resumption."""
    with psycopg.connect(_database_url()) as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT status FROM agent_tasks WHERE id = %s FOR UPDATE", (task_id,)
            )
            task = cursor.fetchone()
            if task is None:
                raise LookupError(f"Task {task_id} does not exist.")
            if task["status"] == "sent" and approved:
                cursor.execute(
                    "SELECT id FROM job_queue WHERE deduplication_key = %s",
                    (f"task:{task_id}:review",),
                )
                existing = cursor.fetchone()
                return (existing["id"] if existing else 0), False
            if task["status"] != "waiting_for_review":
                raise ValueError(f"Task {task_id} is not waiting for review.")

            job_type = "approve_reply" if approved else "reject_reply"
            payload = {
                "reviewer_id": reviewer_id,
                "response_text": response_text,
                "reason": reason,
            }
            cursor.execute(
                """
                INSERT INTO job_queue (
                    task_id, job_type, payload, deduplication_key, max_attempts
                ) VALUES (%s, %s, %s, %s, 7)
                ON CONFLICT (deduplication_key) DO NOTHING
                RETURNING id
                """,
                (
                    task_id,
                    job_type,
                    psycopg.types.json.Jsonb(payload),
                    f"task:{task_id}:review",
                ),
            )
            inserted = cursor.fetchone()
            if inserted is None:
                cursor.execute(
                    "SELECT id FROM job_queue WHERE deduplication_key = %s",
                    (f"task:{task_id}:review",),
                )
                return cursor.fetchone()["id"], False
            cursor.execute(
                "UPDATE agent_tasks SET status = %s, updated_at = NOW() WHERE id = %s",
                ("sending" if approved else "processing", task_id),
            )
            return inserted["id"], True


def recover_stale_jobs(stale_after_seconds: int = 300) -> int:
    """Return abandoned running jobs to the retry pool after a worker crash."""
    with psycopg.connect(_database_url()) as connection:
        result = connection.execute(
            """
            UPDATE job_queue
            SET status = 'retry', available_at = NOW(), locked_at = NULL,
                locked_by = NULL, last_error = 'Worker lease expired', updated_at = NOW()
            WHERE status = 'running'
              AND locked_at < NOW() - make_interval(secs => %s)
            """,
            (stale_after_seconds,),
        )
        return result.rowcount


def heartbeat_job(job_id: int, worker_id: str) -> bool:
    """Renew a running job lease so another worker will not reclaim it."""
    with psycopg.connect(_database_url()) as connection:
        result = connection.execute(
            """
            UPDATE job_queue SET locked_at = NOW(), updated_at = NOW()
            WHERE id = %s AND status = 'running' AND locked_by = %s
            """,
            (job_id, worker_id),
        )
        return result.rowcount == 1


def claim_job(worker_id: str) -> dict[str, Any] | None:
    """Claim one available job using PostgreSQL SKIP LOCKED."""
    with psycopg.connect(_database_url()) as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                WITH candidate AS (
                    SELECT id FROM job_queue
                    WHERE status IN ('queued', 'retry') AND available_at <= NOW()
                    ORDER BY available_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE job_queue AS job
                SET status = 'running', attempt_count = attempt_count + 1,
                    locked_at = NOW(), locked_by = %s, updated_at = NOW()
                FROM candidate
                WHERE job.id = candidate.id
                RETURNING job.*
                """,
                (worker_id,),
            )
            return cursor.fetchone()


def complete_job(job_id: int) -> None:
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            UPDATE job_queue SET status = 'succeeded', completed_at = NOW(),
                locked_at = NULL, locked_by = NULL, last_error = NULL, updated_at = NOW()
            WHERE id = %s
            """,
            (job_id,),
        )


def fail_job(job: dict[str, Any], error: Exception) -> bool:
    """Schedule exponential retry; return True when attempts are exhausted."""
    terminal = int(job["attempt_count"]) >= int(job["max_attempts"])
    delay = min(900, 5 * (2 ** max(0, int(job["attempt_count"]) - 1)))
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            UPDATE job_queue
            SET status = %s,
                available_at = CASE WHEN %s THEN available_at
                    ELSE NOW() + make_interval(secs => %s) END,
                locked_at = NULL, locked_by = NULL, last_error = %s,
                completed_at = CASE WHEN %s THEN NOW() ELSE NULL END,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                "dead" if terminal else "retry",
                terminal,
                delay,
                f"{type(error).__name__}: {str(error)[:1900]}",
                terminal,
                job["id"],
            ),
        )
    return terminal


def retry_dead_job(job_id: int) -> int:
    """Manually requeue a dead job and reset its retry budget."""
    with psycopg.connect(_database_url()) as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                UPDATE job_queue SET status = 'queued', attempt_count = 0,
                    available_at = NOW(), completed_at = NULL, last_error = NULL,
                    locked_at = NULL, locked_by = NULL, updated_at = NOW()
                WHERE id = %s AND status = 'dead'
                RETURNING task_id, job_type
                """,
                (job_id,),
            )
            job = cursor.fetchone()
            if job is None:
                raise ValueError(f"Job {job_id} is not dead or does not exist.")
            if job["task_id"] is not None:
                status = "sending" if job["job_type"] == "approve_reply" else "processing"
                cursor.execute(
                    """
                    UPDATE agent_tasks SET status = %s, error_code = NULL,
                        error_message = NULL, updated_at = NOW() WHERE id = %s
                    """,
                    (status, job["task_id"]),
                )
            return job_id


def list_dead_jobs() -> list[dict[str, Any]]:
    """List dead jobs not already represented by an email task card."""
    with psycopg.connect(_database_url()) as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT id, task_id, job_type, attempt_count, max_attempts,
                       last_error, created_at
                FROM job_queue WHERE status = 'dead' AND task_id IS NULL
                ORDER BY updated_at DESC
                """
            )
            return list(cursor.fetchall())


def queue_metrics() -> dict[str, Any]:
    """Return low-cardinality queue/task metrics for health and scraping."""
    with psycopg.connect(_database_url()) as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT status, COUNT(*) AS count FROM job_queue GROUP BY status"
            )
            jobs = {row["status"]: row["count"] for row in cursor.fetchall()}
            cursor.execute(
                "SELECT status, COUNT(*) AS count FROM agent_tasks GROUP BY status"
            )
            tasks = {row["status"]: row["count"] for row in cursor.fetchall()}
            cursor.execute(
                """
                SELECT EXTRACT(EPOCH FROM (NOW() - MIN(available_at)))::BIGINT AS seconds
                FROM job_queue
                WHERE status IN ('queued', 'retry') AND available_at <= NOW()
                """
            )
            oldest = cursor.fetchone()["seconds"] or 0
            return {"jobs": jobs, "tasks": tasks, "oldest_ready_job_seconds": oldest}


def database_ready() -> bool:
    with psycopg.connect(_database_url()) as connection:
        return connection.execute("SELECT 1").fetchone()[0] == 1


def record_audit_event(
    *,
    action: str,
    actor: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    request_id: str | None = None,
    outcome: str = "success",
    metadata: dict[str, Any] | None = None,
) -> None:
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            INSERT INTO audit_events (
                actor, action, entity_type, entity_id, request_id, outcome, metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                actor,
                action,
                entity_type,
                entity_id,
                request_id,
                outcome,
                psycopg.types.json.Jsonb(metadata or {}),
            ),
        )
