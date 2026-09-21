"""Business nodes belong here once the email workflow is implemented."""
"""
Graph Nodes: Python functions receiving state and returning updates or dynamic transitions.
Implements: Transient retries, Saga compensations, and Human-in-the-loop (HITL).
"""

from typing import Literal
from langgraph.types import Command, interrupt
from langgraph.errors import NodeError
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

from email_agent.state import EmailAgentState
from email_agent.classification import classify_email
from email_agent.config import get_settings
from email_agent.evidence import evaluate_evidence
from email_agent.prompts import (
    CLARIFICATION_RESPONSE_TEMPLATE,
    DRAFT_RESPONSE_TEMPLATE,
)
from email_agent.response_validation import validate_response
from email_agent.tools import query_knowledge_base

settings = get_settings()
llm = ChatOpenAI(
    model=settings.model_name,
    base_url=settings.base_url,
    api_key=settings.deepseek_api_key,
    temperature=0,
)


def read_email_node(state: EmailAgentState) -> dict:
    """Ingest raw email."""
    return {
        "messages": [HumanMessage(content=f"Processing email: {state['email_content']}")]
    }


def triage_email_node(
    state: EmailAgentState,
) -> Command[Literal["search_documentation", "human_review"]]:
    """Make the first business-routing decision for an incoming email."""
    classification = classify_email(
        email_content=state["email_content"],
        email_subject=state["email_subject"],
        settings=settings,
    )

    goto = "human_review" if classification["needs_human"] else "search_documentation"
    return Command(update={"classification": classification}, goto=goto)


def triage_error_handler(
    state: EmailAgentState, error: NodeError
) -> Command[Literal["human_review"]]:
    """Fail closed to manual triage when the decision service is unavailable."""
    classification = {
        "intent": "question",
        "urgency": "medium",
        "complexity": "specialist",
        "topic": state.get("email_subject") or "Unclassified customer request",
        "summary": "Decision service failed; manual triage is required.",
        "intent_confidence": 0.0,
        "urgency_confidence": 0.0,
        "complexity_confidence": 0.0,
        "refund_requested": 0.0,
        "security_incident": 0.0,
        "human_requested": 0.0,
        "needs_human": True,
        "decision_model": f"decision-error: {str(error.error)[:160]}",
    }
    return Command(update={"classification": classification}, goto="human_review")


def search_documentation_node(
    state: EmailAgentState,
) -> Command[Literal["evaluate_evidence"]]:
    """Recall a broad candidate set; a later node decides what is usable."""
    intent = (state.get("classification") or {}).get("intent")
    category = {"bug": "bug", "billing": "billing", "feature": "feature"}.get(intent)
    results = query_knowledge_base(
        state["email_content"],
        category=category,
        limit=8,
    )
    return Command(update={"search_results": results}, goto="evaluate_evidence")


def search_error_handler(
    state: EmailAgentState, error: NodeError
) -> Command[Literal["draft_clarification"]]:
    """Saga compensation branch triggered upon retry budget exhaustion."""
    return Command(
        update={
            "search_results": [],
            "evidence_evaluation": {
                "evidence_sufficient": False,
                "requires_clarification": True,
                "prompt_injection_detected": False,
                "selected_indices": [],
                "answer_support": 0.0,
                "clarification_score": 1.0,
                "decision_model": f"search-error: {str(error.error)[:160]}",
            },
        },
        goto="draft_clarification",
    )


def evaluate_evidence_node(
    state: EmailAgentState,
) -> Command[Literal["draft_response", "draft_clarification"]]:
    """Rerank recalled passages and gate unsupported generation."""
    evaluation, selected = evaluate_evidence(
        customer_request=state["email_content"],
        passages=state.get("search_results") or [],
        settings=settings,
    )
    goto = "draft_response" if evaluation["evidence_sufficient"] else "draft_clarification"
    return Command(
        update={
            "search_results": selected,
            "evidence_evaluation": evaluation,
        },
        goto=goto,
    )


