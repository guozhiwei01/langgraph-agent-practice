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
                       task.created_at, email.sender_email, email.subject,
                       email.plain_text_body
                FROM agent_tasks AS task
                JOIN inbound_emails AS email ON email.id = task.inbound_email_id
                WHERE task.status IN ('pending', 'processing', 'waiting_for_review', 'failed')
                ORDER BY task.created_at DESC
                """
            )
            return list(cursor.fetchall())


def update_task_draft(
    task_id: int, *, status: str, classification: dict[str, Any], draft: str | None
) -> None:
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            UPDATE agent_tasks
            SET status = %s, classification = %s::jsonb, draft_response = %s,
                error_code = NULL, error_message = NULL, updated_at = NOW()
            WHERE id = %s
            """,
            (status, psycopg.types.json.Jsonb(classification), draft, task_id),
        )


def mark_task_failed(task_id: int, error: Exception) -> None:
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            UPDATE agent_tasks
            SET status = 'failed', error_code = %s, error_message = %s,
                attempt_count = attempt_count + 1, updated_at = NOW()
            WHERE id = %s
            """,
            (type(error).__name__, str(error)[:2000], task_id),
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
    status = {"approved": "approved", "edited": "approved", "rejected": "rejected"}[decision]
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            INSERT INTO human_reviews (
                task_id, reviewer_id, decision, original_draft, final_response, reason
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (task_id, reviewer_id, decision, original_draft, final_response, reason),
        )
        connection.execute(
            """
            UPDATE agent_tasks
            SET status = %s, draft_response = COALESCE(%s, draft_response), updated_at = NOW()
            WHERE id = %s
            """,
            (status, final_response, task_id),
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
                status = 'sent', sent_at = NOW(), updated_at = NOW()
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
