from __future__ import annotations

import asyncio
import random
import re

from .base import Engine, Result, Usage

_WORDS = (
    "price shipping quality support trial monthly annual cancel refund discount value "
    "subscription delivery service contract option plan fee budget customer worth"
).split()


class FakeEngine(Engine):
    """Offline engine with random but schema-valid answers. For tests, demos and dry runs."""

    name = "fake"
    default_parallel = 8

    def default_model(self) -> str:
        return "fake"

    def __init__(self, model: str | None = None, seed: int = 7, delay: float = 0.0, fail_rate: float = 0.0, **options):
        super().__init__(model, **options)
        self.rng = random.Random(seed)
        self.delay = delay
        self.fail_rate = fail_rate
        self.calls = 0

    def _sentence(self) -> str:
        words = self.rng.sample(_WORDS, k=self.rng.randint(5, 10))
        return " ".join(words).capitalize() + "."

    def _value(self, schema: dict, prompt: str):
        kind = schema.get("type")
        if isinstance(kind, list):
            kind = next((k for k in kind if k != "null"), "null")
        if "enum" in schema:
            return self.rng.choice(schema["enum"])
        if kind == "object":
            return {k: self._value(v, prompt) for k, v in schema.get("properties", {}).items()}
        if kind == "array":
            n = self.rng.randint(schema.get("minItems", 1), max(schema.get("minItems", 1), min(schema.get("maxItems", 3), 3)))
            return [self._value(schema["items"], prompt) for _ in range(n)]
        if kind == "integer":
            ids = [int(x) for x in re.findall(r"#(\d+)", prompt)]
            if ids and schema.get("description", "").lower().startswith("id"):
                return self.rng.choice(ids)
            return self.rng.randint(schema.get("minimum", 0), schema.get("maximum", 10))
        if kind == "number":
            return round(self.rng.uniform(schema.get("minimum", 0), schema.get("maximum", 100)), 2)
        if kind == "boolean":
            return self.rng.random() < 0.5
        if kind == "null":
            return None
        return self._sentence()

    async def generate(self, system: str, prompt: str, schema: dict | None = None, max_tokens: int = 4000,
                       knowledge_dir: str | None = None) -> Result:
        from .base import EngineError

        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail_rate and self.rng.random() < self.fail_rate:
            raise EngineError("fake failure")
        usage = Usage(input_tokens=len(system + prompt) // 4, output_tokens=60)
        if schema is None:
            return Result(text="\n\n".join(self._sentence() for _ in range(3)), usage=usage)
        data = self._value(schema, prompt)
        return Result(text="", data=data, usage=usage)
