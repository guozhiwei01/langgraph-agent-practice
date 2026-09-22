"""Offline safety tests for external-service adapters."""

import base64
import unittest
from unittest.mock import MagicMock, patch

from email_agent.tools.github_issues import sanitize_for_issue
from email_agent.tools.gmail import (
    _plain_text,
    find_sent_message_by_rfc_message_id,
    send_reply,
)


class IntegrationAdapterTests(unittest.TestCase):
    def test_github_issue_redacts_email_and_token(self) -> None:
        value = sanitize_for_issue(
            "Customer jane@example.com saw a crash. API key: sk-secret-value"
        )
        self.assertNotIn("jane@example.com", value)
        self.assertNotIn("sk-secret-value", value)
        self.assertIn("[redacted-email]", value)
        self.assertIn("[redacted-secret]", value)

    def test_github_issue_title_can_be_sanitized(self) -> None:
        value = sanitize_for_issue("Customer bug: jane@example.com cannot sign in")
        self.assertEqual(value, "Customer bug: [redacted-email] cannot sign in")

    def test_plain_text_mime_part_is_decoded(self) -> None:
        encoded = base64.urlsafe_b64encode("Hello 客户".encode()).decode()
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [{"mimeType": "text/plain", "body": {"data": encoded}}],
        }
        self.assertEqual(_plain_text(payload), "Hello 客户")

    @patch("email_agent.tools.gmail.gmail_service")
    def test_reply_contains_thread_headers(self, gmail_service: MagicMock) -> None:
        gmail_service.return_value.users.return_value.messages.return_value.send.return_value.execute.return_value = {
            "id": "sent-1"
        }
        result = send_reply(
            recipient="customer@example.com",
            subject="Question",
            body="Reviewed answer",
            thread_id="thread-1",
            in_reply_to="<original@example.com>",
            references="<older@example.com>",
            message_id_header="<email-agent-task-17@email-agent.local>",
        )
        self.assertEqual(result, "sent-1")
        call = gmail_service.return_value.users.return_value.messages.return_value.send.call_args
        self.assertEqual(call.kwargs["body"]["threadId"], "thread-1")
        raw = base64.urlsafe_b64decode(call.kwargs["body"]["raw"]).decode()
        self.assertIn("In-Reply-To: <original@example.com>", raw)
        self.assertIn("References: <older@example.com> <original@example.com>", raw)
        self.assertIn("Message-ID: <email-agent-task-17@email-agent.local>", raw)

    @patch("email_agent.tools.gmail.gmail_service")
    def test_sent_message_can_be_reconciled_by_rfc_message_id(
        self, gmail_service: MagicMock
    ) -> None:
        gmail_service.return_value.users.return_value.messages.return_value.list.return_value.execute.return_value = {
            "messages": [{"id": "sent-17"}]
        }
        result = find_sent_message_by_rfc_message_id(
            "<email-agent-task-17@email-agent.local>"
        )
        self.assertEqual(result, "sent-17")
        call = gmail_service.return_value.users.return_value.messages.return_value.list.call_args
        self.assertEqual(
            call.kwargs["q"],
            "in:sent rfc822msgid:<email-agent-task-17@email-agent.local>",
        )


if __name__ == "__main__":
    unittest.main()
