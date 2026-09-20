"""Configuration owned by the arithmetic Agent only."""

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    deepseek_api_key: str
    model_name: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com/v1"


def get_settings() -> Settings:
    """Load this Agent's private .env file without relying on the shell cwd."""
    load_dotenv(PROJECT_DIR / ".env")
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY is missing. Copy .env.example to .env and set it."
        )
    return Settings(deepseek_api_key=api_key)
