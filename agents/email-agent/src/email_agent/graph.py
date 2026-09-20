"""StateGraph assembly and checkpoint compilation."""

from langgraph.graph import StateGraph, START, END
from langgraph.types import RetryPolicy
from langgraph.checkpoint.memory import MemorySaver

from email_agent.state import EmailAgentState
from email_agent.nodes import (
    read_email_node,
    classify_intent_node,
    search_documentation_node,
    search_error_handler,
    bug_tracking_node,
    draft_response_node,
    human_review_node,
    send_reply_node,
)


def compile_agent_app():
    """Build and compile the workflow with fault tolerance."""
    workflow = StateGraph(EmailAgentState)

    # Register nodes with retry policies & Saga error handlers
    workflow.add_node("read_email", read_email_node)
    workflow.add_node("classify_intent", classify_intent_node)
    workflow.add_node(
        "search_documentation",
        search_documentation_node,
        retry_policy=RetryPolicy(max_attempts=3),
        error_handler=search_error_handler,
    )
    workflow.add_node("bug_tracking", bug_tracking_node)
    workflow.add_node("draft_response", draft_response_node)
    workflow.add_node("human_review", human_review_node)
    workflow.add_node("send_reply", send_reply_node)

    # Essential boundary edges
    workflow.add_edge(START, "read_email")
    workflow.add_edge("read_email", "classify_intent")
    workflow.add_edge("send_reply", END)

    # Compile with checkpointer for Human-in-the-loop state durability
    checkpointer = MemorySaver()
    return workflow.compile(checkpointer=checkpointer)


app = compile_agent_app()