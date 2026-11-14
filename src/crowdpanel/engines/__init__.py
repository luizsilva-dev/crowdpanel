from __future__ import annotations

from typing import Any

from .base import Engine, EngineError, Result, Usage

ENGINES = ("claude-code", "codex", "anthropic", "openai", "fake")


def get_engine(name: str, model: str | None = None, **options: Any) -> Engine:
    options = {k: v for k, v in options.items() if v is not None}
    if name == "claude-code":
        from .claude_code import ClaudeCodeEngine
        return ClaudeCodeEngine(model, **options)
    if name == "codex":
        from .codex import CodexEngine
        return CodexEngine(model, **options)
    if name == "anthropic":
        from .anthropic_api import AnthropicEngine
        return AnthropicEngine(model, **options)
    if name == "openai":
        from .openai_api import OpenAIEngine
        return OpenAIEngine(model, **options)
    if name == "fake":
        from .fake import FakeEngine
        return FakeEngine(model, **options)
    raise EngineError(f"unknown engine '{name}' (choose from: {', '.join(ENGINES)})", retryable=False)


__all__ = ["ENGINES", "Engine", "EngineError", "Result", "Usage", "get_engine"]
