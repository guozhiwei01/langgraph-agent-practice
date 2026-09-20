"""Business nodes belong here once the email workflow is implemented."""
"""
Graph Nodes: Python functions receiving state and returning updates or dynamic transitions.
Implements: Transient retries, Saga compensations, and Human-in-the-loop (HITL).
"""

from typing import Literal
import json
from langgraph.types import Command, interrupt
from langgraph.errors import NodeError
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

from email_agent.state import EmailAgentState
from email_agent.schemas import EmailClassification, EmailClassificationPayload
from email_agent.config import get_settings
from email_agent.prompts import CLASSIFICATION_PROMPT_TEMPLATE, DRAFT_RESPONSE_TEMPLATE
from email_agent.tools import file_issue_ticket, query_knowledge_base

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


def classify_intent_node(
    state: EmailAgentState,
) -> Command[Literal["search_documentation"]]:
    """Classify email intent and dynamically route execution."""
    prompt = CLASSIFICATION_PROMPT_TEMPLATE.format(
        email_content=state["email_content"],
        sender_email=state["sender_email"],
    )
    response = llm.invoke(prompt)
    content = response.content
    if not isinstance(content, str):
        raise TypeError("Classification model returned non-text content.")
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Classification model did not return a JSON object.")
    classification: EmailClassification = EmailClassificationPayload.model_validate(
        json.loads(content[start : end + 1])
    ).model_dump()

    # Every category searches available guidance before drafting. All drafts are
    # reviewed by a human in this local integration phase.
    return Command(update={"classification": classification}, goto="search_documentation")


def search_documentation_node(state: EmailAgentState) -> Command[Literal["draft_response"]]:
    """Retrieve documentation with retry capability."""
    intent = (state.get("classification") or {}).get("intent")
    category = {"bug": "bug", "billing": "billing", "feature": "feature"}.get(intent)
    results = query_knowledge_base(state["email_content"], category=category)
    return Command(update={"search_results": results}, goto="draft_response")


def search_error_handler(state: EmailAgentState, error: NodeError) -> Command[Literal["draft_response"]]:
    """Saga compensation branch triggered upon retry budget exhaustion."""
    fallback_doc = [f"Search API degraded ({str(error.error)}). Applying fallback runbook."]
    return Command(update={"search_results": fallback_doc}, goto="draft_response")


def bug_tracking_node(state: EmailAgentState) -> Command[Literal["draft_response"]]:
    """Action node to create tickets in bug tracker."""
    ticket_id = file_issue_ticket("Customer Bug", state["email_content"])
    return Command(
        update={"search_results": [f"Ticket {ticket_id} created"]},
        goto="draft_response",
    )


def draft_response_node(state: EmailAgentState) -> Command[Literal["human_review", "send_reply"]]:
    """Generate response and determine review requirements."""
    classification = state.get("classification") or {}
    docs = "\n".join(state.get("search_results") or [])

    prompt = DRAFT_RESPONSE_TEMPLATE.format(
        email_content=state["email_content"],
        docs=docs,
    )
    response = llm.invoke(prompt)

    return Command(update={"draft_response": response.content}, goto="human_review")


def human_review_node(state: EmailAgentState) -> Command[Literal["send_reply", "__end__"]]:
    """
    Human-in-the-loop approval node.
    Rule: interrupt() must come first because code before it re-executes upon resume.
    """
    classification = state.get("classification") or {}

    human_decision = interrupt({
        "email_id": state.get("email_id"),
        "original_email": state.get("email_content"),
        "draft_response": state.get("draft_response"),
        "urgency": classification.get("urgency"),
        "action": "Please review and approve or edit this response.",
    })

    if human_decision.get("approved"):
        final_reply = human_decision.get("edited_response") or state.get("draft_response")
        return Command(update={"draft_response": final_reply}, goto="send_reply")

    return Command(update={}, goto="__end__")


def send_reply_node(state: EmailAgentState) -> dict:
    """Dispatch email."""
    print(f"\n[Email Dispatched to {state.get('sender_email')}]:\n{state.get('draft_response')}\n")
    return {}
