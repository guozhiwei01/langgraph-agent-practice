"""Application boundary shared by Gmail ingestion and the review web UI."""

from __future__ import annotations

from typing import Any

from email_agent.graph import app
from email_agent.schemas import EmailRequest, EmailResult
from email_agent.storage import (
    create_task_for_email,
    get_task,
    get_tool_success,
    mark_task_sent,
    mark_task_failed,
    record_review,
    record_tool_success,
    update_task_draft,
)
from email_agent.tools.github_issues import create_issue
from email_agent.tools.gmail import (
    fetch_message,
    list_unread_labeled_message_ids,
    mark_message_read,
    send_reply,
)


class EmailAgentService:
    def run(self, request: EmailRequest) -> EmailResult:
        if request.task_id is None:
            raise ValueError("task_id is required for persisted workflow execution.")
        config = {"configurable": {"thread_id": f"task:{request.task_id}"}}
        state = {
            "email_content": request.email_content,
            "sender_email": request.sender_email,
            "email_id": request.email_id,
            "email_subject": request.subject,
            "provider_thread_id": request.provider_thread_id,
            "task_id": request.task_id,
            "classification": None,
            "search_results": None,
            "draft_response": None,
            "messages": None,
        }
        try:
            for _ in app.stream(state, config):
                pass
            values = app.get_state(config).values
            classification = values.get("classification") or {}
            draft = values.get("draft_response")
            update_task_draft(
                request.task_id,
                status="waiting_for_review",
                classification=classification,
                draft=draft,
            )
            return EmailResult(
                task_id=request.task_id,
                status="waiting_for_review",
                draft_response=draft,
            )
        except Exception as error:
            mark_task_failed(request.task_id, error)
            raise

    def sync_gmail(self, limit: int = 20) -> list[EmailResult]:
        results: list[EmailResult] = []
        for message_id in list_unread_labeled_message_ids(limit=limit):
            email = fetch_message(message_id)
            task_id, created = create_task_for_email(email)
            if not created:
                continue
            result = self.run(
                EmailRequest(
                    task_id=task_id,
                    email_id=email["message_id"],
                    sender_email=email["sender"],
                    subject=email["subject"],
                    email_content=email["body"],
                    provider_thread_id=email["thread_id"],
                )
            )
            mark_message_read(message_id)
            results.append(result)
        return results

    def approve_and_send(
        self, task_id: int, *, response_text: str, reviewer_id: str = "local-reviewer"
    ) -> EmailResult:
        task = self._required_task(task_id)
        if task["status"] == "sent":
            return EmailResult(task_id=task_id, status="sent", draft_response=task["draft_response"])
        if task["status"] != "waiting_for_review":
            raise ValueError(f"Task {task_id} is not waiting for review.")
        final_response = response_text.strip()
        if not final_response:
            raise ValueError("Approved response cannot be empty.")

        classification = task.get("classification") or {}
        if classification.get("intent") == "bug":
            issue = self._ensure_github_issue(task_id, classification)
            final_response += f"\n\nEngineering reference: {issue['url']}"

        metadata = task.get("raw_metadata") or {}
        message_id = send_reply(
            recipient=task["sender_email"],
            subject=task["subject"],
            body=final_response,
            thread_id=task["provider_thread_id"],
            in_reply_to=metadata.get("rfc_message_id"),
            references=metadata.get("references"),
        )
        decision = "edited" if final_response != (task.get("draft_response") or "") else "approved"
        record_review(
            task_id,
            reviewer_id=reviewer_id,
            decision=decision,
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
        return EmailResult(task_id=task_id, status="sent", draft_response=final_response)

    def reject(
        self, task_id: int, *, reason: str, reviewer_id: str = "local-reviewer"
    ) -> EmailResult:
        task = self._required_task(task_id)
        if task["status"] != "waiting_for_review":
            raise ValueError(f"Task {task_id} is not waiting for review.")
        record_review(
            task_id,
            reviewer_id=reviewer_id,
            decision="rejected",
            original_draft=task.get("draft_response"),
            final_response=None,
            reason=reason.strip() or "Rejected during local review.",
        )
        return EmailResult(task_id=task_id, status="rejected", draft_response=task.get("draft_response"))

    @staticmethod
    def _required_task(task_id: int) -> dict[str, Any]:
        task = get_task(task_id)
        if task is None:
            raise LookupError(f"Task {task_id} does not exist.")
        return task

    @staticmethod
    def _ensure_github_issue(task_id: int, classification: dict[str, Any]) -> dict[str, Any]:
        key = f"task:{task_id}:github-issue"
        existing = get_tool_success(key)
        if existing:
            return existing
        number, url = create_issue(
            title=f"Customer bug: {classification.get('topic', 'Uncategorized issue')}",
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
