"""Console client for the agent's Server-Sent Events endpoint."""

import argparse
import json
import os
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

MAX_HISTORY_TURNS = 6
MAX_STORED_SESSIONS = 10


@dataclass
class StreamResult:
    text: str
    usage: dict[str, Any] | None = None
    error: str | None = None


@dataclass
class SessionSummary:
    requests: int = 0
    successful_responses: int = 0
    failed_responses: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    token_reports: int = 0
    estimated_api_cost_usd: float = 0.0
    estimated_cost_reports: int = 0
    subscription_requests: int = 0
    unpriced_requests: int = 0
    session_id: str | None = None
    session_store_path: Path | None = None

    def record(self, result: StreamResult) -> None:
        if result.error:
            self.failed_responses += 1
        else:
            self.successful_responses += 1

        usage = result.usage or {}
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        if isinstance(input_tokens, int) and isinstance(output_tokens, int):
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens
            self.token_reports += 1

        cost = usage.get("estimated_cost_usd")
        if isinstance(cost, (int, float)):
            self.estimated_api_cost_usd += cost
            self.estimated_cost_reports += 1

        cost_basis = usage.get("cost_basis")
        if cost_basis == "claude_subscription":
            self.subscription_requests += 1
        elif cost_basis == "gemini_paid_list_estimate" and cost is None:
            self.unpriced_requests += 1

    def print(self) -> None:
        print("\nSession summary")
        if self.session_id:
            print(f"Session ID: {self.session_id}")
            if self.session_store_path:
                store_argument = shlex.quote(str(self.session_store_path))
                print(
                    "Resume command: uv run agent-console-session "
                    f"{self.session_id} --session-store {store_argument}"
                )
            else:
                print("History was not saved; use --session-store PATH to enable resume.")
        print(f"User turns: {self.requests}")
        print(f"Completed responses: {self.successful_responses}")
        print(f"Failed responses: {self.failed_responses}")
        if self.token_reports:
            total_tokens = self.input_tokens + self.output_tokens
            print(
                f"Provider-reported tokens ({self.token_reports} responses): "
                f"{self.input_tokens} input, {self.output_tokens} output, "
                f"{total_tokens} total"
            )
        else:
            print("Provider-reported tokens: unavailable")
        if self.estimated_cost_reports:
            print(
                "Estimated Gemini API cost at paid list rates "
                f"(not an invoice; free-tier use may be $0): "
                f"${self.estimated_api_cost_usd:.6f}"
            )
        else:
            print("Estimated API cost: unavailable")
        if self.subscription_requests:
            print(
                f"Claude Pro requests: {self.subscription_requests} "
                "(subscription usage; no per-request dollar estimate)"
            )
        if self.unpriced_requests:
            print(f"Gemini requests without a known rate: {self.unpriced_requests}")


def _normalize_history(history: Any) -> list[tuple[str, str]]:
    if not isinstance(history, list):
        return []
    normalized: list[tuple[str, str]] = []
    for item in history:
        if isinstance(item, tuple) and len(item) == 2:
            speaker, message = item
            normalized.append((str(speaker), str(message)))
        elif isinstance(item, list) and len(item) == 2:
            speaker, message = item
            normalized.append((str(speaker), str(message)))
    return normalized


