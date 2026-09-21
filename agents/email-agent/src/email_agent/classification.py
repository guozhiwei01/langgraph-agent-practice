"""Business-level email triage backed by a replaceable decision engine."""

from __future__ import annotations

from typing import Literal, cast

from pydantic import BaseModel, Field

from email_agent.config import Settings, get_settings
from email_agent.decision_engine import DecisionEngine, TypeSafeDecisionEngine
from email_agent.schemas import EmailClassification, EmailClassificationPayload


URGENCY_LEVELS = ("low", "medium", "high", "critical")
COMPLEXITY_LEVELS = ("standard", "investigation", "specialist")


class ChoiceAnswer(BaseModel):
    type: Literal["choice"]
    choice: str
    confidence: float = Field(ge=0, le=1)
    probabilities: dict[str, float]


class ScoreAnswer(BaseModel):
    type: Literal["score"]
    score: float
    confidence: float = Field(ge=0, le=1)
    probabilities: dict[str, float]


class NoulAnswer(BaseModel):
    type: Literal["noul"]
    noul: float = Field(ge=0, le=1)


def classify_email(
    *,
    email_content: str,
    email_subject: str,
    settings: Settings | None = None,
    engine: DecisionEngine | None = None,
) -> EmailClassification:
    """Triage one email using a single multi-question decision request."""
    config = settings or get_settings()
    decision_engine = engine or TypeSafeDecisionEngine.from_settings(config)
    response = decision_engine.evaluate(
        state={
            "subject": email_subject,
            "content": email_content,
        },
        questions=triage_questions(),
    )

    answers = response.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("TypeSafe response is missing an answers object.")

    intent = ChoiceAnswer.model_validate(answers.get("intent"))
    urgency = ScoreAnswer.model_validate(answers.get("urgency"))
    complexity = ScoreAnswer.model_validate(answers.get("complexity"))
    refund = NoulAnswer.model_validate(answers.get("refund_requested"))
    security = NoulAnswer.model_validate(answers.get("security_incident"))
    human = NoulAnswer.model_validate(answers.get("human_requested"))

    urgency_index = _nearest_level(urgency.score, len(URGENCY_LEVELS))
    complexity_index = _nearest_level(complexity.score, len(COMPLEXITY_LEVELS))
    risk_threshold = config.typesafe_decision_threshold

    classification = EmailClassificationPayload(
        intent=intent.choice,
        urgency=URGENCY_LEVELS[urgency_index],
        complexity=COMPLEXITY_LEVELS[complexity_index],
        topic=_topic(email_subject),
        summary=_summary(email_content),
        intent_confidence=intent.confidence,
        urgency_confidence=urgency.confidence,
        complexity_confidence=complexity.confidence,
        refund_requested=refund.noul,
        security_incident=security.noul,
        human_requested=human.noul,
        needs_human=(
            intent.choice == "other"
            or intent.confidence < config.typesafe_min_confidence
            or complexity_index == len(COMPLEXITY_LEVELS) - 1
            or urgency_index == len(URGENCY_LEVELS) - 1
            or refund.noul >= risk_threshold
            or security.noul >= risk_threshold
            or human.noul >= risk_threshold
        ),
        decision_model=str(response.get("model") or config.typesafe_model),
    )
    return cast(EmailClassification, classification.model_dump())


def triage_questions() -> dict[str, dict]:
    return {
        "intent": {
            "type": "choice",
            "instructions": (
                "Select the primary customer request in `content`, using `subject` "
                "only as supporting context."
            ),
            "criteria": {
                "question": "General product, account, or usage question",
                "bug": "Software malfunction, outage, or incorrect behavior",
                "billing": "Payment, subscription, invoice, charge, or refund issue",
                "feature": "Request for new or changed product functionality",
                "other": "The primary request does not fit any listed category",
            },
        },
        "urgency": {
            "type": "score",
            "instructions": (
                "Rate how urgently the request in `content` needs attention, using "
                "`subject` only as supporting context."
            ),
            "criteria": [
                "No immediate impact; normal queue is appropriate",
                "Normal business impact; timely response is needed",
                "Significant ongoing impact or likely financial loss",
                "Security incident, major financial loss, or complete outage",
            ],
        },
        "complexity": {
            "type": "score",
            "instructions": (
                "Rate the complexity required to resolve the request in `content`."
            ),
            "criteria": [
                "Standard documentation or deterministic procedure is sufficient",
                "Investigation or several coordinated steps are required",
                "Specialist judgment, authorization, or human escalation is required",
            ],
        },
        "refund_requested": {
            "type": "noul",
            "instructions": (
                "Does `content` explicitly ask for a refund, reimbursement, charge "
                "reversal, or other financial remedy?"
            ),
        },
        "security_incident": {
            "type": "noul",
            "instructions": (
                "Does `content` report account compromise, credential exposure, "
                "data leakage, fraud, or another security incident?"
            ),
        },
        "human_requested": {
            "type": "noul",
            "instructions": (
                "Does `content` explicitly ask to speak with a human agent or specialist?"
            ),
        },
    }


def _nearest_level(score: float, level_count: int) -> int:
    return max(0, min(level_count - 1, int(score + 0.5)))


def _topic(subject: str) -> str:
    return " ".join(subject.split())[:120] or "Customer request"


def _summary(content: str) -> str:
    return " ".join(content.split())[:1000] or "No email content supplied."
