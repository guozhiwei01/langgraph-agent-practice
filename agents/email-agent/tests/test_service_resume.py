"""Checks that web review actions resume the persisted LangGraph thread."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from langgraph.types import Command

from email_agent.service import EmailAgentService


class _Graph:
    def __init__(self) -> None:
        self.calls = []

    def get_state(self, config):
        return SimpleNamespace(
            next=("human_review",),
            interrupts=(SimpleNamespace(value={"action": "review"}),),
            values={"draft_response": "Original draft"},
        )

    def stream(self, value, config):
        self.calls.append((value, config))
        yield {"human_review": {}}


def _task(status: str, draft: str = "Original draft") -> dict:
    return {
        "id": 17,
        "status": status,
        "langgraph_thread_id": "gmail:provider-message-17",
        "draft_response": draft,
    }


class ServiceResumeTests(unittest.TestCase):
    @patch("email_agent.service.get_task")
    def test_approve_resumes_same_persisted_thread(self, get_task) -> None:
        get_task.side_effect = [_task("waiting_for_review"), _task("sent", "Reviewed")]
        graph = _Graph()
        service = EmailAgentService(graph)

        result = service.approve_and_send(17, response_text="Reviewed")

        command, config = graph.calls[0]
        self.assertIsInstance(command, Command)
        self.assertEqual(command.resume["approved"], True)
        self.assertEqual(command.resume["edited_response"], "Reviewed")
        self.assertEqual(
            config,
            {"configurable": {"thread_id": "gmail:provider-message-17"}},
        )
        self.assertEqual(result.status, "sent")

    @patch("email_agent.service.get_task")
    def test_reject_resumes_same_thread_with_reason(self, get_task) -> None:
        get_task.side_effect = [_task("waiting_for_review"), _task("rejected")]
        graph = _Graph()
        service = EmailAgentService(graph)

        result = service.reject(17, reason="Needs specialist review")

        command, config = graph.calls[0]
        self.assertEqual(command.resume["approved"], False)
        self.assertEqual(command.resume["reason"], "Needs specialist review")
        self.assertEqual(config["configurable"]["thread_id"], "gmail:provider-message-17")
        self.assertEqual(result.status, "rejected")

    @patch("email_agent.service.get_task", return_value=_task("waiting_for_review"))
    def test_missing_checkpoint_is_not_silently_bypassed(self, get_task) -> None:
        graph = _Graph()
        graph.get_state = lambda config: SimpleNamespace(next=(), interrupts=(), values={})
        service = EmailAgentService(graph)

        with self.assertRaisesRegex(RuntimeError, "Tasks created before PostgresSaver"):
            service.approve_and_send(17, response_text="Reviewed")
        self.assertEqual(graph.calls, [])

    @patch("email_agent.service.get_task")
    def test_approval_retry_continues_failed_send_node_without_second_resume(
        self, get_task
    ) -> None:
        get_task.side_effect = [_task("sending"), _task("sent", "Reviewed")]
        graph = _Graph()
        graph.get_state = lambda config: SimpleNamespace(
            next=("send_reply",), interrupts=(), values={"draft_response": "Reviewed"}
        )
        service = EmailAgentService(graph)

        result = service.approve_and_send(17, response_text="Reviewed")

        value, _ = graph.calls[0]
        self.assertIsNone(value)
        self.assertEqual(result.status, "sent")

    @patch("email_agent.service.get_task")
    def test_rejection_retry_continues_failed_record_node(self, get_task) -> None:
        get_task.side_effect = [_task("processing"), _task("rejected")]
        graph = _Graph()
        graph.get_state = lambda config: SimpleNamespace(
            next=("record_rejection",), interrupts=(), values={}
        )
        service = EmailAgentService(graph)

        result = service.reject(17, reason="Retry")

        value, _ = graph.calls[0]
        self.assertIsNone(value)
        self.assertEqual(result.status, "rejected")


if __name__ == "__main__":
    unittest.main()
