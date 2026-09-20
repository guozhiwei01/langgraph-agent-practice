"""Create privacy-minimized GitHub Issues after explicit human approval."""

from __future__ import annotations

import json
import re
import subprocess
import urllib.request

from email_agent.config import get_settings


def create_issue(*, title: str, description: str) -> tuple[str, str]:
    settings = get_settings()
    repository = settings.github_repository
    if not repository:
        raise RuntimeError("GITHUB_REPOSITORY is missing from the Email Agent environment.")
    body = (
        "This issue was created from an approved Email Agent review.\n\n"
        "Customer identifiers and raw email headers have been intentionally omitted.\n\n"
        f"## Sanitized report\n\n{sanitize_for_issue(description)}"
    )
    token = settings.github_token or _gh_cli_token()
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/issues",
        data=json.dumps({"title": title, "body": body}).encode(),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.load(response)
    return str(payload["number"]), payload["html_url"]


def sanitize_for_issue(text: str) -> str:
    """Remove common customer identifiers and secrets before GitHub transmission."""
    value = re.sub(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "[redacted-email]",
        text,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"(?i)\b(api[_ -]?key|token|authorization)\s*[:=]\s*\S+",
        r"\1: [redacted-secret]",
        value,
    )
    return value.strip()[:4000]


def _gh_cli_token() -> str:
    result = subprocess.run(
        ["gh", "auth", "token"], check=True, capture_output=True, text=True
    )
    token = result.stdout.strip()
    if not token:
        raise RuntimeError("GitHub authentication is unavailable.")
    return token
