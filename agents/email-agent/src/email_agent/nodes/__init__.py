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
from email_agent.storage import (
    get_sent_outbound,
    get_task,
    get_tool_success,
    mark_outbound_sending,
    mark_task_sent,
    prepare_outbound,
    record_review,
    record_tool_success,
)
from email_agent.tools import query_knowledge_base
from email_agent.tools.github_issues import create_issue, sanitize_for_issue
from email_agent.tools.gmail import (
    find_sent_message_by_rfc_message_id,
    send_reply as send_gmail_reply,
)

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


def human_review_node(
    state: EmailAgentState,
) -> Command[Literal["send_reply", "record_rejection"]]:
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
        original_reply = state.get("draft_response") or ""
        decision = "edited" if final_reply != original_reply else "approved"
        return Command(
            update={
                "draft_response": final_reply,
                "review_decision": decision,
                "reviewer_id": human_decision.get("reviewer_id") or "local-reviewer",
                "review_reason": None,
            },
            goto="send_reply",
        )

    return Command(
        update={
            "review_decision": "rejected",
            "reviewer_id": human_decision.get("reviewer_id") or "local-reviewer",
            "review_reason": human_decision.get("reason") or "Rejected during local review.",
        },
        goto="record_rejection",
    )


def record_rejection_node(state: EmailAgentState) -> dict:
    """Record a rejected interrupt decision before completing the graph."""
    task_id = state.get("task_id")
    if task_id is None:
        raise ValueError("task_id is required to record a review.")
    record_review(
        task_id,
        reviewer_id=state.get("reviewer_id") or "local-reviewer",
        decision="rejected",
        original_draft=state.get("draft_response"),
        final_response=None,
        reason=state.get("review_reason") or "Rejected during local review.",
    )
    return {}


def send_reply_node(state: EmailAgentState) -> dict:
    """Dispatch an approved reply and persist the externally visible result."""
    task_id = state.get("task_id")
    if task_id is None:
        raise ValueError("task_id is required to send an approved reply.")

    task = get_task(task_id)
    if task is None:
        raise LookupError(f"Task {task_id} does not exist.")
    existing = get_sent_outbound(task_id)
    if existing:
        return {
            "draft_response": existing["body"],
            "provider_message_id": existing["provider_message_id"],
        }

    final_response = (state.get("draft_response") or "").strip()
    if not final_response:
        raise ValueError("An approved response cannot be empty.")

    github_issue_url = None
    classification = state.get("classification") or {}
    if classification.get("intent") == "bug":
        issue = _ensure_github_issue(task_id, classification)
        github_issue_url = issue["url"]
        final_response += f"\n\nEngineering reference: {github_issue_url}"

    rfc_message_id = f"<email-agent-task-{task_id}@email-agent.local>"
    outbound = prepare_outbound(
        task_id,
        recipient=task["sender_email"],
        subject=task["subject"],
        body=final_response,
        idempotency_key=f"task:{task_id}:gmail-reply",
        rfc_message_id=rfc_message_id,
    )
    final_response = outbound["body"]
    if outbound["status"] == "sent":
        return {
            "draft_response": final_response,
            "provider_message_id": outbound["provider_message_id"],
            "github_issue_url": github_issue_url,
        }

    reconciled_message_id = find_sent_message_by_rfc_message_id(
        outbound["rfc_message_id"]
    )
    if reconciled_message_id:
        record_review(
            task_id,
            reviewer_id=state.get("reviewer_id") or "local-reviewer",
            decision=state.get("review_decision") or "approved",
            original_draft=task.get("draft_response"),
            final_response=final_response,
        )
        mark_task_sent(
            task_id,
            recipient=outbound["recipient_email"],
            subject=outbound["subject"],
            body=final_response,
            provider_message_id=reconciled_message_id,
        )
        return {
            "draft_response": final_response,
            "provider_message_id": reconciled_message_id,
            "github_issue_url": github_issue_url,
        }

    metadata = task.get("raw_metadata") or {}
    mark_outbound_sending(task_id)
    message_id = send_gmail_reply(
        recipient=task["sender_email"],
        subject=task["subject"],
        body=final_response,
        thread_id=task["provider_thread_id"],
        in_reply_to=metadata.get("rfc_message_id"),
        references=metadata.get("references"),
        message_id_header=outbound["rfc_message_id"],
    )
    record_review(
        task_id,
        reviewer_id=state.get("reviewer_id") or "local-reviewer",
        decision=state.get("review_decision") or "approved",
        original_draft=task.get("draft_response"),
        final_response=final_response,
    )
    mark_task_sent(
        task_id,
        recipient=task["sender_email"],
        subject=task["subject"],
        body=final_response,
        provider_message_id=message_id,
    )
    return {
        "draft_response": final_response,
        "provider_message_id": message_id,
        "github_issue_url": github_issue_url,
    }


def _ensure_github_issue(task_id: int, classification: dict) -> dict:
    """Create one privacy-minimized issue for an approved bug task."""
    key = f"task:{task_id}:github-issue"
    existing = get_tool_success(key)
    if existing:
        return existing
    number, url = create_issue(
        title=sanitize_for_issue(
            f"Customer bug: {classification.get('topic', 'Uncategorized issue')}"
        ),
        description=classification.get("summary", "No sanitized summary available."),
    )
    response = {"number": number, "url": url}
    record_tool_success(
        task_id,
        tool_name="github_issue",
        idempotency_key=key,
        response=response,
    )
    return response
