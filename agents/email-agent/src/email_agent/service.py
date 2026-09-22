"""Application boundary shared by Gmail ingestion and the review web UI."""

from __future__ import annotations

from typing import Any

from langgraph.types import Command

from email_agent.schemas import EmailRequest, EmailResult
from email_agent.storage import (
    get_task,
    update_task_draft,
)


class EmailAgentService:
    def __init__(self, graph: Any) -> None:
        self.graph = graph

    def run(self, request: EmailRequest) -> EmailResult:
        if request.task_id is None:
            raise ValueError("task_id is required for persisted workflow execution.")
        task = self._required_task(request.task_id)
        config = self._graph_config(task)
        state = {
            "email_content": request.email_content,
            "sender_email": request.sender_email,
            "email_id": request.email_id,
            "email_subject": request.subject,
            "provider_thread_id": request.provider_thread_id,
            "task_id": request.task_id,
            "classification": None,
            "search_results": None,
            "evidence_evaluation": None,
            "draft_response": None,
            "response_validation": None,
            "review_decision": None,
            "reviewer_id": None,
            "review_reason": None,
            "provider_message_id": None,
            "github_issue_url": None,
            "messages": None,
        }
        existing = self.graph.get_state(config)
        if "human_review" in existing.next and existing.interrupts:
            pass
        elif existing.next:
            for _ in self.graph.stream(None, config):
                pass
        else:
            for _ in self.graph.stream(state, config):
                pass
        snapshot = self.graph.get_state(config)
        if "human_review" not in snapshot.next or not snapshot.interrupts:
            raise RuntimeError("Email graph did not pause at human review as expected.")
        values = snapshot.values
        classification = values.get("classification") or {}
        evidence_evaluation = values.get("evidence_evaluation") or {}
        response_validation = values.get("response_validation") or {}
        draft = values.get("draft_response")
        update_task_draft(
            request.task_id,
            status="waiting_for_review",
            classification=classification,
            evidence_evaluation=evidence_evaluation,
            response_validation=response_validation,
            draft=draft,
        )
        return EmailResult(
            task_id=request.task_id,
            status="waiting_for_review",
            draft_response=draft,
        )

    def approve_and_send(
        self, task_id: int, *, response_text: str, reviewer_id: str = "local-reviewer"
    ) -> EmailResult:
        task = self._required_task(task_id)
        if task["status"] == "sent":
            return EmailResult(task_id=task_id, status="sent", draft_response=task["draft_response"])
        if task["status"] not in {"waiting_for_review", "sending"}:
            raise ValueError(f"Task {task_id} is not waiting for review.")
        final_response = response_text.strip()
        if not final_response:
            raise ValueError("Approved response cannot be empty.")

        config = self._graph_config(task)
        self._resume_review(
            config,
            expected_node="send_reply",
            decision={
                "approved": True,
                "edited_response": final_response,
                "reviewer_id": reviewer_id,
            },
        )

        completed = self._required_task(task_id)
        if completed["status"] != "sent":
            raise RuntimeError(f"Task {task_id} resumed but did not reach sent status.")
        return EmailResult(
            task_id=task_id,
            status="sent",
            draft_response=completed.get("draft_response"),
        )

    def reject(
        self, task_id: int, *, reason: str, reviewer_id: str = "local-reviewer"
    ) -> EmailResult:
        task = self._required_task(task_id)
        if task["status"] not in {"waiting_for_review", "processing"}:
            raise ValueError(f"Task {task_id} is not waiting for review.")
        config = self._graph_config(task)
        self._resume_review(
            config,
            expected_node="record_rejection",
            decision={
                "approved": False,
                "reviewer_id": reviewer_id,
                "reason": reason.strip() or "Rejected during local review.",
            },
        )
        completed = self._required_task(task_id)
        if completed["status"] != "rejected":
            raise RuntimeError(f"Task {task_id} resumed but did not reach rejected status.")
        return EmailResult(
            task_id=task_id,
            status="rejected",
            draft_response=completed.get("draft_response"),
        )

    @staticmethod
    def _required_task(task_id: int) -> dict[str, Any]:
        task = get_task(task_id)
        if task is None:
            raise LookupError(f"Task {task_id} does not exist.")
        return task

    @staticmethod
    def _graph_config(task: dict[str, Any]) -> dict[str, dict[str, str]]:
        thread_id = task.get("langgraph_thread_id")
        if not thread_id:
            raise RuntimeError(f"Task {task['id']} has no LangGraph thread ID.")
        return {"configurable": {"thread_id": str(thread_id)}}

    def _resume_review(
        self,
        config: dict[str, Any],
        *,
        expected_node: str,
        decision: dict[str, Any],
    ) -> None:
        snapshot = self.graph.get_state(config)
        if "human_review" in snapshot.next and snapshot.interrupts:
            value: Command | None = Command(resume=decision)
        elif expected_node in snapshot.next:
            value = None
        else:
            raise RuntimeError(
                "The persisted LangGraph thread is neither paused at human_review "
                f"nor retryable at {expected_node}. Tasks created before PostgresSaver "
                "was enabled must be synced again."
            )
        for _ in self.graph.stream(value, config):
            pass
