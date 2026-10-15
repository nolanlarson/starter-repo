"""Console client for the agent's Server-Sent Events endpoint."""

import argparse
import json
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def stream_prompt(url: str, prompt: str) -> str:
    body = json.dumps({"prompt": prompt}).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    response_chunks: list[str] = []

    try:
        with urlopen(request, timeout=60) as response:
            for line in response:
                if not line.startswith(b"data:"):
                    continue
                event: Any = json.loads(line[5:].strip())
                event_type = event.get("type")
                text = event.get("text", "")
                if event_type == "status":
                    print(f"[{text}]", file=sys.stderr)
                elif event_type == "token":
                    response_chunks.append(text)
                    print(text, end="", flush=True)
                elif event_type == "done":
                    print()
                elif event_type == "error":
                    raise RuntimeError(text or "agent request failed")
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"server returned HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"could not reach {url}: {error.reason}") from error

    return "".join(response_chunks)


def _conversation_prompt(history: list[tuple[str, str]], prompt: str) -> str:
    if not history:
        return prompt

    transcript = "\n".join(
        f"{speaker}: {message}" for speaker, message in history
    )
    return (
        "Continue this conversation using the earlier turns as context.\n\n"
        f"{transcript}\nUser: {prompt}\nAssistant:"
    )


def run_chat(url: str) -> None:
    history: list[tuple[str, str]] = []
    print("Chat started. Enter /quit to exit.")

    while True:
        try:
            prompt = input("You: ")
        except EOFError:
            print()
            return

        if prompt.strip() == "/quit":
            print("Goodbye.")
            return
        if not prompt.strip():
            continue

        print("Assistant: ", end="", flush=True)
        try:
            response = stream_prompt(url, _conversation_prompt(history, prompt))
        except RuntimeError as error:
            print(file=sys.stderr)
            print(f"Error: {error}", file=sys.stderr)
            continue

        history.extend((("User", prompt), ("Assistant", response)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Stream a prompt to the agent")
    parser.add_argument(
        "--url", default="http://127.0.0.1:8000/stream", help="SSE endpoint URL"
    )
    parser.add_argument(
        "prompt", nargs="?", help="Optional one-shot prompt; omit for interactive chat"
    )
    args = parser.parse_args()

    if args.prompt is None:
        run_chat(args.url)
        return

    try:
        stream_prompt(args.url, args.prompt)
    except RuntimeError as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()