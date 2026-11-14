from __future__ import annotations

import json
import time

from .base import Engine, EngineError, Result, Usage, parse_json
from .cli_common import CliRunner


class ClaudeCodeEngine(Engine):
    """Uses the locally installed Claude Code CLI (`claude -p`), logged in with your own plan.

    Calls count against your Claude plan limits, shared with your other Claude Code sessions.
    For personal use under Anthropic's terms; anything offered to other people should use the
    `anthropic` engine with an API key instead.
    """

    name = "claude-code"
    default_parallel = 6

    def default_model(self) -> str:
        return "haiku"

    def __init__(self, model: str | None = None, effort: str = "low", use_api_key: bool = False,
                 timeout: float = 240.0, **options):
        super().__init__(model, **options)
        self.effort = effort
        # An exported ANTHROPIC_API_KEY makes the CLI bill the API instead of the plan; drop it
        # unless the user explicitly wants that.
        drop = () if use_api_key else ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
        self.runner = CliRunner("claude", timeout=timeout, env_drop=drop)

    def build_args(self, system: str, schema: dict | None) -> list[str]:
        args = [
            "-p",
            "--model", self.model,
            "--system-prompt", system,
            "--tools", "",
            "--strict-mcp-config",
            "--setting-sources", "",
            "--no-session-persistence",
            "--output-format", "json",
        ]
        if self.effort:
            args += ["--effort", self.effort]
        if schema is not None:
            args += ["--json-schema", json.dumps(schema)]
        return args

    async def generate(self, system: str, prompt: str, schema: dict | None = None, max_tokens: int = 4000) -> Result:
        started = time.monotonic()
        code, out, err = await self.runner.run(self.build_args(system, schema), prompt)
        try:
            payload = json.loads(out)
        except json.JSONDecodeError:
            raise EngineError(f"claude exited with {code}: {(err or out).strip()[:300]}")
        if payload.get("is_error"):
            message = str(payload.get("result") or payload.get("subtype") or "unknown error")
            retryable = not any(s in message.lower() for s in ("not logged in", "invalid api key", "please run /login"))
            raise EngineError(f"claude: {message[:300]}", retryable=retryable)

        raw = payload.get("usage") or {}
        usage = Usage(
            input_tokens=int(raw.get("input_tokens", 0)),
            cached_input_tokens=int(raw.get("cache_read_input_tokens", 0)),
            cache_write_tokens=int(raw.get("cache_creation_input_tokens", 0)),
            output_tokens=int(raw.get("output_tokens", 0)),
        )
        text = str(payload.get("result") or "")
        data = None
        if schema is not None:
            data = payload.get("structured_output")
            if data is None:
                data = parse_json(text)
        return Result(text=text, data=data, usage=usage, seconds=time.monotonic() - started)

    async def close(self) -> None:
        self.runner.cleanup()
