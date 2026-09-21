"""Post-generation quality and safety decisions for response drafts."""

from __future__ import annotations

from typing import Literal, cast

from pydantic import BaseModel, Field

from email_agent.config import Settings, get_settings
from email_agent.decision_engine import DecisionEngine, TypeSafeDecisionEngine
from email_agent.schemas import ResponseValidation, ResponseValidationPayload


class NoulAnswer(BaseModel):
    type: Literal["noul"]
    noul: float = Field(ge=0, le=1)


class ScoreAnswer(BaseModel):
    type: Literal["score"]
    score: float = Field(ge=0, le=3)
    confidence: float = Field(ge=0, le=1)
    probabilities: dict[str, float]


def validate_response(
    *,
    customer_request: str,
    evidence: list[str],
    draft_response: str,
    settings: Settings | None = None,
    engine: DecisionEngine | None = None,
) -> ResponseValidation:
    """Assess a generated draft; application code retains the final authority."""
    config = settings or get_settings()
    decision_engine = engine or TypeSafeDecisionEngine.from_settings(config)
    response = decision_engine.evaluate(
        state={
            "customer_request": customer_request,
            "approved_evidence": evidence,
            "draft_response": draft_response,
        },
        questions=_validation_questions(),
    )
    answers = response["answers"]
    addresses = NoulAnswer.model_validate(answers.get("addresses_issue"))
    grounded = NoulAnswer.model_validate(answers.get("grounded_or_cautious"))
    commitment = NoulAnswer.model_validate(answers.get("unauthorized_commitment"))
    sensitive = NoulAnswer.model_validate(answers.get("requests_sensitive_data"))
    quality = ScoreAnswer.model_validate(answers.get("quality"))
    threshold = config.typesafe_decision_threshold
    risk_ceiling = 1 - threshold

    reasons: list[str] = []
    if addresses.noul < threshold:
        reasons.append("Draft may not fully address the customer request.")
    if grounded.noul < threshold:
        reasons.append("Draft may contain unsupported claims or insufficient caution.")
    if commitment.noul > risk_ceiling:
        reasons.append("Draft may make an unauthorized commitment.")
    if sensitive.noul > risk_ceiling:
        reasons.append("Draft may request credentials or unnecessary sensitive data.")
    if quality.score < 2:
        reasons.append("Draft quality is below the send-ready threshold.")

    validation = ResponseValidationPayload(
        addresses_issue=addresses.noul,
        grounded_or_cautious=grounded.noul,
        unauthorized_commitment=commitment.noul,
        requests_sensitive_data=sensitive.noul,
        quality_score=quality.score,
        safe_to_send=not reasons,
        review_reasons=reasons,
        decision_model=str(response.get("model") or config.typesafe_model),
    )
    return cast(ResponseValidation, validation.model_dump())


def _validation_questions() -> dict[str, dict]:
    return {
        "addresses_issue": {
            "type": "noul",
            "instructions": (
                "Does `draft_response` directly address the main request in "
                "`customer_request`?"
            ),
        },
        "grounded_or_cautious": {
            "type": "noul",
            "instructions": (
                "Is every factual claim and recommended action in `draft_response` "
                "supported by `approved_evidence`, or does `draft_response` clearly "
                "avoid asserting unsupported facts and ask an appropriate clarifying "
                "question?"
            ),
        },
        "unauthorized_commitment": {
            "type": "noul",
            "instructions": (
                "Does `draft_response` promise a refund, compensation, deadline, "
                "account change, or other action whose authorization is not present "
                "in `approved_evidence`?"
            ),
        },
        "requests_sensitive_data": {
            "type": "noul",
            "instructions": (
                "Does `draft_response` ask for a password, authentication token, secret "
                "key, full payment credential, or unnecessary sensitive data?"
            ),
        },
        "quality": {
            "type": "score",
            "instructions": (
                "Rate `draft_response` for accuracy and completeness against "
                "`customer_request` and `approved_evidence`, as well as clarity and tone."
            ),
            "criteria": [
                "Unsafe or unusable",
                "Requires substantial revision",
                "Useful with minor review",
                "Clear, complete, and send-ready",
            ],
        },
    }
