"""Claude-first agent pipeline with a Gemini fallback."""

import logging
import json
import os
import shutil
from collections.abc import AsyncIterator, Iterator
from time import perf_counter
from uuid import uuid4

from dotenv import load_dotenv

load_dotenv()

LOGGER = logging.getLogger(__name__)
AgentEvent = dict[str, object]
DEFAULT_CLAUDE_MODEL = "claude-haiku-5-5"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
MAX_PROMPT_CHARS = 20_000
MAX_AGENT_TURNS = 4
GEMINI_PAID_PRICES_PER_MILLION = {
    "gemini-2.5-flash": (0.30, 2.50),
}


def _configured_claude_model() -> str:
    return os.getenv("CLAUDE_MODEL") or DEFAULT_CLAUDE_MODEL


def _configured_gemini_model() -> str:
    return os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL


def _gemini_fallback_enabled() -> bool:
    value = os.getenv("ENABLE_GEMINI_FALLBACK", "false")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _configured_claude_cli_path() -> str | None:
    override = os.getenv("CLAUDE_CLI_PATH")
    if override:
        return override
    return shutil.which("claude")


def _web_search_enabled() -> bool:
    value = os.getenv("ENABLE_WEB_SEARCH")
    if value is None:
        return True
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _claude_allowed_tools() -> list[str]:
    if not _web_search_enabled():
        return []
    return ["WebSearch", "WebFetch"]


def _claude_usage(message: object) -> dict[str, object]:
    from claude_agent_sdk import ResultMessage

    if not isinstance(message, ResultMessage):
        return {}

    model_usage = message.model_usage or {}
    if model_usage:
        input_tokens = sum(
            usage["inputTokens"]
            + usage["cacheReadInputTokens"]
            + usage["cacheCreationInputTokens"]
            for usage in model_usage.values()
        )
        output_tokens = sum(
            usage["outputTokens"] for usage in model_usage.values()
        )
        models = sorted(
            {
                usage.get("canonicalModel") or model
                for model, usage in model_usage.items()
            }
        )
        return {
            "provider": "claude",
            "model": ",".join(models),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_basis": "claude_subscription",
        }

    usage = message.usage or {}
    return {
        "provider": "claude",
        "model": "Claude Code default",
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cost_basis": "claude_subscription",
    }


