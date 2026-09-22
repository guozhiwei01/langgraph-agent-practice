"""HTTP-level checks for authentication, CSRF and queued actions."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from email_agent.auth import SESSION_COOKIE, ReviewAuth
from email_agent.web import app


class WebAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.auth = ReviewAuth("138", "password", "secret")
        app.state.review_auth = self.auth
        self.client = TestClient(app)

    def _login(self) -> None:
        with patch("email_agent.web.record_audit_event"):
            response = self.client.post(
                "/login",
                data={"phone": "138", "password": "password"},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertIn(SESSION_COOKIE, self.client.cookies)

    def test_home_redirects_to_login_without_session(self) -> None:
        response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")

    def test_authenticated_home_renders_csrf_protected_forms(self) -> None:
        self._login()
        with (
            patch("email_agent.web.list_review_tasks", return_value=[]),
            patch("email_agent.web.list_dead_jobs", return_value=[]),
        ):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('name="csrf_token"', response.text)
        self.assertIn("Queue Gmail sync", response.text)

    def test_sync_rejects_missing_csrf(self) -> None:
        self._login()
        response = self.client.post("/sync", data={})
        self.assertEqual(response.status_code, 422)

    def test_sync_enqueues_job_with_valid_csrf(self) -> None:
        self._login()
        session = self.client.cookies[SESSION_COOKIE]
        csrf = self.auth.issue_csrf(session)
        with (
            patch("email_agent.web.enqueue_job", return_value=(23, True)) as enqueue,
            patch("email_agent.web.record_audit_event"),
        ):
            response = self.client.post(
                "/sync", data={"csrf_token": csrf}, follow_redirects=False
            )
        self.assertEqual(response.status_code, 303)
        self.assertIn("job+23", response.headers["location"])
        self.assertEqual(enqueue.call_args.args[0], "sync_gmail")


if __name__ == "__main__":
    unittest.main()
