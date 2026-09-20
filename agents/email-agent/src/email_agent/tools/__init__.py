"""External knowledge and issue-tracker integrations for workflow nodes."""

from .knowledge_base import query_knowledge_base


def file_issue_ticket(title: str, description: str) -> str:
    """Simulate creating a ticket in Jira or GitHub Issues."""
    return "BUG-2026-ENG"
