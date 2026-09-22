"""Authentication tests for the review UI."""

from __future__ import annotations

import unittest

from email_agent.auth import SESSION_MAX_AGE, ReviewAuth


class ReviewAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.auth = ReviewAuth(phone="any phone", password="any password", secret="test-secret")

    def test_credentials_are_unconstrained_but_exact(self) -> None:
        self.assertTrue(self.auth.verify_credentials("any phone", "any password"))
        self.assertFalse(self.auth.verify_credentials("any phone ", "any password"))

    def test_signed_session_round_trip_and_expiry(self) -> None:
        token = self.auth.issue_session("any phone", now=100)
        self.assertEqual(self.auth.verify_session(token, now=100), "any phone")
        self.assertIsNone(self.auth.verify_session(token, now=100 + SESSION_MAX_AGE + 1))

    def test_tampered_session_is_rejected(self) -> None:
        token = self.auth.issue_session("any phone", now=100)
        encoded, signature = token.split(".", 1)
        tampered = f"{encoded}.{signature[:-1]}0"
        self.assertIsNone(self.auth.verify_session(tampered, now=100))

    def test_csrf_token_is_bound_to_session(self) -> None:
        session = self.auth.issue_session("any phone")
        csrf = self.auth.issue_csrf(session)
        self.assertTrue(self.auth.verify_csrf(session, csrf))
        self.assertFalse(self.auth.verify_csrf(session + "x", csrf))


if __name__ == "__main__":
    unittest.main()
