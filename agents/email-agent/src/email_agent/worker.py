"""Durable PostgreSQL job worker for ingestion, graph execution and review actions."""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from typing import Any

from email_agent.config import get_settings
from email_agent.observability import configure_logging
from email_agent.runtime import postgres_agent_app
from email_agent.schemas import EmailRequest
from email_agent.service import EmailAgentService
from email_agent.storage import (
    claim_job,
    complete_job,
    create_task_for_email,
    enqueue_job,
    fail_job,
    get_task,
    record_audit_event,
    record_task_attempt,
    recover_stale_jobs,
    set_task_status,
    heartbeat_job,
)
from email_agent.tools.gmail import (
    fetch_message,
    list_unread_labeled_message_ids,
    mark_message_read,
)


logger = logging.getLogger(__name__)


class JobProcessor:
    def __init__(self, service: EmailAgentService) -> None:
        self.service = service

    def process(self, job: dict[str, Any]) -> None:
        handlers = {
            "sync_gmail": self._sync_gmail,
            "process_email": self._process_email,
            "approve_reply": self._approve_reply,
            "reject_reply": self._reject_reply,
        }
        try:
            handler = handlers[job["job_type"]]
        except KeyError as error:
            raise ValueError(f"Unsupported job type: {job['job_type']}") from error
        handler(job)

    def _sync_gmail(self, job: dict[str, Any]) -> None:
        limit = int((job.get("payload") or {}).get("limit", 20))
        for message_id in list_unread_labeled_message_ids(limit=limit):
            email = fetch_message(message_id)
            task_id, _ = create_task_for_email(email)
            # Always attempt the idempotent enqueue. This repairs a crash that
            # happened after the inbound transaction but before queue insertion.
            enqueue_job(
                "process_email",
                task_id=task_id,
                deduplication_key=f"task:{task_id}:process",
            )

    def _process_email(self, job: dict[str, Any]) -> None:
        task = _required_task(job)
        set_task_status(task["id"], "processing")
        self.service.run(
            EmailRequest(
                task_id=task["id"],
                email_id=task["provider_message_id"],
                sender_email=task["sender_email"],
                subject=task["subject"],
                email_content=task["plain_text_body"],
                provider_thread_id=task["provider_thread_id"],
            )
        )
        mark_message_read(task["provider_message_id"])

    def _approve_reply(self, job: dict[str, Any]) -> None:
        task = _required_task(job)
        payload = job.get("payload") or {}
        self.service.approve_and_send(
            task["id"],
            response_text=payload.get("response_text") or "",
            reviewer_id=payload.get("reviewer_id") or "local-reviewer",
        )

    def _reject_reply(self, job: dict[str, Any]) -> None:
        task = _required_task(job)
        payload = job.get("payload") or {}
        self.service.reject(
            task["id"],
            reason=payload.get("reason") or "Rejected during local review.",
            reviewer_id=payload.get("reviewer_id") or "local-reviewer",
        )


def _required_task(job: dict[str, Any]) -> dict[str, Any]:
    task_id = job.get("task_id")
    if task_id is None:
        raise ValueError(f"Job {job['id']} requires a task ID.")
    task = get_task(task_id)
    if task is None:
        raise LookupError(f"Task {task_id} does not exist.")
    return task


def run_worker(*, once: bool = False) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    recovered = recover_stale_jobs(settings.worker_stale_seconds)
    if recovered:
        logger.warning(
            "Recovered stale jobs",
            extra={"event": "jobs.recovered", "worker_id": worker_id},
        )

    processed = 0
    last_recovery = time.monotonic()
    with postgres_agent_app() as graph:
        processor = JobProcessor(EmailAgentService(graph))
        while True:
            if time.monotonic() - last_recovery >= min(
                60, settings.worker_stale_seconds / 2
            ):
                recover_stale_jobs(settings.worker_stale_seconds)
                last_recovery = time.monotonic()
            job = claim_job(worker_id)
            if job is None:
                if once:
                    return processed
                time.sleep(settings.worker_poll_seconds)
                continue

            started = time.monotonic()
            context = {
                "worker_id": worker_id,
                "job_id": job["id"],
                "job_type": job["job_type"],
                "task_id": job.get("task_id"),
                "attempt": job["attempt_count"],
            }
            logger.info("Job started", extra={"event": "job.started", **context})
            heartbeat_stop = threading.Event()
            heartbeat = threading.Thread(
                target=_heartbeat_loop,
                args=(
                    job["id"],
                    worker_id,
                    heartbeat_stop,
                    max(1.0, settings.worker_stale_seconds / 3),
                ),
                daemon=True,
            )
            heartbeat.start()
            try:
                processor.process(job)
                complete_job(job["id"])
                _record_audit_safely(job, worker_id)
                logger.info(
                    "Job succeeded",
                    extra={
                        "event": "job.succeeded",
                        "duration_ms": round((time.monotonic() - started) * 1000, 2),
                        **context,
                    },
                )
            except Exception as error:
                terminal = fail_job(job, error)
                if job.get("task_id") is not None:
                    record_task_attempt(job["task_id"], error, terminal=terminal)
                logger.exception(
                    "Job failed",
                    extra={
                        "event": "job.dead" if terminal else "job.retry",
                        "duration_ms": round((time.monotonic() - started) * 1000, 2),
                        **context,
                    },
                )
            finally:
                heartbeat_stop.set()
                heartbeat.join(timeout=1)
            processed += 1
            if once:
                return processed


def _heartbeat_loop(
    job_id: int,
    worker_id: str,
    stop: threading.Event,
    interval: float,
) -> None:
    while not stop.wait(interval):
        try:
            heartbeat_job(job_id, worker_id)
        except Exception:
            logger.exception(
                "Job heartbeat failed",
                extra={
                    "event": "job.heartbeat_failed",
                    "worker_id": worker_id,
                    "job_id": job_id,
                },
            )


def _record_audit_safely(job: dict[str, Any], worker_id: str) -> None:
    """Observability failures must never replay a completed business action."""
    try:
        record_audit_event(
            action=f"job.{job['job_type']}",
            actor=worker_id,
            entity_type="task" if job.get("task_id") else "job",
            entity_id=str(job.get("task_id") or job["id"]),
        )
    except Exception:
        logger.exception(
            "Worker audit event could not be persisted",
            extra={
                "event": "audit.failed",
                "worker_id": worker_id,
                "job_id": job["id"],
                "job_type": job["job_type"],
                "task_id": job.get("task_id"),
            },
        )


def main() -> None:
    run_worker()


if __name__ == "__main__":
    main()
