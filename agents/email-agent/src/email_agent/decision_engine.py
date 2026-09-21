"""Provider-neutral decision engine contracts and the TypeSafe HTTP adapter."""

from __future__ import annotations

import json
import time
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from email_agent.config import Settings


TYPESAFE_ENDPOINT = "https://api.typesafe.ai/v1/systemone"


class DecisionEngine(Protocol):
    """Evaluate typed, atomic questions against shared state."""

    def evaluate(
        self,
        *,
        state: str | dict[str, Any] | list[str],
        questions: dict[str, dict[str, Any]],
    ) -> dict[str, Any]: ...


class TypeSafeDecisionEngine:
    """Small synchronous TypeSafe adapter with bounded overload retries."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout: float,
        max_attempts: int = 3,
    ) -> None:
        if not api_key:
            raise ValueError("TypeSafe API key cannot be empty.")
        if timeout <= 0:
            raise ValueError("TypeSafe timeout must be positive.")
        if max_attempts < 1:
            raise ValueError("TypeSafe max_attempts must be at least 1.")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_attempts = max_attempts

    @classmethod
    def from_settings(cls, settings: Settings) -> "TypeSafeDecisionEngine":
        if not settings.typesafe_api_key:
            raise RuntimeError(
                "TYPESAFE_API_KEY is missing. Set it in agents/email-agent/.env."
            )
        return cls(
            api_key=settings.typesafe_api_key,
            model=settings.typesafe_model,
            timeout=settings.typesafe_timeout_seconds,
        )

    def evaluate(
        self,
        *,
        state: str | dict[str, Any] | list[str],
        questions: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        if not questions:
            raise ValueError("At least one decision question is required.")
        payload = {
            "state": state,
            "model": self.model,
            "questions": questions,
        }
        request = Request(
            TYPESAFE_ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        for attempt in range(self.max_attempts):
            try:
                with urlopen(request, timeout=self.timeout) as http_response:
                    result = json.load(http_response)
                if not isinstance(result, dict):
                    raise ValueError("TypeSafe response must be a JSON object.")
                answers = result.get("answers")
                if not isinstance(answers, dict):
                    raise ValueError("TypeSafe response is missing an answers object.")
                return result
            except HTTPError as error:
                if error.code not in {429, 529} or attempt == self.max_attempts - 1:
                    detail = error.read().decode("utf-8", errors="replace")[:500]
                    raise RuntimeError(
                        f"TypeSafe request failed with HTTP {error.code}: {detail}"
                    ) from error
                time.sleep(0.5 * (2**attempt))
            except URLError as error:
                raise RuntimeError(f"TypeSafe request failed: {error.reason}") from error

        raise RuntimeError("TypeSafe request failed after retries.")
