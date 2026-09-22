"""Environment configuration for the email Agent."""

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    deepseek_api_key: str
    model_name: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com/v1"
    typesafe_api_key: Optional[str] = None
    typesafe_model: str = "jev-1.13.0"
    typesafe_min_confidence: float = 0.7
    typesafe_decision_threshold: float = 0.7
    typesafe_timeout_seconds: float = 10.0
    database_url: Optional[str] = None
    dashscope_api_key: Optional[str] = None
    gmail_credentials_file: Optional[Path] = None
    gmail_token_file: Optional[Path] = None
    gmail_user_id: str = "me"
    gmail_label: str = "email-agent"
    github_token: Optional[str] = None
    github_repository: Optional[str] = None
    review_phone: Optional[str] = None
    review_password: Optional[str] = None
    session_secret: Optional[str] = None
    worker_poll_seconds: float = 1.0
    worker_stale_seconds: int = 300
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        for name, value in (
            ("typesafe_min_confidence", self.typesafe_min_confidence),
            ("typesafe_decision_threshold", self.typesafe_decision_threshold),
        ):
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1.")
        if self.typesafe_timeout_seconds <= 0:
            raise ValueError("typesafe_timeout_seconds must be positive.")
        if self.worker_poll_seconds <= 0 or self.worker_stale_seconds <= 0:
            raise ValueError("Worker timing settings must be positive.")


def get_settings() -> Settings:
    """Load this Agent's private .env file without relying on the shell cwd."""
    load_dotenv(PROJECT_DIR / ".env")

    deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")

    if not deepseek_api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY is missing. Copy .env.example to .env and set it."
        )

    return Settings(
        deepseek_api_key=deepseek_api_key,
        typesafe_api_key=os.getenv("TYPESAFE_API_KEY"),
        typesafe_model=os.getenv("TYPESAFE_MODEL", "jev-1.13.0"),
        typesafe_min_confidence=float(
            os.getenv("TYPESAFE_MIN_CONFIDENCE", "0.7")
        ),
        typesafe_decision_threshold=float(
            os.getenv("TYPESAFE_DECISION_THRESHOLD", "0.7")
        ),
        typesafe_timeout_seconds=float(
            os.getenv("TYPESAFE_TIMEOUT_SECONDS", "10")
        ),
        database_url=os.getenv("DATABASE_URL"),
        dashscope_api_key=os.getenv("DASHSCOPE_API_KEY"),
        gmail_credentials_file=_optional_path(os.getenv("GMAIL_CREDENTIALS_FILE")),
        gmail_token_file=_optional_path(os.getenv("GMAIL_TOKEN_FILE")),
        gmail_user_id=os.getenv("GMAIL_USER_ID", "me"),
        gmail_label=os.getenv("GMAIL_LABEL", "email-agent"),
        github_token=os.getenv("GITHUB_TOKEN"),
        github_repository=os.getenv("GITHUB_REPOSITORY"),
        review_phone=os.getenv("REVIEW_PHONE"),
        review_password=os.getenv("REVIEW_PASSWORD"),
        session_secret=os.getenv("SESSION_SECRET"),
        worker_poll_seconds=float(os.getenv("WORKER_POLL_SECONDS", "1")),
        worker_stale_seconds=int(os.getenv("WORKER_STALE_SECONDS", "300")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )


def _optional_path(value: Optional[str]) -> Optional[Path]:
    """Convert a configured file path while preserving an unset value."""
    return Path(value).expanduser() if value else None
