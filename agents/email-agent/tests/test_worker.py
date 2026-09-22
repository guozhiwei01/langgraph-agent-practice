"""Unit tests for durable job dispatch without external services."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from email_agent.worker import JobProcessor


class WorkerTests(unittest.TestCase):
    @patch("email_agent.worker.enqueue_job")
    @patch("email_agent.worker.create_task_for_email", return_value=(17, False))
    @patch("email_agent.worker.fetch_message", return_value={"message_id": "gmail-17"})
    @patch("email_agent.worker.list_unread_labeled_message_ids", return_value=["gmail-17"])
    def test_sync_job_repairs_missing_processing_job_for_existing_email(
        self, list_ids, fetch, create_task, enqueue
    ) -> None:
        processor = JobProcessor(MagicMock())
        processor.process({"id": 1, "job_type": "sync_gmail", "payload": {"limit": 5}})

        enqueue.assert_called_once_with(
            "process_email", task_id=17, deduplication_key="task:17:process"
        )

    @patch("email_agent.worker.mark_message_read")
    @patch("email_agent.worker.set_task_status")
    @patch("email_agent.worker.get_task")
    def test_process_job_marks_gmail_read_only_after_graph_success(
        self, get_task, set_status, mark_read
    ) -> None:
        get_task.return_value = {
            "id": 17,
            "provider_message_id": "gmail-17",
            "sender_email": "customer@example.com",
            "subject": "Question",
            "plain_text_body": "Help",
            "provider_thread_id": "thread-17",
        }
        service = MagicMock()
        processor = JobProcessor(service)

        processor.process({"id": 2, "task_id": 17, "job_type": "process_email", "payload": {}})

        service.run.assert_called_once()
        set_status.assert_called_once_with(17, "processing")
        mark_read.assert_called_once_with("gmail-17")


if __name__ == "__main__":
    unittest.main()
