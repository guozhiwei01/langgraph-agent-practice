"""End-to-end checks for pausing and resuming the compiled email graph."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from email_agent.graph import compile_agent_app


def _classification() -> dict:
    return {
        "intent": "question",
        "urgency": "medium",
        "complexity": "specialist",
        "topic": "Manual review test",
        "summary": "A test message that requires a reviewer.",
        "intent_confidence": 1.0,
        "urgency_confidence": 1.0,
        "complexity_confidence": 1.0,
        "refund_requested": 0.0,
        "security_incident": 0.0,
        "human_requested": 1.0,
        "needs_human": True,
        "decision_model": "test",
    }


def _initial_state() -> dict:
    return {
        "email_content": "Please ask a person to help me.",
        "sender_email": "customer@example.com",
        "email_id": "message-17",
        "email_subject": "Manual review",
        "provider_thread_id": "gmail-thread-17",
        "task_id": 17,
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


class GraphResumeTests(unittest.TestCase):
    def _paused_graph(self, thread_id: str):
        classify = patch(
            "email_agent.nodes.classify_email", return_value=_classification()
        )
        classify.start()
        self.addCleanup(classify.stop)

        graph = compile_agent_app(MemorySaver())
        config = {"configurable": {"thread_id": thread_id}}
        list(graph.stream(_initial_state(), config))

        snapshot = graph.get_state(config)
        self.assertEqual(snapshot.next, ("human_review",))
        self.assertTrue(snapshot.interrupts)
        return graph, config

    def test_approval_resumes_interrupt_and_runs_send_node(self) -> None:
        graph, config = self._paused_graph("approve-17")
        task = {
            "id": 17,
            "sender_email": "customer@example.com",
            "subject": "Manual review",
            "provider_thread_id": "gmail-thread-17",
            "raw_metadata": {},
            "draft_response": None,
        }

        with (
            patch("email_agent.nodes.get_task", return_value=task),
            patch("email_agent.nodes.get_sent_outbound", return_value=None),
            patch(
                "email_agent.nodes.prepare_outbound",
                return_value={
                    "status": "pending",
                    "body": "A reviewer-authored response.",
                    "recipient_email": "customer@example.com",
                    "subject": "Manual review",
                    "rfc_message_id": "<email-agent-task-17@email-agent.local>",
                    "provider_message_id": None,
                },
            ),
            patch("email_agent.nodes.find_sent_message_by_rfc_message_id", return_value=None),
            patch("email_agent.nodes.mark_outbound_sending"),
            patch(
                "email_agent.nodes.send_gmail_reply", return_value="provider-message-1"
            ) as send_reply,
            patch("email_agent.nodes.record_review") as record_review,
            patch("email_agent.nodes.mark_task_sent") as mark_task_sent,
        ):
            list(
                graph.stream(
                    Command(
                        resume={
                            "approved": True,
                            "edited_response": "A reviewer-authored response.",
                            "reviewer_id": "reviewer-1",
                        }
                    ),
                    config,
                )
            )

        self.assertEqual(graph.get_state(config).next, ())
        send_reply.assert_called_once()
        record_review.assert_called_once_with(
            17,
            reviewer_id="reviewer-1",
            decision="edited",
            original_draft=None,
            final_response="A reviewer-authored response.",
        )
        mark_task_sent.assert_called_once()

    def test_rejection_resumes_interrupt_and_records_reason(self) -> None:
        graph, config = self._paused_graph("reject-17")

        with patch("email_agent.nodes.record_review") as record_review:
            list(
                graph.stream(
                    Command(
                        resume={
                            "approved": False,
                            "reviewer_id": "reviewer-2",
                            "reason": "Needs a billing specialist.",
                        }
                    ),
                    config,
                )
            )

        self.assertEqual(graph.get_state(config).next, ())
        record_review.assert_called_once_with(
            17,
            reviewer_id="reviewer-2",
            decision="rejected",
            original_draft=None,
            final_response=None,
            reason="Needs a billing specialist.",
        )


if __name__ == "__main__":
    unittest.main()
