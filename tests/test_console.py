import json
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import unittest
from unittest.mock import call, patch

from app.console import (
    MAX_HISTORY_TURNS,
    MAX_STORED_SESSIONS,
    StreamResult,
    run_chat,
    save_session,
    stream_prompt,
)


class ConsoleChatTests(unittest.TestCase):
    @patch("app.console.urlopen")
    def test_stream_prompt_returns_usage_metadata(self, mocked_urlopen):
        response = mocked_urlopen.return_value.__enter__.return_value
        response.__iter__.return_value = iter(
            [
                b'data: {"type":"token","text":"Hi"}\n',
                b'data: {"type":"done","text":""}\n',
                b'data: {"type":"usage","provider":"gemini",'
                b'"input_tokens":2,"output_tokens":1}\n',
            ]
        )
        output = StringIO()

        with redirect_stdout(output):
            result = stream_prompt("http://localhost:8000/stream", "Hello")

        self.assertEqual(result.text, "Hi")
        self.assertEqual(result.usage["provider"], "gemini")
        self.assertEqual(result.usage["input_tokens"], 2)

    @patch(
        "app.console.stream_prompt",
        side_effect=[
            StreamResult(
                "Hello!",
                {
                    "provider": "claude",
                    "input_tokens": 4,
                    "output_tokens": 2,
                    "cost_basis": "claude_subscription",
                },
            ),
            StreamResult(
                "You said hello.",
                {
                    "provider": "gemini",
                    "input_tokens": 8,
                    "output_tokens": 3,
                    "estimated_cost_usd": 0.0000099,
                    "cost_basis": "gemini_paid_list_estimate",
                },
            ),
        ],
    )
    @patch("builtins.input", side_effect=["Hello", "What did I say?", "/quit"])
    def test_chat_sends_prior_turns_until_quit(self, _input, stream_prompt):
        output = StringIO()
        with redirect_stdout(output):
            run_chat("http://localhost:8000/stream")

        self.assertEqual(
            stream_prompt.call_args_list,
            [
                call("http://localhost:8000/stream", "Hello"),
                call(
                    "http://localhost:8000/stream",
                    "Continue this conversation using the earlier turns as context.\n\n"
                    "User: Hello\nAssistant: Hello!\nUser: What did I say?\nAssistant:",
                ),
            ],
        )
        self.assertIn("User turns: 2", output.getvalue())
        self.assertIn("Completed responses: 2", output.getvalue())
        self.assertIn("12 input, 5 output, 17 total", output.getvalue())
        self.assertIn("$0.000010", output.getvalue())
        self.assertIn("Claude Pro requests: 1", output.getvalue())
        self.assertIn("History was not saved", output.getvalue())


    @patch(
        "app.console.stream_prompt",
        return_value=StreamResult(
            "Hello!",
            {
                "provider": "claude",
                "input_tokens": 3,
                "output_tokens": 2,
                "cost_basis": "claude_subscription",
            },
        ),
    )
    @patch("builtins.input", side_effect=["Hello", "/quit"])
    def test_chat_persists_session_and_prints_resume_details(
        self, _input, _stream_prompt
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            session_store_path = Path(tmpdir) / "sessions.json"
            output = StringIO()
            with redirect_stdout(output):
                run_chat(
                    "http://localhost:8000/stream",
                    session_id="resume-123",
                    session_store_path=session_store_path,
                )

            stored = json.loads(session_store_path.read_text())
            self.assertEqual(stored["session_id"], "resume-123")
            self.assertEqual(stored["history"][-2], ["User", "Hello"])
            self.assertEqual(session_store_path.stat().st_mode & 0o777, 0o600)
            self.assertIn("Session ID: resume-123", output.getvalue())
            self.assertIn(
                "Resume command: uv run agent-console-session resume-123 --session-store",
                output.getvalue(),
            )

    def test_session_store_retains_bounded_history_and_session_count(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            session_store_path = Path(tmpdir) / "sessions.json"
            history = [
                item
                for turn in range(MAX_HISTORY_TURNS + 2)
                for item in (("User", f"question {turn}"), ("Assistant", f"answer {turn}"))
            ]

            for session_number in range(MAX_STORED_SESSIONS + 1):
                save_session(
                    f"session-{session_number}",
                    history,
                    path=session_store_path,
                )

            stored = json.loads(session_store_path.read_text())
            self.assertEqual(len(stored["sessions"]), MAX_STORED_SESSIONS)
            self.assertNotIn("session-0", stored["sessions"])
            self.assertEqual(
                len(stored["sessions"][f"session-{MAX_STORED_SESSIONS}"]["history"]),
                2 * MAX_HISTORY_TURNS,
            )


if __name__ == "__main__":
    unittest.main()