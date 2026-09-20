"""
Data schemas and contracts for structured LLM outputs.
Design Pattern: Schema definition using TypedDict / Pydantic.
"""

from typing import Literal, TypedDict

from pydantic import BaseModel, Field


class EmailRequest(BaseModel):
    """Public request accepted by the application service."""

    email_id: str = Field(min_length=1)
    sender_email: str = Field(min_length=3)
    subject: str = ""
    email_content: str = Field(min_length=1)
    provider_thread_id: str | None = None
    task_id: int | None = None


class EmailResult(BaseModel):
    """Public processing result without exposing LangGraph internals."""

    task_id: int
    status: Literal["waiting_for_review", "sent", "rejected", "failed"]
    draft_response: str | None = None


class EmailClassification(TypedDict):
    """Structured extraction output schema."""
    intent: Literal["question", "bug", "billing", "feature", "complex"]
    urgency: Literal["low", "medium", "high", "critical"]
    topic: str
    summary: str


class EmailClassificationPayload(BaseModel):
    """Runtime validation for model-generated classification JSON."""

    intent: Literal["question", "bug", "billing", "feature", "complex"]
    urgency: Literal["low", "medium", "high", "critical"]
    topic: str = Field(min_length=1)
    summary: str = Field(min_length=1)
