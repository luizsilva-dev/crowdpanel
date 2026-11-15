from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


class EngineError(RuntimeError):
    """A call that failed for good (after the engine's own retries)."""

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class Usage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None  # None when the engine can't tell (subscription CLIs report tokens only)

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.cached_input_tokens += other.cached_input_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.output_tokens += other.output_tokens
        if other.cost_usd is not None:
            self.cost_usd = (self.cost_usd or 0.0) + other.cost_usd


@dataclass
class Result:
    text: str
    data: Any = None
    usage: Usage = field(default_factory=Usage)
    seconds: float = 0.0


class Engine:
    """One way of reaching a model. Subclasses implement `generate`.

    `schema` is a JSON Schema written in the strict subset every provider accepts:
    every object lists all its properties in `required` and sets `additionalProperties: false`.
    When a schema is given, `Result.data` holds the parsed object.
    """

    name = "base"
    default_parallel = 4
    billed = False  # True when every call costs money (API engines)

    def __init__(self, model: str | None = None, **options: Any):
        self.model = model or self.default_model()
        self.options = options

    def default_model(self) -> str:
        return ""

    def describe(self) -> str:
        return f"{self.name} ({self.model})" if self.model else self.name

    supports_knowledge = False  # can read files from a folder while answering

    async def generate(self, system: str, prompt: str, schema: dict | None = None, max_tokens: int = 4000,
                       knowledge_dir: str | None = None) -> Result:
        raise NotImplementedError

    async def close(self) -> None:
        return None


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_json(text: str) -> Any:
    """Parses a JSON answer, tolerating code fences and text around the object."""
    cleaned = _FENCE.sub("", (text or "").strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                pass
    raise EngineError(f"model did not return valid JSON: {cleaned[:200]!r}")


def schema_hint(schema: dict) -> str:
    """For engines without native structured output: the schema goes into the prompt."""
    return (
        "\n\nReply with a single JSON object and nothing else. It must match this JSON Schema:\n"
        + json.dumps(schema, ensure_ascii=False)
    )