def _estimate_gemini_cost(
    model: str, input_tokens: int | None, output_tokens: int | None
) -> float | None:
    prices = GEMINI_PAID_PRICES_PER_MILLION.get(model)
    if prices is None or input_tokens is None or output_tokens is None:
        return None
    return (input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000


async def _claude_text_chunks(
    prompt: str, metrics: dict[str, object]
) -> AsyncIterator[str]:
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, StreamEvent, query

    options = ClaudeAgentOptions(
        model=_configured_claude_model(),
        cli_path=_configured_claude_cli_path(),
        tools=_claude_allowed_tools(),
        allowed_tools=_claude_allowed_tools(),
        max_turns=MAX_AGENT_TURNS,
        include_partial_messages=True,
        permission_mode="default",
    )
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            metrics.update(_claude_usage(message))
            if message.is_error:
                details = (
                    "; ".join(message.errors or [])
                    or message.result
                    or message.subtype
                )
                raise RuntimeError(f"Claude SDK returned {message.subtype}: {details}")
            continue
        if not isinstance(message, StreamEvent):
            continue
        event = message.event
        delta = event.get("delta", {})
        if (
            event.get("type") == "content_block_delta"
            and delta.get("type") == "text_delta"
        ):
            text = delta.get("text", "")
            if text:
                yield text


def _gemini_text_chunks(prompt: str, metrics: dict[str, object]) -> Iterator[str]:
    from google import genai

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    client = genai.Client(api_key=api_key)
    model = _configured_gemini_model()
    metrics.update(
        {
            "provider": "gemini",
            "model": model,
            "cost_basis": "gemini_paid_list_estimate",
        }
    )
    try:
        for response in client.models.generate_content_stream(
            model=model,
            contents=prompt,
        ):
            usage = response.usage_metadata
            if usage is not None:
                input_tokens = usage.prompt_token_count
                output_tokens = usage.candidates_token_count
                if output_tokens is not None:
                    output_tokens += usage.thoughts_token_count or 0
                metrics.update(
                    {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                    }
                )
                estimated_cost = _estimate_gemini_cost(
                    model, input_tokens, output_tokens
                )
                if estimated_cost is not None:
                    metrics["estimated_cost_usd"] = estimated_cost
            if response.text:
                yield response.text
    finally:
        client.close()


def _finish_request(
    metrics: dict[str, object], started_at: float, outcome: str
) -> AgentEvent:
    metrics["duration_ms"] = round((perf_counter() - started_at) * 1000, 2)
    record = {"event": "llm_request", "outcome": outcome, **metrics}
    LOGGER.info(json.dumps(record, sort_keys=True))
    return {"type": "usage", **metrics, "outcome": outcome}


async def stream_agent_response(prompt: str) -> AsyncIterator[AgentEvent]:
    """Stream a Claude response, falling back to Gemini before any text is sent."""
    cleaned_prompt = prompt.strip()
    if not cleaned_prompt:
        raise ValueError("prompt must not be empty")
    if len(cleaned_prompt) > MAX_PROMPT_CHARS:
        raise ValueError(
            f"prompt exceeds the {MAX_PROMPT_CHARS:,}-character limit"
        )

    started_at = perf_counter()
    metrics: dict[str, object] = {
        "request_id": uuid4().hex,
        "provider": "claude",
        "model": _configured_claude_model(),
        "input_tokens": None,
        "output_tokens": None,
        "estimated_cost_usd": None,
        "cost_basis": "claude_subscription",
        "fallback_used": False,
    }
    yield {"type": "status", "text": "Connecting to Claude"}
    claude_error_detail: str | None = None
    if os.getenv("ANTHROPIC_API_KEY"):
        metrics["fallback_reason"] = "anthropic_api_key_present"
        first_chunk = None
    else:
        claude_metrics: dict[str, object] = {}
        claude_stream = _claude_text_chunks(cleaned_prompt, claude_metrics)
        try:
            first_chunk = await anext(claude_stream, None)
        except Exception as error:
            metrics["fallback_used"] = True
            metrics["fallback_reason"] = type(error).__name__
            metrics["claude_error_type"] = type(error).__name__
            claude_error_detail = str(error)[:300]
            first_chunk = None
        if first_chunk is not None:
            metrics.update(claude_metrics)

    if first_chunk is None:
        if not _gemini_fallback_enabled():
            reason = claude_error_detail or metrics.get("fallback_reason")
            detail = f"Claude unavailable ({reason})" if reason else "Claude returned no response"
            yield {
                "type": "error",
                "text": (
                    f"{detail}. Gemini fallback is disabled by default; set "
                    "ENABLE_GEMINI_FALLBACK=true to opt in to sending prompts "
                    "to Gemini."
                ),
            }
            yield _finish_request(metrics, started_at, "error")
            return

        metrics["fallback_used"] = True
        yield {
            "type": "status",
            "text": "Claude unavailable; trying Gemini text-only fallback (no web search)",
        }
        metrics.update(
            {
                "provider": "gemini",
                "model": _configured_gemini_model(),
                "input_tokens": None,
                "output_tokens": None,
                "estimated_cost_usd": None,
                "cost_basis": "gemini_paid_list_estimate",
            }
        )
        gemini_metrics: dict[str, object] = {}
        try:
            gemini_stream = _gemini_text_chunks(cleaned_prompt, gemini_metrics)
            first_chunk = next(gemini_stream, None)
            metrics.update(gemini_metrics)
            if first_chunk is None:
                raise RuntimeError("Gemini returned an empty response")
        except Exception as error:
            metrics["error_type"] = type(error).__name__
            yield {"type": "error", "text": f"Gemini fallback failed: {error}"}
            yield _finish_request(metrics, started_at, "error")
            return

        yield {"type": "token", "text": first_chunk}
        try:
            for chunk in gemini_stream:
                yield {"type": "token", "text": chunk}
        except Exception as error:
            metrics.update(gemini_metrics)
            metrics["error_type"] = type(error).__name__
            yield {"type": "error", "text": f"Gemini stream failed: {error}"}
            yield _finish_request(metrics, started_at, "error")
            return

        yield {"type": "done", "text": ""}
        metrics.update(gemini_metrics)
        yield _finish_request(metrics, started_at, "success")
        return

    yield {"type": "token", "text": first_chunk}
    try:
        async for chunk in claude_stream:
            yield {"type": "token", "text": chunk}
    except Exception as error:
        metrics.update(claude_metrics)
        metrics["error_type"] = type(error).__name__
        yield {
            "type": "error",
            "text": "Claude stream failed after output began; Gemini was not started to avoid duplicating the response.",
        }
        yield _finish_request(metrics, started_at, "error")
        return

    yield {"type": "done", "text": ""}
    metrics.update(claude_metrics)
    yield _finish_request(metrics, started_at, "success")