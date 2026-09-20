"""Authorize the local Gmail account and store its OAuth token outside the repo."""

from __future__ import annotations

import argparse
import os
import json
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


PROJECT_DIR = Path(__file__).resolve().parents[1]
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]


def configured_path(name: str) -> Path:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is missing from the Email Agent environment.")
    return Path(value).expanduser()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Authorize Gmail OAuth scopes.")
    parser.add_argument(
        "--force-consent",
        action="store_true",
        help="Ignore the saved access token and show Google's consent flow again.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_dotenv(PROJECT_DIR / ".env")
    credentials_path = configured_path("GMAIL_CREDENTIALS_FILE")
    token_path = configured_path("GMAIL_TOKEN_FILE")
    if not credentials_path.is_file():
        raise FileNotFoundError(f"Gmail OAuth client file does not exist: {credentials_path}")

    credentials = None
    if token_path.is_file() and not args.force_consent:
        stored_scopes = set(json.loads(token_path.read_text()).get("scopes", []))
        if set(SCOPES).issubset(stored_scopes):
            credentials = Credentials.from_authorized_user_file(str(token_path), SCOPES)
            if credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())

    if credentials is None or not credentials.valid or not credentials.has_scopes(SCOPES):
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
        credentials = flow.run_local_server(
            host="localhost",
            port=0,
            open_browser=True,
            authorization_prompt_message="Open the following URL in your browser to authorize Gmail:\n{url}\n",
            success_message="Gmail authorization completed. You may close this tab.",
            include_granted_scopes="true",
            prompt="consent",
        )

    # Verify the authorized account with a read-only Gmail API request before saving.
    profile = build("gmail", "v1", credentials=credentials).users().getProfile(
        userId="me"
    ).execute()
    token_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    token_path.parent.chmod(0o700)
    os.umask(0o077)
    token_path.write_text(credentials.to_json())
    token_path.chmod(0o600)
    print(f"Gmail authorization verified for: {profile['emailAddress']}")
    print(f"OAuth token saved at: {token_path}")


if __name__ == "__main__":
    main()
