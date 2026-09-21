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
    intent: Literal["question", "bug", "billing", "feature", "other"]
    urgency: Literal["low", "medium", "high", "critical"]
    complexity: Literal["standard", "investigation", "specialist"]
    topic: str
    summary: str
    intent_confidence: float
    urgency_confidence: float
    complexity_confidence: float
    refund_requested: float
    security_incident: float
    human_requested: float
    needs_human: bool
    decision_model: str


class EmailClassificationPayload(BaseModel):
    """Runtime validation for the decision classifier result."""

    intent: Literal["question", "bug", "billing", "feature", "other"]
    urgency: Literal["low", "medium", "high", "critical"]
    complexity: Literal["standard", "investigation", "specialist"]
    topic: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    intent_confidence: float = Field(ge=0, le=1)
    urgency_confidence: float = Field(ge=0, le=1)
    complexity_confidence: float = Field(ge=0, le=1)
    refund_requested: float = Field(ge=0, le=1)
    security_incident: float = Field(ge=0, le=1)
    human_requested: float = Field(ge=0, le=1)
    needs_human: bool
    decision_model: str = Field(min_length=1)


class EvidenceEvaluation(TypedDict):
    """Decision record for retrieved knowledge candidates."""

    evidence_sufficient: bool
    requires_clarification: bool
    prompt_injection_detected: bool
    selected_indices: list[int]
    answer_support: float
    clarification_score: float
    decision_model: str


class EvidenceEvaluationPayload(BaseModel):
    evidence_sufficient: bool
    requires_clarification: bool
    prompt_injection_detected: bool
    selected_indices: list[int]
    answer_support: float = Field(ge=0, le=1)
    clarification_score: float = Field(ge=0, le=1)
    decision_model: str = Field(min_length=1)


class ResponseValidation(TypedDict):
    """Decision record produced after the response-writing model runs."""

    addresses_issue: float
    grounded_or_cautious: float
    unauthorized_commitment: float
    requests_sensitive_data: float
    quality_score: float
    safe_to_send: bool
    review_reasons: list[str]
    decision_model: str


class ResponseValidationPayload(BaseModel):
    addresses_issue: float = Field(ge=0, le=1)
    grounded_or_cautious: float = Field(ge=0, le=1)
    unauthorized_commitment: float = Field(ge=0, le=1)
    requests_sensitive_data: float = Field(ge=0, le=1)
    quality_score: float = Field(ge=0, le=3)
    safe_to_send: bool
    review_reasons: list[str]
    decision_model: str = Field(min_length=1)
