from __future__ import annotations

import time

from .base import Engine, EngineError, Result, Usage, parse_json, schema_hint


class OpenAIEngine(Engine):
    """OpenAI or any OpenAI-compatible API (OpenRouter, Groq, Together, Ollama, LM Studio, vLLM...).

    Needs OPENAI_API_KEY (or --api-key). Point --base-url at a compatible server to use another
    provider. Use --json-mode object or prompt when the server does not support json_schema.
    """

    name = "openai"
    default_parallel = 8
    billed = True

    def __init__(self, model: str | None = None, api_key: str | None = None, base_url: str | None = None,
                 json_mode: str = "schema", price_in: float | None = None, price_out: float | None = None,
                 **options):
        super().__init__(model, **options)
        if not self.model:
            raise EngineError("the openai engine needs --model (the model name your provider uses)", retryable=False)
        try:
            import openai
        except ImportError:
            raise EngineError("the openai engine needs: pip install 'crowdpanel[openai]'", retryable=False)
        if json_mode not in ("schema", "object", "prompt"):
            raise EngineError("json_mode must be schema, object or prompt", retryable=False)
        self._sdk = openai
        kwargs = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self.client = openai.AsyncOpenAI(**kwargs)
        self.json_mode = json_mode
        self.price_in = price_in
        self.price_out = price_out
        self.billed = bool(price_in or price_out) or not base_url

    def describe(self) -> str:
        base = str(self.client.base_url)
        host = base.split("//", 1)[-1].split("/", 1)[0]
        return f"openai ({self.model} @ {host})"

    def cost(self, u: Usage) -> float | None:
        if self.price_in is None or self.price_out is None:
            return None
        return (u.input_tokens * self.price_in + u.cached_input_tokens * self.price_in * 0.5
                + u.output_tokens * self.price_out) / 1_000_000

    async def generate(self, system: str, prompt: str, schema: dict | None = None, max_tokens: int = 4000) -> Result:
        sdk = self._sdk
        kwargs = {}
        user = prompt
        if schema is not None:
            if self.json_mode == "schema":
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "answer", "schema": schema, "strict": True},
                }
            elif self.json_mode == "object":
                kwargs["response_format"] = {"type": "json_object"}
                user = prompt + schema_hint(schema)
            else:
                user = prompt + schema_hint(schema)

        started = time.monotonic()
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_completion_tokens=max_tokens,
                **kwargs,
            )
        except (sdk.AuthenticationError, sdk.PermissionDeniedError, sdk.NotFoundError, sdk.BadRequestError) as e:
            raise EngineError(f"openai: {e.message}", retryable=False)
        except sdk.RateLimitError as e:
            raise EngineError(f"openai rate limit: {e.message}")
        except sdk.APIStatusError as e:
            raise EngineError(f"openai error {e.status_code}: {e.message}")
        except sdk.APIConnectionError as e:
            raise EngineError(f"openai connection error: {e}")

        if not response.choices:
            raise EngineError("openai: empty response")
        choice = response.choices[0]
        if getattr(choice.message, "refusal", None):
            raise EngineError(f"openai: the model declined: {choice.message.refusal[:200]}", retryable=False)
        if choice.finish_reason == "length":
            raise EngineError(f"openai: answer cut at max_tokens={max_tokens}")
        text = choice.message.content or ""

        usage = Usage()
        if response.usage:
            cached = 0
            details = getattr(response.usage, "prompt_tokens_details", None)
            if details is not None:
                cached = getattr(details, "cached_tokens", 0) or 0
            usage.input_tokens = (response.usage.prompt_tokens or 0) - cached
            usage.cached_input_tokens = cached
            usage.output_tokens = response.usage.completion_tokens or 0
        usage.cost_usd = self.cost(usage)
        data = parse_json(text) if schema is not None else None
        return Result(text=text, data=data, usage=usage, seconds=time.monotonic() - started)

    async def close(self) -> None:
        await self.client.close()
