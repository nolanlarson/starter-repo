# AI Agent Pipeline Starter

A Python agent pipeline template with an HTTP Server-Sent Events (SSE) endpoint
and a console client. Claude Code (Pro subscription) is the primary provider;
Gemini is used as a fallback if Claude is unavailable before producing output.

## Run it

Requires Python 3.10 or later and [uv](https://docs.astral.sh/uv/getting-started/installation/).
`uv sync` creates the project environment, installs the project and its
development tools, and creates or updates `uv.lock`.

```bash
uv sync
uv run pytest
```

In one terminal, start the app:

```bash
uv run agent-server
```

In another terminal, start a chat session through the streaming console client:

```bash
uv run agent-console
```

Enter `/quit` to exit. The console keeps the conversation history for the
current session and includes it with each request. For a single prompt, the
existing one-shot form is still available:

```bash
uv run agent-console "Summarize this week's support themes"
```

Before the first request, configure the provider credentials below. Claude
Code's CLI must be installed and signed in to your Pro account. The SDK runs
Claude with built-in tools disabled; this template is currently a chat pipeline,
not an agent with filesystem or shell access.

The client prints status updates and response chunks as SSE events arrive. The
server listens on `http://127.0.0.1:8000` by default. Configure it with
`--port`, and configure the console URL with `--url`:

```bash
uv run agent-server --port 9000
uv run agent-console --url http://127.0.0.1:9000/stream "Hello agent"
```

Keep the server bound to `127.0.0.1`. It has no user authentication and should
not be exposed to a network.

The endpoint accepts a JSON `POST` and streams `status`, `token`, and `done`
events. You can inspect it without the console client using curl:

```bash
curl -N -H 'Content-Type: application/json' \
	-d '{"prompt":"Hello agent"}' http://127.0.0.1:8000/stream
```

`GET /health` returns a small JSON health response.

## Local environment settings

The app loads `.env` automatically. Create it from the example without
overwriting an existing file:

```bash
test -f .env || cp .env.example .env
```

For Claude Code Pro, install and sign in to the [Claude Code CLI](https://code.claude.com/docs/en/quickstart), then run:

```bash
claude setup-token
```

Paste the printed token into `.env` as `CLAUDE_CODE_OAUTH_TOKEN`. This uses your
Claude subscription allowance, shared with Claude chat and Claude Code; it is
not a separate Claude API key or API credit balance. Keep the token private.
If `ANTHROPIC_API_KEY` is set in your shell, unset it before running the app:
that variable takes precedence over Pro authentication and may incur API
charges, so this template skips Claude when it is present.

For Gemini fallback, create a key in [Google AI Studio](https://aistudio.google.com/apikey)
and set `GEMINI_API_KEY` in `.env`. The default model is `gemini-2.5-flash`; set
`GEMINI_MODEL` to another model available to your project if needed. Gemini's
free tier has model-specific quotas, and Google states that free-tier content
may be used to improve its products. Do not send sensitive data on that tier.

Claude is attempted first. Gemini is tried only if Claude fails before sending
any text. If either provider fails after it has started streaming, the endpoint
returns an error event instead of mixing or duplicating partial answers. The
server does not persist chat history; the console resends the active session's
history with each prompt, and that history is discarded when the console exits.

## How the pieces fit

- `app/agent.py` adapts Claude and Gemini text streams into structured events
	and owns the pre-output fallback behavior.
- `app/server.py` exposes the agent as an HTTP SSE endpoint. It validates the
	request and translates each pipeline event into the SSE wire format.
- `app/console.py` is a streaming client. It posts a prompt and renders status
	and token events without waiting for the full response.
- `tests/` contains unit tests for the pipeline event sequence and original
	greeting scaffold.
- `src/` retains the original minimal greeting example as a separate scaffold.

## Dependency workflow

Add runtime dependencies with `uv add <package>` and development-only tools
with `uv add --dev <package>`. Commit both `pyproject.toml` and `uv.lock` so
other developers and CI use the same resolved versions. For CI or a
reproducibility check, use `uv sync --locked` to fail if the lockfile is stale.

```bash
uv add openai
uv add --dev ruff
uv sync --locked
```
