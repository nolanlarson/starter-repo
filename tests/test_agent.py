import asyncio
import json
import unittest
from unittest.mock import patch

from claude_agent_sdk import ResultMessage

from app.agent import (
    MAX_AGENT_TURNS,
    MAX_PROMPT_CHARS,
    _claude_text_chunks,
    _claude_usage,
    _configured_gemini_model,
    _configured_claude_model,
    _estimate_gemini_cost,
    stream_agent_response,
)


async def fake_async_stream(*chunks):
    for chunk in chunks:
        yield chunk


async def fail_before_output(_prompt, _metrics):
    raise RuntimeError("Claude unavailable")
    yield ""


async def fail_after_output(_prompt, _metrics):
    yield "partial"
    raise RuntimeError("Claude stream disconnected")


async def fake_claude_response(_prompt, metrics):
    metrics.update(
        {
            "provider": "claude",
            "model": "test-model",
            "input_tokens": 4,
            "output_tokens": 3,
        }
    )
    async for chunk in fake_async_stream("Hello", " from Claude"):
        yield chunk


def collect_events(prompt):
    async def collect():
        return [event async for event in stream_agent_response(prompt)]

    return asyncio.run(collect())


class AgentStreamTests(unittest.TestCase):
    def test_configured_model_is_passed_to_claude_sdk(self):
        async def collect_chunks():
            return [chunk async for chunk in _claude_text_chunks("hello", {})]

        with (
            patch.dict("os.environ", {"CLAUDE_MODEL": "claude-haiku-5-5"}),
            patch("claude_agent_sdk.ClaudeAgentOptions") as options_factory,
            patch("claude_agent_sdk.query", return_value=fake_async_stream()),
            patch("app.agent.shutil.which", return_value="/usr/local/bin/claude"),
        ):
            asyncio.run(collect_chunks())

        self.assertEqual(
            options_factory.call_args.kwargs["model"], "claude-haiku-5-5"
        )
        self.assertEqual(
            options_factory.call_args.kwargs["tools"],
            ["WebSearch", "WebFetch"],
        )
        self.assertEqual(options_factory.call_args.kwargs["max_turns"], MAX_AGENT_TURNS)
        self.assertEqual(options_factory.call_args.kwargs["permission_mode"], "default")
        self.assertEqual(
            options_factory.call_args.kwargs["cli_path"], "/usr/local/bin/claude"
        )

    def test_claude_model_defaults_to_haiku_and_supports_override(self):
        with patch.dict("os.environ", {"CLAUDE_MODEL": ""}):
            self.assertEqual(_configured_claude_model(), "claude-haiku-5-5")

        with patch.dict("os.environ", {"CLAUDE_MODEL": "claude-opus-5-5"}):
            self.assertEqual(_configured_claude_model(), "claude-opus-5-5")

    def test_enables_builtin_web_search_tool_when_toggle_is_on(self):
        async def collect_chunks():
            return [chunk async for chunk in _claude_text_chunks("hello", {})]

        with (
            patch.dict("os.environ", {"ENABLE_WEB_SEARCH": "true"}),
            patch("claude_agent_sdk.ClaudeAgentOptions") as options_factory,
            patch("claude_agent_sdk.query", return_value=fake_async_stream()),
        ):
            asyncio.run(collect_chunks())

        self.assertEqual(
            options_factory.call_args.kwargs["allowed_tools"],
            ["WebSearch", "WebFetch"],
        )

    def test_configured_gemini_model_defaults_to_supported_model(self):
        with patch.dict("os.environ", {"GEMINI_MODEL": ""}):
            self.assertEqual(_configured_gemini_model(), "gemini-3.5-flash-lite")

    def test_claude_sdk_error_result_is_reported(self):
        error_message = ResultMessage(
            subtype="error_during_execution",
            duration_ms=10,
            duration_api_ms=10,
            is_error=True,
            num_turns=1,
            session_id="test-session",
            errors=["web tool unavailable"],
        )

        async def collect_chunks():
            return [chunk async for chunk in _claude_text_chunks("search", {})]

        with (
            patch("claude_agent_sdk.ClaudeAgentOptions"),
            patch("claude_agent_sdk.query", return_value=fake_async_stream(error_message)),
        ):
            with self.assertRaisesRegex(RuntimeError, "web tool unavailable"):
                asyncio.run(collect_chunks())

    def test_claude_usage_reads_sdk_model_usage_typed_dict(self):
        message = ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=90,
            is_error=False,
            num_turns=1,
            session_id="test-session",
            model_usage={
                "model-alias": {
                    "inputTokens": 4,
                    "outputTokens": 3,
                    "cacheReadInputTokens": 2,
                    "cacheCreationInputTokens": 1,
                    "webSearchRequests": 0,
                    "costUSD": 0.0,
                    "contextWindow": 200000,
                    "maxOutputTokens": 8192,
                    "canonicalModel": "claude-test-model",
                }
            },
        )

        usage = _claude_usage(message)

        self.assertEqual(usage["model"], "claude-test-model")
        self.assertEqual(usage["input_tokens"], 7)
        self.assertEqual(usage["output_tokens"], 3)

    def test_gemini_cost_estimate_uses_paid_list_rates(self):
        self.assertAlmostEqual(
            _estimate_gemini_cost("gemini-2.5-flash", 1_000_000, 1_000_000),
            2.8,
        )
        self.assertIsNone(_estimate_gemini_cost("unknown-model", 1, 1))

    def test_structured_request_log_excludes_prompt_and_response(self):
        with (
            patch(
                "app.agent._claude_text_chunks",
                side_effect=fake_claude_response,
            ),
            self.assertLogs("app.agent", level="INFO") as captured,
        ):
            events = collect_events("private prompt sentinel")

        record = json.loads(captured.records[0].getMessage())
        self.assertEqual(record["event"], "llm_request")
        self.assertEqual(record["provider"], "claude")
        self.assertEqual(record["input_tokens"], 4)
        self.assertEqual(record["output_tokens"], 3)
        self.assertNotIn("private prompt sentinel", json.dumps(record))
        self.assertNotIn("Hello from Claude", json.dumps(record))
        self.assertEqual(events[-1]["type"], "usage")

    def test_streams_claude_tokens_and_completion(self):
        with patch(
            "app.agent._claude_text_chunks",
            side_effect=fake_claude_response,
        ):
            events = collect_events("hello there")

        self.assertEqual(events[0], {"type": "status", "text": "Connecting to Claude"})
        self.assertEqual(
            "".join(event["text"] for event in events if event["type"] == "token"),
            "Hello from Claude",
        )
        self.assertEqual(events[-2], {"type": "done", "text": ""})
        self.assertEqual(events[-1]["type"], "usage")
        self.assertEqual(events[-1]["input_tokens"], 4)
        self.assertEqual(events[-1]["output_tokens"], 3)

    def test_falls_back_to_gemini_if_claude_fails_before_output(self):
        with (
            patch.dict("os.environ", {"ENABLE_GEMINI_FALLBACK": "true"}),
            patch("app.agent._claude_text_chunks", side_effect=fail_before_output),
            patch("app.agent._gemini_text_chunks", return_value=iter(["Gemini reply"])),
        ):
            events = collect_events("hello")

        self.assertEqual(
            events[1],
            {
                "type": "status",
                "text": "Claude unavailable; trying Gemini text-only fallback (no web search)",
            },
        )
        self.assertEqual(
            "".join(event["text"] for event in events if event["type"] == "token"),
            "Gemini reply",
        )
        self.assertEqual(events[-2], {"type": "done", "text": ""})

    def test_gemini_fallback_is_disabled_by_default(self):
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("app.agent._claude_text_chunks", side_effect=fail_before_output),
            patch("app.agent._gemini_text_chunks") as gemini_stream,
        ):
            events = collect_events("hello")

        gemini_stream.assert_not_called()
        self.assertEqual(events[-2]["type"], "error")
        self.assertIn("ENABLE_GEMINI_FALLBACK=true", events[-2]["text"])

    def test_claude_error_details_are_not_written_to_structured_logs(self):
        with (
            patch.dict("os.environ", {"ENABLE_GEMINI_FALLBACK": "false"}),
            patch("app.agent._claude_text_chunks", side_effect=fail_before_output),
            self.assertLogs("app.agent", level="INFO") as captured,
        ):
            events = collect_events("hello")

        record = json.loads(captured.records[0].getMessage())
        self.assertEqual(record["claude_error_type"], "RuntimeError")
        self.assertNotIn("Claude unavailable", json.dumps(record))
        self.assertIn("Claude unavailable", events[-2]["text"])

    def test_skips_claude_if_anthropic_api_key_could_override_pro_auth(self):
        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-api-key"}),
            patch("app.agent._claude_text_chunks") as claude_stream,
            patch("app.agent._gemini_text_chunks") as gemini_stream,
        ):
            events = collect_events("hello")

        claude_stream.assert_not_called()
        gemini_stream.assert_not_called()
        self.assertEqual(events[-2]["type"], "error")

    def test_does_not_restart_with_gemini_after_claude_started_output(self):
        with (
            patch("app.agent._claude_text_chunks", side_effect=fail_after_output),
            patch("app.agent._gemini_text_chunks") as gemini_stream,
        ):
            events = collect_events("hello")

        gemini_stream.assert_not_called()
        self.assertEqual(events[1], {"type": "token", "text": "partial"})
        self.assertEqual(events[-2]["type"], "error")
        self.assertEqual(events[-1]["type"], "usage")

    def test_rejects_blank_prompt(self):
        with self.assertRaisesRegex(ValueError, "prompt must not be empty"):
            collect_events("   ")

    def test_rejects_prompt_over_character_limit(self):
        with self.assertRaisesRegex(ValueError, "character limit"):
            collect_events("x" * (MAX_PROMPT_CHARS + 1))


if __name__ == "__main__":
    unittest.main()