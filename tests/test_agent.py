import asyncio
import unittest
from unittest.mock import patch

from app.agent import stream_agent_response


async def fake_async_stream(*chunks):
    for chunk in chunks:
        yield chunk


async def fail_before_output(_prompt):
    raise RuntimeError("Claude unavailable")
    yield ""


async def fail_after_output(_prompt):
    yield "partial"
    raise RuntimeError("Claude stream disconnected")


def collect_events(prompt):
    async def collect():
        return [event async for event in stream_agent_response(prompt)]

    return asyncio.run(collect())


class AgentStreamTests(unittest.TestCase):
    def test_streams_claude_tokens_and_completion(self):
        with patch(
            "app.agent._claude_text_chunks",
            return_value=fake_async_stream("Hello", " from Claude"),
        ):
            events = collect_events("hello there")

        self.assertEqual(events[0], {"type": "status", "text": "Connecting to Claude"})
        self.assertEqual(
            "".join(event["text"] for event in events if event["type"] == "token"),
            "Hello from Claude",
        )
        self.assertEqual(events[-1], {"type": "done", "text": ""})

    def test_falls_back_to_gemini_if_claude_fails_before_output(self):
        with (
            patch("app.agent._claude_text_chunks", side_effect=fail_before_output),
            patch("app.agent._gemini_text_chunks", return_value=iter(["Gemini reply"])),
        ):
            events = collect_events("hello")

        self.assertEqual(
            events[1],
            {"type": "status", "text": "Claude unavailable; trying Gemini fallback"},
        )
        self.assertEqual(
            "".join(event["text"] for event in events if event["type"] == "token"),
            "Gemini reply",
        )
        self.assertEqual(events[-1], {"type": "done", "text": ""})

    def test_skips_claude_if_anthropic_api_key_could_override_pro_auth(self):
        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-api-key"}),
            patch("app.agent._claude_text_chunks") as claude_stream,
            patch("app.agent._gemini_text_chunks", return_value=iter(["Gemini reply"])),
        ):
            events = collect_events("hello")

        claude_stream.assert_not_called()
        self.assertEqual(
            "".join(event["text"] for event in events if event["type"] == "token"),
            "Gemini reply",
        )

    def test_does_not_restart_with_gemini_after_claude_started_output(self):
        with (
            patch("app.agent._claude_text_chunks", side_effect=fail_after_output),
            patch("app.agent._gemini_text_chunks") as gemini_stream,
        ):
            events = collect_events("hello")

        gemini_stream.assert_not_called()
        self.assertEqual(events[1], {"type": "token", "text": "partial"})
        self.assertEqual(events[-1]["type"], "error")

    def test_rejects_blank_prompt(self):
        with self.assertRaisesRegex(ValueError, "prompt must not be empty"):
            collect_events("   ")


if __name__ == "__main__":
    unittest.main()