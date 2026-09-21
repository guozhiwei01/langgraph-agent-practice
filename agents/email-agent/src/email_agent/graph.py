"""StateGraph assembly and checkpoint compilation."""

from langgraph.graph import StateGraph, START, END
from langgraph.types import RetryPolicy
from langgraph.checkpoint.memory import MemorySaver

from email_agent.state import EmailAgentState
from email_agent.nodes import (
    read_email_node,
    triage_email_node,
    triage_error_handler,
    search_documentation_node,
    search_error_handler,
    evaluate_evidence_node,
    evidence_error_handler,
    draft_response_node,
    draft_clarification_node,
    validate_response_node,
    validation_error_handler,
    human_review_node,
    send_reply_node,
)


def compile_agent_app():
    """Build and compile the workflow with fault tolerance."""
    workflow = StateGraph(EmailAgentState)

    # Register nodes with retry policies & Saga error handlers
    workflow.add_node("read_email", read_email_node)
    workflow.add_node(
        "triage_email",
        triage_email_node,
        error_handler=triage_error_handler,
    )
    workflow.add_node(
        "search_documentation",
        search_documentation_node,
        retry_policy=RetryPolicy(max_attempts=3),
        error_handler=search_error_handler,
    )
    workflow.add_node(
        "evaluate_evidence",
        evaluate_evidence_node,
        error_handler=evidence_error_handler,
    )
    workflow.add_node("draft_response", draft_response_node)
    workflow.add_node("draft_clarification", draft_clarification_node)
    workflow.add_node(
        "validate_response",
        validate_response_node,
        error_handler=validation_error_handler,
    )
    workflow.add_node("human_review", human_review_node)
    workflow.add_node("send_reply", send_reply_node)

    # Essential boundary edges
    workflow.add_edge(START, "read_email")
    workflow.add_edge("read_email", "triage_email")
    workflow.add_edge("send_reply", END)

    # Compile with checkpointer for Human-in-the-loop state durability
    checkpointer = MemorySaver()
    return workflow.compile(checkpointer=checkpointer)


app = compile_agent_app()
