"""
Global State container for the Email Agent workflow.
Core Principle: Store raw data, never pre-formatted prompts (LangGraph Best Practice).
"""

from typing import Optional, TypedDict
from langchain_core.messages import HumanMessage
from email_agent.schemas import EmailClassification


class EmailAgentState(TypedDict):
    """Execution state shared across all nodes in the graph."""
    email_content: str
    sender_email: str
    email_id: str
    email_subject: str
    provider_thread_id: Optional[str]
    task_id: Optional[int]
    classification: Optional[EmailClassification]
    search_results: Optional[list[str]]
    draft_response: Optional[str]
    messages: Optional[list[HumanMessage]]
