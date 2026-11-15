from __future__ import annotations

import json
import os
import time
import uuid

from .base import Engine, EngineError, Result, Usage, parse_json
from .cli_common import CliRunner


class CodexEngine(Engine):
    """Uses the locally installed OpenAI Codex CLI (`codex exec`), logged in with your ChatGPT plan
    (`codex login`). Calls count against your ChatGPT plan limits. Personal use only."""

    name = "codex"
    default_parallel = 4

    def __init__(self, model: str | None = None, timeout: float = 300.0, **options):
        super().__init__(model, **options)
        self.runner = CliRunner("codex", timeout=timeout)

    def build_args(self, schema_path: str | None, out_path: str) -> list[str]:
        args = [
            "exec",
            "--json",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox", "read-only",
            "-o", out_path,
        ]
        if self.model:
            args += ["-m", self.model]
        if schema_path:
            args += ["--output-schema", schema_path]
        args.append("-")  # prompt comes from stdin
        return args

    async def generate(self, system: str, prompt: str, schema: dict | None = None, max_tokens: int = 4000,
                       knowledge_dir: str | None = None) -> Result:
        started = time.monotonic()
        tag = uuid.uuid4().hex
        out_path = os.path.join(self.runner.workdir, f"{tag}.out")
        schema_path = None
        if schema is not None:
            schema_path = os.path.join(self.runner.workdir, f"{tag}.schema.json")
            with open(schema_path, "w", encoding="utf-8") as fh:
                json.dump(schema, fh)

        # codex exec has no system prompt flag, so the instructions go first in the prompt
        full_prompt = (
            "Do not run commands, read files or use tools. Answer directly.\n\n"
            f"<instructions>\n{system}\n</instructions>\n\n{prompt}"
        )
        try:
            code, out, err = await self.runner.run(self.build_args(schema_path, out_path), full_prompt)
            usage = Usage()
            failure = None
            for line in out.splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = event.get("type")
                if kind == "turn.completed":
                    raw = event.get("usage") or {}
                    usage.input_tokens += int(raw.get("input_tokens", 0))
                    usage.cached_input_tokens += int(raw.get("cached_input_tokens", 0))
                    usage.output_tokens += int(raw.get("output_tokens", 0)) + int(raw.get("reasoning_output_tokens", 0))
                elif kind in ("turn.failed", "error"):
                    failure = json.dumps(event)[:300]
            if failure or code != 0:
                raise EngineError(f"codex failed ({code}): {failure or err.strip()[:300]}")
            try:
                with open(out_path, encoding="utf-8") as fh:
                    text = fh.read().strip()
            except FileNotFoundError:
                raise EngineError("codex returned no final message")
        finally:
            for path in (out_path, schema_path):
                if path and os.path.exists(path):
                    os.remove(path)

        data = parse_json(text) if schema is not None else None
        return Result(text=text, data=data, usage=usage, seconds=time.monotonic() - started)

    async def close(self) -> None:
        self.runner.cleanup()
