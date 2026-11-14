from __future__ import annotations

import time

from .base import Engine, EngineError, Result, Usage, parse_json

# USD per million tokens: input, output. Cache reads bill at 0.1x input, cache writes at 1.25x.
# Override with --price-in/--price-out when prices change.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}


class AnthropicEngine(Engine):
    """Claude through the Anthropic API (official SDK). Needs ANTHROPIC_API_KEY."""

    name = "anthropic"
    default_parallel = 8
    billed = True

    def default_model(self) -> str:
        return "claude-haiku-5-5"

    def __init__(self, model: str | None = None, effort: str = "low", api_key: str | None = None,
                 price_in: float | None = None, price_out: float | None = None, **options):
        super().__init__(model, **options)
        try:
            import anthropic
        except ImportError:
            raise EngineError("the anthropic engine needs: pip install 'crowdpanel[anthropic]'", retryable=False)
        self._sdk = anthropic
        self.client = anthropic.AsyncAnthropic(api_key=api_key) if api_key else anthropic.AsyncAnthropic()
        self.effort = effort
        known = PRICES.get(self.model)
        self.price_in = price_in if price_in is not None else (known[0] if known else None)
        self.price_out = price_out if price_out is not None else (known[1] if known else None)

    def cost(self, u: Usage) -> float | None:
        if self.price_in is None or self.price_out is None:
            return None
        return (
            u.input_tokens * self.price_in
            + u.cached_input_tokens * self.price_in * 0.1
            + u.cache_write_tokens * self.price_in * 1.25
            + u.output_tokens * self.price_out
        ) / 1_000_000

    async def generate(self, system: str, prompt: str, schema: dict | None = None, max_tokens: int = 4000) -> Result:
        sdk = self._sdk
        output_config: dict = {}
        if self.effort:
            output_config["effort"] = self.effort
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        kwargs = {}
        if output_config:
            kwargs["output_config"] = output_config

        started = time.monotonic()
        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                # the system prompt (rules + scenario) is the same for every persona, so it is cached
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": prompt}],
                **kwargs,
            )
        except (sdk.AuthenticationError, sdk.PermissionDeniedError, sdk.NotFoundError, sdk.BadRequestError) as e:
            raise EngineError(f"anthropic: {e.message}", retryable=False)
        except sdk.RateLimitError as e:
            raise EngineError(f"anthropic rate limit: {e.message}")
        except sdk.APIStatusError as e:
            raise EngineError(f"anthropic error {e.status_code}: {e.message}")
        except sdk.APIConnectionError as e:
            raise EngineError(f"anthropic connection error: {e}")

        if response.stop_reason == "refusal":
            raise EngineError("anthropic: the model declined this request", retryable=False)
        if response.stop_reason == "max_tokens":
            raise EngineError(f"anthropic: answer cut at max_tokens={max_tokens}")

        text = "".join(b.text for b in response.content if b.type == "text")
        u = response.usage
        usage = Usage(
            input_tokens=u.input_tokens or 0,
            cached_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
            output_tokens=u.output_tokens or 0,
        )
        usage.cost_usd = self.cost(usage)
        data = parse_json(text) if schema is not None else None
        return Result(text=text, data=data, usage=usage, seconds=time.monotonic() - started)

    async def close(self) -> None:
        await self.client.close()
