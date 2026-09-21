"""Offline checks for the knowledge retrieval adapter."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from email_agent.tools import knowledge_base


class KnowledgeBaseTests(unittest.TestCase):
    def test_empty_query_does_not_call_embedding_api(self) -> None:
        with patch.object(knowledge_base, "embed_texts") as embed:
            self.assertEqual(knowledge_base.search_knowledge(" \n "), [])
            embed.assert_not_called()

    def test_embedding_response_is_ordered_by_index(self) -> None:
        first = SimpleNamespace(index=1, embedding=[2.0] * 1024)
        second = SimpleNamespace(index=0, embedding=[1.0] * 1024)
        response = SimpleNamespace(data=[first, second])
        with patch.object(
            knowledge_base, "embedding_config", return_value=("test-key", "https://example.test", "test-model")
        ), patch.object(knowledge_base, "OpenAI") as client:
            client.return_value.embeddings.create.return_value = response
            vectors = knowledge_base.embed_texts(["one", "two"])
        self.assertEqual(vectors[0][0], 1.0)
        self.assertEqual(vectors[1][0], 2.0)

    def test_output_marks_demo_and_includes_source(self) -> None:
        hit = knowledge_base.KnowledgeHit(
            document_slug="password-reset",
            title="Reset password",
            heading="Steps",
            content="Use the account page.",
            source_uri="kb://demo/password",
            similarity=0.6123,
            is_demo=True,
        )
        with patch.object(knowledge_base, "search_knowledge", return_value=[hit]) as search:
            result = knowledge_base.query_knowledge_base("reset password", category="account")
        search.assert_called_once_with("reset password", category="account", limit=2)
        self.assertEqual(len(result), 1)
        self.assertIn("DEMO / fictional policy", result[0])
        self.assertIn("kb://demo/password", result[0])
        self.assertIn("Use the account page.", result[0])

    def test_invalid_limit_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            knowledge_base.search_knowledge("query", limit=0)


if __name__ == "__main__":
    unittest.main()
