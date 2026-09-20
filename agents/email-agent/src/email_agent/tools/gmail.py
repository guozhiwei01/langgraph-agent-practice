"""Gmail API adapter for labeled inbound mail and reviewed outbound replies."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr
import json
import os
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from email_agent.config import get_settings


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]


def gmail_service():
    settings = get_settings()
    token_path = settings.gmail_token_file
    if token_path is None or not token_path.is_file():
        raise RuntimeError("Gmail token is missing. Run scripts/authorize_gmail.py first.")
    credentials = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not credentials.has_scopes(SCOPES):
        raise RuntimeError("Gmail token lacks gmail.modify or gmail.send; reauthorize Gmail.")
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        os.umask(0o077)
        token_path.write_text(credentials.to_json())
        token_path.chmod(0o600)
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def ensure_processing_label() -> str:
    settings = get_settings()
    service = gmail_service()
    labels = service.users().labels().list(userId=settings.gmail_user_id).execute().get("labels", [])
    for label in labels:
        if label.get("name") == settings.gmail_label:
            return label["id"]
    created = service.users().labels().create(
        userId=settings.gmail_user_id,
        body={
            "name": settings.gmail_label,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        },
    ).execute()
    return created["id"]


def list_unread_labeled_message_ids(limit: int = 20) -> list[str]:
    settings = get_settings()
    label_id = ensure_processing_label()
    response = gmail_service().users().messages().list(
        userId=settings.gmail_user_id,
        labelIds=[label_id, "UNREAD"],
        maxResults=limit,
    ).execute()
    return [item["id"] for item in response.get("messages", [])]


def _decode_body(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode()).decode("utf-8", errors="replace")


def _plain_text(payload: dict[str, Any]) -> str:
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return _decode_body(payload["body"]["data"])
    for part in payload.get("parts", []):
        text = _plain_text(part)
        if text:
            return text
    if payload.get("body", {}).get("data"):
        return _decode_body(payload["body"]["data"])
    return ""


def fetch_message(message_id: str) -> dict[str, Any]:
    settings = get_settings()
    message = gmail_service().users().messages().get(
        userId=settings.gmail_user_id, id=message_id, format="full"
    ).execute()
    headers = {
        item["name"].lower(): item["value"]
        for item in message.get("payload", {}).get("headers", [])
    }
    received_at = datetime.fromtimestamp(int(message["internalDate"]) / 1000, timezone.utc)
    return {
        "message_id": message["id"],
        "thread_id": message.get("threadId"),
        "sender": parseaddr(headers.get("from", ""))[1],
        "recipient": parseaddr(headers.get("to", ""))[1],
        "subject": headers.get("subject", ""),
        "body": _plain_text(message.get("payload", {})).strip(),
        "received_at": received_at,
        "metadata": json.dumps(
            {
                "gmail_label_ids": message.get("labelIds", []),
                "rfc_message_id": headers.get("message-id"),
                "references": headers.get("references"),
            }
        ),
    }


def mark_message_read(message_id: str) -> None:
    settings = get_settings()
    gmail_service().users().messages().modify(
        userId=settings.gmail_user_id,
        id=message_id,
        body={"removeLabelIds": ["UNREAD"]},
    ).execute()


def send_reply(
    *,
    recipient: str,
    subject: str,
    body: str,
    thread_id: str | None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> str:
    settings = get_settings()
    message = EmailMessage()
    message["To"] = recipient
    message["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = f"{references} {in_reply_to}".strip() if references else in_reply_to
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    request: dict[str, Any] = {"raw": raw}
    if thread_id:
        request["threadId"] = thread_id
    sent = gmail_service().users().messages().send(
        userId=settings.gmail_user_id, body=request
    ).execute()
    return sent["id"]
