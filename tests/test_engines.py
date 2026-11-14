import json
import shutil
from types import SimpleNamespace

import pytest

from crowdpanel.engines import EngineError, get_engine
from crowdpanel.engines.base import parse_json
from crowdpanel.prompts import ACTION_SCHEMA


def test_parse_json_tolerates_fences_and_noise():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure! {"a": 2} hope it helps') == {"a": 2}
    with pytest.raises(EngineError):
        parse_json("no json here")


def test_unknown_engine():
    with pytest.raises(EngineError):
        get_engine("nope")


def test_action_schema_is_strict_everywhere():
    def walk(node):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                walk(child)
        if node.get("type") == "array":
            walk(node["items"])

    walk(ACTION_SCHEMA)


@pytest.fixture
def fake_binary(tmp_path, monkeypatch):
    def make(name, script):
        path = tmp_path / name
        path.write_text("#!/usr/bin/env python3\n" + script)
        path.chmod(0o755)
        monkeypatch.setenv("PATH", f"{tmp_path}:{__import__('os').environ['PATH']}")
        return path
    return make


async def test_claude_code_engine_parses_structured_output(fake_binary, monkeypatch):
    fake_binary("claude", (
        "import json, sys, os\n"
        "args = sys.argv[1:]\n"
        "assert '--json-schema' in args and args[args.index('--tools') + 1] == ''\n"
        "assert 'ANTHROPIC_API_KEY' not in os.environ\n"
        "sys.stdin.read()\n"
        "print(json.dumps({'is_error': False, 'result': '', 'structured_output': {'ok': True},\n"
        "  'usage': {'input_tokens': 3, 'cache_read_input_tokens': 100, 'cache_creation_input_tokens': 7, 'output_tokens': 9}}))\n"
    ))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-be-dropped")
    engine = get_engine("claude-code")
    res = await engine.generate("sys", "hi", {"type": "object"})
    await engine.close()
    assert res.data == {"ok": True}
    assert res.usage.cached_input_tokens == 100 and res.usage.output_tokens == 9


async def test_claude_code_login_error_is_not_retried(fake_binary):
    fake_binary("claude", (
        "import json, sys\nsys.stdin.read()\n"
        "print(json.dumps({'is_error': True, 'result': 'Not logged in. Please run /login'}))\n"
    ))
    engine = get_engine("claude-code")
    with pytest.raises(EngineError) as err:
        await engine.generate("sys", "hi", None)
    await engine.close()
    assert err.value.retryable is False


async def test_codex_engine_reads_last_message_and_usage(fake_binary):
    fake_binary("codex", (
        "import json, sys\n"
        "args = sys.argv[1:]\n"
        "assert args[0] == 'exec' and '--output-schema' in args and args[-1] == '-'\n"
        "prompt = sys.stdin.read()\n"
        "assert '<instructions>' in prompt\n"
        "out = args[args.index('-o') + 1]\n"
        "open(out, 'w').write(json.dumps({'ok': True}))\n"
        "print(json.dumps({'type': 'thread.started', 'thread_id': '1'}))\n"
        "print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 50, 'cached_input_tokens': 20,\n"
        "  'output_tokens': 5, 'reasoning_output_tokens': 4}}))\n"
    ))
    engine = get_engine("codex", model="some-model")
    res = await engine.generate("sys", "hi", {"type": "object"})
    await engine.close()
    assert res.data == {"ok": True}
    assert res.usage.input_tokens == 50 and res.usage.output_tokens == 9


async def test_missing_cli_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(EngineError) as err:
        get_engine("codex")
    assert "not found" in str(err.value)


class _FakeAnthropicMessages:
    def __init__(self):
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=json.dumps({"ok": True}))],
            usage=SimpleNamespace(input_tokens=10, output_tokens=20, cache_read_input_tokens=1000,
                                  cache_creation_input_tokens=0),
        )


async def test_anthropic_engine_request_shape(monkeypatch):
    pytest.importorskip("anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    engine = get_engine("anthropic")
    fake = _FakeAnthropicMessages()
    engine.client = SimpleNamespace(messages=fake, close=lambda: None)
    res = await engine.generate("system text", "hello", {"type": "object", "properties": {}, "required": [],
                                                          "additionalProperties": False})
    assert res.data == {"ok": True}
    assert fake.kwargs["model"] == "claude-haiku-5-5"
    assert fake.kwargs["output_config"]["format"]["type"] == "json_schema"
    assert fake.kwargs["output_config"]["effort"] == "low"
    assert fake.kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "temperature" not in fake.kwargs
    assert res.usage.cost_usd == pytest.approx((10 * 0.10 + 1000 * 0.01 + 20 * 0.50) / 1e6)


class _FakeOpenAICompletions:
    def __init__(self):
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{"ok": true}', refusal=None))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=7,
                                  prompt_tokens_details=SimpleNamespace(cached_tokens=40)),
        )


async def test_openai_engine_json_schema_and_prompt_modes(monkeypatch):
    pytest.importorskip("openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}

    engine = get_engine("openai", model="m", base_url="http://localhost:9/v1")
    fake = _FakeOpenAICompletions()
    engine.client.chat = SimpleNamespace(completions=fake)
    res = await engine.generate("sys", "hi", schema)
    assert res.data == {"ok": True}
    assert fake.kwargs["response_format"]["json_schema"]["strict"] is True
    assert res.usage.input_tokens == 60 and res.usage.cached_input_tokens == 40

    engine = get_engine("openai", model="m", base_url="http://localhost:9/v1", json_mode="prompt")
    fake = _FakeOpenAICompletions()
    engine.client.chat = SimpleNamespace(completions=fake)
    await engine.generate("sys", "hi", schema)
    assert "response_format" not in fake.kwargs
    assert "JSON Schema" in fake.kwargs["messages"][1]["content"]


def test_openai_engine_needs_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    with pytest.raises(EngineError):
        get_engine("openai")