def load_session_history(session_id: str | None, path: str | Path) -> list[tuple[str, str]]:
    if not session_id or not path:
        return []
    store_path = Path(path)
    if not store_path.exists():
        return []
    try:
        payload = json.loads(store_path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    if payload.get("session_id") == session_id:
        return _normalize_history(payload.get("history", []))
    sessions = payload.get("sessions")
    if isinstance(sessions, dict):
        session_data = sessions.get(session_id)
        if isinstance(session_data, dict):
            return _normalize_history(session_data.get("history", []))
    return []


def save_session(session_id: str, history: list[tuple[str, str]], path: str | Path) -> None:
    store_path = Path(path)
    payload: dict[str, Any] = {"session_id": session_id, "history": _normalize_history(history)}
    if store_path.exists():
        try:
            existing = json.loads(store_path.read_text())
            if isinstance(existing, dict):
                payload = existing
        except (OSError, json.JSONDecodeError):
            payload = {}

    if not isinstance(payload.get("sessions"), dict):
        payload["sessions"] = {}
    recent_history = _normalize_history(history)[-2 * MAX_HISTORY_TURNS :]
    payload["session_id"] = session_id
    payload["history"] = recent_history
    payload["sessions"][session_id] = {"history": recent_history}
    for old_session_id in list(payload["sessions"])[:-MAX_STORED_SESSIONS]:
        del payload["sessions"][old_session_id]
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    try:
        os.chmod(store_path, 0o600)
    except OSError:
        pass


def stream_prompt(url: str, prompt: str) -> StreamResult:
    body = json.dumps({"prompt": prompt}).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    response_chunks: list[str] = []
    usage: dict[str, Any] | None = None
    error_message: str | None = None

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
                    error_message = text or "agent request failed"
                elif event_type == "usage":
                    usage = event
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"server returned HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"could not reach {url}: {error.reason}") from error

    return StreamResult("".join(response_chunks), usage, error_message)


def _conversation_prompt(history: list[tuple[str, str]], prompt: str) -> str:
    if not history:
        return prompt

    recent_history = history[-2 * MAX_HISTORY_TURNS :]
    transcript = "\n".join(
        f"{speaker}: {message}" for speaker, message in recent_history
    )
    return (
        "Continue this conversation using the earlier turns as context.\n\n"
        f"{transcript}\nUser: {prompt}\nAssistant:"
    )


def run_chat(
    url: str,
    session_id: str | None = None,
    session_store_path: str | Path | None = None,
) -> None:
    active_session_id = session_id or uuid4().hex[:12]
    history = (
        load_session_history(active_session_id, path=session_store_path)
        if session_store_path
        else []
    )
    summary = SessionSummary(
        session_id=active_session_id,
        session_store_path=Path(session_store_path) if session_store_path else None,
    )
    if history:
        print(f"Resuming session {active_session_id}.")
    print("Chat started. Enter /quit to exit.")

    while True:
        try:
            prompt = input("You: ")
        except EOFError:
            print()
            break

        if prompt.strip() == "/quit":
            if session_store_path:
                save_session(active_session_id, history, path=session_store_path)
            print("Goodbye.")
            break
        if not prompt.strip():
            continue

        print("Assistant: ", end="", flush=True)
        summary.requests += 1
        try:
            result = stream_prompt(url, _conversation_prompt(history, prompt))
        except RuntimeError as error:
            summary.failed_responses += 1
            print(file=sys.stderr)
            print(f"Error: {error}", file=sys.stderr)
            continue

        summary.record(result)
        if result.error:
            print(file=sys.stderr)
            print(f"Error: {result.error}", file=sys.stderr)
            continue

        history.extend((("User", prompt), ("Assistant", result.text)))

    if session_store_path:
        save_session(active_session_id, history, path=session_store_path)
    summary.print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Stream a prompt to the agent")
    parser.add_argument(
        "--url", default="http://127.0.0.1:8000/stream", help="SSE endpoint URL"
    )
    parser.add_argument(
        "--session-id", help="Resume an existing session by ID"
    )
    parser.add_argument(
        "--session-store",
        type=Path,
        help="Persist conversation history at this path (plaintext, user-controlled)",
    )
    parser.add_argument(
        "prompt", nargs="?", help="Optional one-shot prompt; omit for interactive chat"
    )
    args = parser.parse_args()

    if args.prompt is None:
        if args.session_id and not args.session_store:
            parser.error("--session-id requires --session-store PATH")
        run_chat(
            args.url,
            session_id=args.session_id,
            session_store_path=args.session_store,
        )
        return

    try:
        result = stream_prompt(args.url, args.prompt)
    except RuntimeError as error:
        parser.exit(1, f"Error: {error}\n")
    if result.error:
        parser.exit(1, f"Error: {result.error}\n")


def main_session() -> None:
    parser = argparse.ArgumentParser(description="Resume a previous console chat session")
    parser.add_argument("session_id", help="Session ID to resume")
    parser.add_argument(
        "--session-store",
        type=Path,
        required=True,
        help="Path to the plaintext session store used when the chat was saved",
    )
    parser.add_argument(
        "--url", default="http://127.0.0.1:8000/stream", help="SSE endpoint URL"
    )
    args = parser.parse_args()
    run_chat(
        args.url,
        session_id=args.session_id,
        session_store_path=args.session_store,
    )


if __name__ == "__main__":
    main()