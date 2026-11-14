# Engines

An engine is the way crowdpanel reaches a model. Pick one with `--engine` and, optionally, a model with `--model`. The engine and model are saved with the simulation, so `resume`, `survey`, `report` and `ask` reuse them unless you pass other ones.

Every engine receives the same two things: a system prompt (the rules and the scenario, identical for every persona) and a user prompt (one persona, its memory and its feed), and must return JSON that matches a schema. The schemas use the strict subset every provider accepts: all properties required, no extra properties, `null` for unused fields.

## claude-code

Runs the [Claude Code](https://code.claude.com) CLI you already have installed and logged in, once per call:

```
claude -p --model haiku --effort low --system-prompt "..." --tools "" --strict-mcp-config \
  --setting-sources "" --no-session-persistence --output-format json --json-schema '{...}'
```

- No tools, no MCP servers, no settings, hooks or memory files are loaded, and each call runs in an empty temporary folder, so nothing from your projects reaches the personas. This also keeps each call around 1,000 tokens of overhead instead of the tens of thousands a normal Claude Code session carries.
- Default model `haiku`; any model alias or name Claude Code accepts works (`--model sonnet`).
- `--effort low` by default (`--effort medium` and so on to change it).
- Usage counts against your Claude plan limits, which are shared with your other Claude Code sessions. A long simulation can use a good part of a usage window.
- If `ANTHROPIC_API_KEY` is set in your environment, Claude Code would bill the API instead of your plan. crowdpanel removes it for this engine; pass `--use-api-key` if you do want API billing through the CLI.
- Each call starts the CLI, which takes a few seconds. crowdpanel runs several personas at once (6 by default, `--parallel` to change).

## codex

Runs the [Codex CLI](https://github.com/openai/codex) logged in with your ChatGPT account (`codex login`):

```
codex exec --json --ephemeral --skip-git-repo-check --sandbox read-only -o OUT --output-schema SCHEMA -
```

- Codex has no system prompt flag, so the rules go at the top of the prompt, with an instruction not to run commands.
- Read-only sandbox, nothing saved to `~/.codex/sessions`, empty temporary folder.
- `--model` is passed to `codex exec -m`; without it Codex uses your configured default.
- Usage counts against your ChatGPT plan limits.

## anthropic

The Anthropic API through the official Python SDK (`pip install "crowdpanel[anthropic]"`).

- Reads `ANTHROPIC_API_KEY` (or the variable named in `--api-key-env`).
- Default model `claude-haiku-5-5`. Use `--model claude-sonnet-5-5` or `--model claude-opus-5-5` for richer personas at a higher cost.
- Structured output through `output_config.format`, reasoning effort through `output_config.effort` (`low` by default).
- The system prompt is marked for prompt caching. It is shared by every persona, so after the first call it is billed at the cache rate when it is long enough to be cached.
- Cost is tracked with built-in prices per model; pass `--price-in` and `--price-out` (USD per million tokens) when prices change or for other models.

## openai

The OpenAI Chat Completions API through the official Python SDK (`pip install "crowdpanel[openai]"`). Works with OpenAI and with any server that speaks the same API: OpenRouter, Groq, Together, Fireworks, DeepSeek, Ollama, LM Studio, vLLM and others.

- `--model` is required.
- Reads `OPENAI_API_KEY` by default. For other providers, `--base-url` and `--api-key-env`:

  ```bash
  crowdpanel run s.yaml p.yaml --engine openai \
    --base-url https://openrouter.ai/api/v1 --api-key-env OPENROUTER_API_KEY --model some/model
  ```

- `--json-mode schema` (default) asks for strict JSON Schema output. If the server does not support it, use `--json-mode object` (JSON mode plus the schema in the prompt) or `--json-mode prompt` (schema in the prompt only).
- Cost is tracked only when you pass `--price-in` and `--price-out`.

## fake

Random answers that follow the schemas, with no model at all. Use it to try the pipeline, the web interface and the reports, and in tests.

## Adding an engine

Subclass `crowdpanel.engines.base.Engine`, implement `async generate(system, prompt, schema, max_tokens)` returning a `Result` with `text`, parsed `data` (when a schema was given) and `usage`, and register it in `crowdpanel/engines/__init__.py`. Raise `EngineError(..., retryable=False)` for problems that retrying cannot fix (bad credentials, missing CLI) so the simulation stops with a clear message instead of retrying.
