import asyncio
import unittest
from unittest.mock import patch

from app.evals import evaluate_cases


async def fake_events(*events):
    for event in events:
        yield event


class EvalHarnessTests(unittest.TestCase):
    def test_passes_when_response_contains_expected_text(self):
        cases = [
            {
                "id": "phrase",
                "prompt": "Say hello",
                "expected_contains": ["hello"],
            }
        ]
        with patch(
            "app.evals.stream_agent_response",
            return_value=fake_events(
                {"type": "token", "text": "Hello there"},
                {"type": "done", "text": ""},
            ),
        ):
            results = asyncio.run(evaluate_cases(cases))

        self.assertTrue(results[0]["passed"])
        self.assertEqual(results[0]["missing_expectations"], [])

    def test_fails_when_expected_text_is_missing(self):
        cases = [
            {
                "id": "phrase",
                "prompt": "Say hello",
                "expected_contains": ["goodbye"],
            }
        ]
        with patch(
            "app.evals.stream_agent_response",
            return_value=fake_events({"type": "token", "text": "Hello there"}),
        ):
            results = asyncio.run(evaluate_cases(cases))

        self.assertFalse(results[0]["passed"])
        self.assertEqual(results[0]["missing_expectations"], ["goodbye"])


if __name__ == "__main__":
    unittest.main()