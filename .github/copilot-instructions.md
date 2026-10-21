# Repository Guidelines

## Project Shape

- `app/agent.py` owns provider selection, the Claude web-only tool boundary, fallback behavior, and request limits.
- `app/server.py` exposes the local SSE endpoint; `app/console.py` is its interactive client.
- Keep provider/runtime behavior in `app/agent.py`, and preserve the documented SSE event contract.

## Development

- Use `uv` for dependencies and commands. Start with `uv sync --locked`.
- Run `uv run pytest -q` for offline validation.
- `uv run agent-evals` makes live provider requests and consumes quota; do not run it unless explicitly requested.
- Add focused tests for provider, persistence, server, and CLI behavior changes.

## Privacy and Tool Boundaries

- Do not commit `.env`, credentials, or provider output containing user data.
- Do not log prompts, responses, API keys, OAuth tokens, or raw provider error text.
- Keep Claude restricted to `WebSearch` and `WebFetch`; do not add shell, filesystem, or other tools without an explicit design decision and documentation.
- Keep Gemini fallback disabled by default. It is text-only and must not be described as a web-search fallback.
- Keep chat history in memory by default. Disk persistence must remain opt-in and use restrictive file permissions.
- Preserve the prompt and agent-turn limits unless a change includes a documented resource rationale and tests.
