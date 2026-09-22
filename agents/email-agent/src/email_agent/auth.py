"""Small, dependency-free authentication helper for the local review UI."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time
from dataclasses import dataclass

from email_agent.config import Settings


SESSION_COOKIE = "email_agent_session"
SESSION_MAX_AGE = 8 * 60 * 60


@dataclass(frozen=True)
class ReviewAuth:
    """Credentials and signing key for the single local review account."""

    phone: str
    password: str
    secret: str

    @classmethod
    def from_settings(cls, settings: Settings) -> "ReviewAuth":
        missing = [
            name
            for name, value in (
                ("REVIEW_PHONE", settings.review_phone),
                ("REVIEW_PASSWORD", settings.review_password),
                ("SESSION_SECRET", settings.session_secret),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "Review login is not configured. Set " + ", ".join(missing) + " in .env."
            )
        return cls(settings.review_phone or "", settings.review_password or "", settings.session_secret or "")

    def verify_credentials(self, phone: str, password: str) -> bool:
        """Compare credentials without imposing a phone/password format."""
        return hmac.compare_digest(phone, self.phone) and hmac.compare_digest(
            password, self.password
        )

    def issue_session(self, phone: str, now: int | None = None) -> str:
        timestamp = int(time.time() if now is None else now)
        payload = f"{phone}|{timestamp}"
        signature = self._sign(payload)
        encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
        return f"{encoded}.{signature}"

    def verify_session(self, token: str | None, now: int | None = None) -> str | None:
        if not token or "." not in token:
            return None
        encoded, signature = token.rsplit(".", 1)
        try:
            payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
            phone, timestamp_text = payload.rsplit("|", 1)
            timestamp = int(timestamp_text)
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return None
        current = int(time.time() if now is None else now)
        if not hmac.compare_digest(signature, self._sign(payload)):
            return None
        if timestamp > current or current - timestamp > SESSION_MAX_AGE:
            return None
        if not hmac.compare_digest(phone, self.phone):
            return None
        return phone

    def issue_csrf(self, session_token: str) -> str:
        return self._sign(f"csrf|{session_token}")

    def verify_csrf(self, session_token: str | None, csrf_token: str | None) -> bool:
        if not session_token or not csrf_token or self.verify_session(session_token) is None:
            return False
        return hmac.compare_digest(csrf_token, self.issue_csrf(session_token))

    def _sign(self, payload: str) -> str:
        return hmac.new(self.secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
