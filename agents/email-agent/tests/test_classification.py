"""Offline tests for the TypeSafe HTTP decision classifier."""

import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from email_agent.classification import classify_email
from email_agent.config import Settings
from email_agent.decision_engine import TypeSafeDecisionEngine


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()


def _settings(*, threshold: float = 0.6) -> Settings:
    return Settings(
        deepseek_api_key="deepseek-test",
        typesafe_api_key="typesafe-test",
        typesafe_model="jev-1.13.0",
        typesafe_min_confidence=threshold,
    )


def _response(
    *,
    complexity_score: float = 1.0,
    intent_confidence: float = 0.9,
    refund_requested: float = 0.0,
):
    return {
        "model": "jev-1.13.0",
        "answers": {
            "intent": {
                "type": "choice",
                "choice": "billing",
                "confidence": intent_confidence,
                "probabilities": {
                    "question": 0.02,
                    "bug": 0.03,
                    "billing": 0.9,
                    "feature": 0.05,
                    "other": 0.0,
                },
            },
            "urgency": {
                "type": "score",
                "score": 2.2,
                "confidence": 0.8,
                "probabilities": {"0": 0.0, "1": 0.0, "2": 0.8, "3": 0.2},
            },
            "complexity": {
                "type": "score",
                "score": complexity_score,
                "confidence": 0.75,
                "probabilities": {"0": 0.1, "1": 0.8, "2": 0.1},
            },
            "refund_requested": {"type": "noul", "noul": refund_requested},
            "security_incident": {"type": "noul", "noul": 0.0},
            "human_requested": {"type": "noul", "noul": 0.0},
        },
        "usage": {"input_tokens": 100, "output_tokens": 20},
    }


class _Engine:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls = []

    def evaluate(self, *, state, questions):
        self.calls.append({"state": state, "questions": questions})
        return self.response


class TypeSafeClassificationTests(unittest.TestCase):
    def test_maps_decision_response_to_classification(self) -> None:
        engine = _Engine(_response())

        result = classify_email(
            email_content="I was charged twice and need help.",
            email_subject="Duplicate charge",
            settings=_settings(),
            engine=engine,
        )

        self.assertEqual(result["intent"], "billing")
        self.assertEqual(result["urgency"], "high")
        self.assertEqual(result["complexity"], "investigation")
        self.assertFalse(result["needs_human"])
        call = engine.calls[0]
        self.assertEqual(call["state"]["subject"], "Duplicate charge")
        self.assertEqual(call["questions"]["intent"]["type"], "choice")
        self.assertEqual(call["questions"]["urgency"]["type"], "score")
        self.assertEqual(call["questions"]["complexity"]["type"], "score")
        self.assertEqual(call["questions"]["refund_requested"]["type"], "noul")
        self.assertIn("other", call["questions"]["intent"]["criteria"])
        self.assertIn("`content`", call["questions"]["intent"]["instructions"])

    def test_low_confidence_requires_human_review(self) -> None:
        engine = _Engine(_response(intent_confidence=0.4))

        result = classify_email(
            email_content="This might be about a charge or a product error.",
            email_subject="Not sure",
            settings=_settings(),
            engine=engine,
        )

        self.assertTrue(result["needs_human"])

    def test_specialist_complexity_requires_human_review(self) -> None:
        engine = _Engine(_response(complexity_score=1.7))

        result = classify_email(
            email_content="Please authorize a non-standard refund.",
            email_subject="Refund approval",
            settings=_settings(),
            engine=engine,
        )

        self.assertEqual(result["complexity"], "specialist")
        self.assertTrue(result["needs_human"])

    def test_refund_request_requires_human_review(self) -> None:
        engine = _Engine(_response(refund_requested=0.92))
        result = classify_email(
            email_content="Please refund the duplicate charge.",
            email_subject="Refund",
            settings=_settings(),
            engine=engine,
        )
        self.assertTrue(result["needs_human"])

    def test_other_intent_requires_human_review(self) -> None:
        response = _response()
        response["answers"]["intent"].update({
            "choice": "other",
            "confidence": 0.99,
            "probabilities": {
                "question": 0.0,
                "bug": 0.0,
                "billing": 0.0,
                "feature": 0.0,
                "other": 1.0,
            },
        })
        result = classify_email(
            email_content="Please transfer this message to your legal department.",
            email_subject="Legal request",
            settings=_settings(),
            engine=_Engine(response),
        )
        self.assertEqual(result["intent"], "other")
        self.assertTrue(result["needs_human"])

    @patch("email_agent.decision_engine.time.sleep")
    @patch("email_agent.decision_engine.urlopen")
    def test_rate_limit_is_retried(self, urlopen, sleep) -> None:
        rate_limit = HTTPError(
            "https://api.typesafe.ai/v1/systemone",
            429,
            "Too Many Requests",
            {},
            io.BytesIO(b'{"detail":"slow down"}'),
        )
        urlopen.side_effect = [
            rate_limit,
            _Response(json.dumps(_response()).encode()),
        ]
        engine = TypeSafeDecisionEngine(
            api_key="typesafe-test",
            model="jev-1.13.0",
            timeout=5,
        )
        result = engine.evaluate(state="test", questions={"intent": {"type": "noul"}})

        self.assertEqual(result["model"], "jev-1.13.0")
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(0.5)

    @patch("email_agent.decision_engine.urlopen")
    def test_http_adapter_posts_expected_payload(self, urlopen) -> None:
        urlopen.return_value = _Response(json.dumps(_response()).encode())
        engine = TypeSafeDecisionEngine(
            api_key="typesafe-test",
            model="jev-1.13.0",
            timeout=5,
        )

        engine.evaluate(state={"message": "hello"}, questions={"x": {"type": "noul"}})

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer typesafe-test")
        self.assertEqual(payload["model"], "jev-1.13.0")
        self.assertEqual(payload["state"], {"message": "hello"})

    def test_missing_api_key_is_rejected_before_network_call(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "TYPESAFE_API_KEY"):
            classify_email(
                email_content="Hello",
                email_subject="Question",
                settings=Settings(deepseek_api_key="deepseek-test"),
            )


if __name__ == "__main__":
    unittest.main()
