"""Claude-first agent pipeline with a Gemini fallback."""

import logging
import os
from collections.abc import AsyncIterator, Iterator

from dotenv import load_dotenv

load_dotenv()

LOGGER = logging.getLogger(__name__)
AgentEvent = dict[str, str]


async def _claude_text_chunks(prompt: str) -> AsyncIterator[str]:
    from claude_agent_sdk import ClaudeAgentOptions, StreamEvent, query

    options = ClaudeAgentOptions(
        tools=[],
        allowed_tools=[],
        max_turns=1,
        include_partial_messages=True,
        permission_mode="dontAsk",
    )
    async for message in query(prompt=prompt, options=options):
        if not isinstance(message, StreamEvent):
            continue
        event = message.event
        delta = event.get("delta", {})
        if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
            text = delta.get("text", "")
            if text:
                yield text


def _gemini_text_chunks(prompt: str) -> Iterator[str]:
    from google import genai

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    client = genai.Client(api_key=api_key)
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    try:
        for response in client.models.generate_content_stream(
            model=model,
            contents=prompt,
        ):
            if response.text:
                yield response.text
    finally:
        client.close()


async def stream_agent_response(prompt: str) -> AsyncIterator[AgentEvent]:
    """Stream a Claude response, falling back to Gemini before any text is sent."""
    cleaned_prompt = prompt.strip()
    if not cleaned_prompt:
        raise ValueError("prompt must not be empty")

    yield {"type": "status", "text": "Connecting to Claude"}
    if os.getenv("ANTHROPIC_API_KEY"):
        LOGGER.warning("Skipping Claude because ANTHROPIC_API_KEY could incur API billing")
        first_chunk = None
    else:
        claude_stream = _claude_text_chunks(cleaned_prompt)
        try:
            first_chunk = await anext(claude_stream, None)
        except Exception as error:
            LOGGER.warning("Claude failed before streaming; trying Gemini: %s", error)
            first_chunk = None

    if first_chunk is None:
        yield {"type": "status", "text": "Claude unavailable; trying Gemini fallback"}
        try:
            gemini_stream = _gemini_text_chunks(cleaned_prompt)
            first_chunk = next(gemini_stream, None)
            if first_chunk is None:
                raise RuntimeError("Gemini returned an empty response")
        except Exception as error:
            LOGGER.warning("Gemini fallback failed: %s", error)
            yield {"type": "error", "text": f"Gemini fallback failed: {error}"}
            return

        yield {"type": "token", "text": first_chunk}
        try:
            for chunk in gemini_stream:
                yield {"type": "token", "text": chunk}
        except Exception as error:
            LOGGER.warning("Gemini stream failed: %s", error)
            yield {"type": "error", "text": f"Gemini stream failed: {error}"}
            return

        yield {"type": "done", "text": ""}
        return

    yield {"type": "token", "text": first_chunk}
    try:
        async for chunk in claude_stream:
            yield {"type": "token", "text": chunk}
    except Exception as error:
        LOGGER.warning("Claude stream failed after output began: %s", error)
        yield {
            "type": "error",
            "text": "Claude stream failed after output began; Gemini was not started to avoid duplicating the response.",
        }
        return

    yield {"type": "done", "text": ""}