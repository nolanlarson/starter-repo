# AI Agent Pipeline Starter

A Python agent pipeline template with an HTTP Server-Sent Events (SSE) endpoint
and a console client. Claude Code (Pro subscription) is the primary provider;
Gemini is an optional text-only fallback and is disabled by default.

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

Enter `/quit` to exit. The console keeps up to six recent turns in memory and
includes them with each request. History is not written to disk by default.
For a single prompt, the existing one-shot form is still available:

```bash
uv run agent-console "Summarize this week's support themes"
```

To explicitly persist plaintext conversation history for resume, provide a
path when starting the interactive console:

```bash
uv run agent-console --session-store ~/.agent-sessions.json
```

The file is restricted to the current user where supported. It retains up to
ten sessions and the latest six turns per session. Treat it as sensitive and
delete it when no longer needed. The console prints the matching resume command
when the session exits.

When the session ends, the console reports user turns, successful and failed
responses, provider-reported token totals, and available cost estimates. Gemini
costs are estimated only for known model pricing and defaults to the current
supported fallback path; free-tier requests may be free. Claude Code Pro usage is
shown as subscription usage, not
as a per-request dollar cost.

Before the first request, configure the provider credentials below. Claude
Code's CLI must be installed and signed in to your Pro account. The SDK uses
the same `claude` binary reported by your shell (`claude` on `PATH`, or the
`CLAUDE_CLI_PATH` override). The agent exposes only native `WebSearch` and
`WebFetch` tools by default; it cannot use shell, filesystem, or other Claude
Code tools. `ENABLE_WEB_SEARCH=false` disables those tools. Each request is
limited to four agent turns and 20,000 prompt characters, including chat
history. These are request-size safeguards, not a provider token or billing
quota; actual subscription/API limits are controlled by Claude and Google.

The client prints status updates and response chunks as SSE events arrive. The
server listens on `http://127.0.0.1:8000` by default. Configure it with
`--port`, and configure the console URL with `--url`:

```bash
uv run agent-server --port 9000
uv run agent-console --url http://127.0.0.1:9000/stream "Hello agent"
```

Keep the server bound to `127.0.0.1`. It has no user authentication and should
not be exposed to a network.

The endpoint accepts a JSON `POST` and streams `status`, `token`, `done`, and
`usage` events. You can inspect it without the console client using curl:

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
The Claude model defaults to `claude-haiku-5-5`; set `CLAUDE_MODEL` in `.env`
to another model available to your Claude Code account to override it.
If `ANTHROPIC_API_KEY` is set in your shell, unset it before running the app:
that variable takes precedence over Pro authentication and may incur API
charges, so this template skips Claude when it is present.

For Gemini fallback, create a key in [Google AI Studio](https://aistudio.google.com/apikey)
and set `GEMINI_API_KEY` in `.env`. Explicitly enable
`ENABLE_GEMINI_FALLBACK=true` to opt in.
Fallback is disabled by default so a Claude failure does not silently send your
prompt to another provider. When enabled, Gemini is a text-only fallback without
web search; responses to current-information requests may be outdated or
ungrounded. The default model is `gemini-3.5-flash-lite`; set `GEMINI_MODEL` to
another model available to your project if needed. Gemini's
free tier has model-specific quotas, and Google states that free-tier content
may be used to improve its products. Do not send sensitive data on that tier.

The free-tier Gemini path still does not automatically browse the web unless your
runtime adds a search tool or MCP provider explicitly. If you want real web
research in a live session, prefer the native Claude runtime when it exposes the
web tools.

Claude is attempted first. Gemini is tried only if explicitly enabled and Claude
fails before sending any text. If either provider fails after it has started
streaming, the endpoint returns an error event instead of mixing or duplicating
partial answers. The server does not persist chat history. The console persists
it only when started with `--session-store PATH`; resume with the command printed
at session exit, or run:

```bash
uv run agent-console-session <session-id> --session-store <path>
```

Saved transcripts are plaintext and should be treated as sensitive.

## Evaluations and logging

Run the opt-in live smoke-eval set with:

```bash
uv run agent-evals
```

The cases in `evals/cases.json` check that live responses contain expected text.
These deterministic checks are intentionally lightweight, not an LLM-as-judge
suite; running them makes real provider requests and consumes quota. Use unit
tests for fast offline checks, and expand the eval cases as the agent gains
specific behaviors to protect.

The server writes one JSON log record per pipeline request with a request ID,
provider, model, duration, outcome, fallback status, token usage, and cost basis.
Prompt and response text are deliberately excluded. Gemini cost values are
estimates at the standard paid list rate for supported models, not invoices;
Claude subscription usage has no per-request dollar estimate.

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