def evidence_error_handler(
    state: EmailAgentState, error: NodeError
) -> Command[Literal["draft_clarification"]]:
    """Never pass unreviewed retrieval results to the writing model."""
    evaluation = {
        "evidence_sufficient": False,
        "requires_clarification": True,
        "prompt_injection_detected": False,
        "selected_indices": [],
        "answer_support": 0.0,
        "clarification_score": 1.0,
        "decision_model": f"decision-error: {str(error.error)[:160]}",
    }
    return Command(
        update={"search_results": [], "evidence_evaluation": evaluation},
        goto="draft_clarification",
    )


def draft_response_node(state: EmailAgentState) -> Command[Literal["validate_response"]]:
    """Generate a grounded response from the accepted evidence only."""
    docs = "\n".join(state.get("search_results") or [])

    prompt = DRAFT_RESPONSE_TEMPLATE.format(
        email_content=state["email_content"],
        docs=docs,
    )
    response = llm.invoke(prompt)
    if not isinstance(response.content, str):
        raise TypeError("Drafting model returned non-text content.")

    return Command(
        update={"draft_response": response.content},
        goto="validate_response",
    )


def draft_clarification_node(
    state: EmailAgentState,
) -> Command[Literal["validate_response"]]:
    """Ask for missing information instead of inventing unsupported answers."""
    prompt = CLARIFICATION_RESPONSE_TEMPLATE.format(
        email_content=state["email_content"],
    )
    response = llm.invoke(prompt)
    if not isinstance(response.content, str):
        raise TypeError("Drafting model returned non-text content.")
    return Command(
        update={"draft_response": response.content},
        goto="validate_response",
    )


def validate_response_node(
    state: EmailAgentState,
) -> Command[Literal["human_review"]]:
    """Evaluate the generated draft before exposing it to a reviewer."""
    draft = state.get("draft_response")
    if not draft:
        raise ValueError("A draft is required before response validation.")
    validation = validate_response(
        customer_request=state["email_content"],
        evidence=state.get("search_results") or [],
        draft_response=draft,
        settings=settings,
    )

    # Sending remains human-approved in this phase even when Jev marks the draft safe.
    return Command(update={"response_validation": validation}, goto="human_review")


def validation_error_handler(
    state: EmailAgentState, error: NodeError
) -> Command[Literal["human_review"]]:
    """A draft that could not be validated must be reviewed manually."""
    validation = {
        "addresses_issue": 0.0,
        "grounded_or_cautious": 0.0,
        "unauthorized_commitment": 1.0,
        "requests_sensitive_data": 1.0,
        "quality_score": 0.0,
        "safe_to_send": False,
        "review_reasons": [
            f"Automated response validation failed: {str(error.error)[:160]}"
        ],
        "decision_model": "decision-error",
    }
    return Command(update={"response_validation": validation}, goto="human_review")


def human_review_node(state: EmailAgentState) -> Command[Literal["send_reply", "__end__"]]:
    """
    Human-in-the-loop approval node.
    Rule: interrupt() must come first because code before it re-executes upon resume.
    """
    classification = state.get("classification") or {}
    evidence = state.get("evidence_evaluation") or {}
    validation = state.get("response_validation") or {}

    human_decision = interrupt({
        "email_id": state.get("email_id"),
        "original_email": state.get("email_content"),
        "draft_response": state.get("draft_response"),
        "urgency": classification.get("urgency"),
        "triage_needs_human": classification.get("needs_human"),
        "evidence_sufficient": evidence.get("evidence_sufficient"),
        "response_safe": validation.get("safe_to_send"),
        "review_reasons": validation.get("review_reasons", []),
        "action": "Please review and approve or edit this response.",
    })

    if human_decision.get("approved"):
        final_reply = human_decision.get("edited_response") or state.get("draft_response")
        if not final_reply:
            raise ValueError("Manual triage requires a reviewer-authored response.")
        return Command(update={"draft_response": final_reply}, goto="send_reply")

    return Command(update={}, goto="__end__")


def send_reply_node(state: EmailAgentState) -> dict:
    """Dispatch email."""
    print(f"\n[Email Dispatched to {state.get('sender_email')}]:\n{state.get('draft_response')}\n")
    return {}
