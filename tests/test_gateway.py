"""spiderbot/ai/gateway.py - the single fault boundary (invariant 2: never raises)."""

from __future__ import annotations

import asyncio

from conftest import make_cfg

from spiderbot.ai.gateway import Gateway


class _Block:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _Usage:
    input_tokens = 10
    output_tokens = 5


class _Response:
    model = "claude-opus-5"
    usage = _Usage()

    def __init__(self, text: str) -> None:
        self.content = [_Block(text)]


class _FakeMessages:
    def __init__(self, response=None, exc=None, delay: float = 0.0) -> None:
        self._response = response
        self._exc = exc
        self._delay = delay
        self.kwargs = None
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._exc is not None:
            raise self._exc
        return self._response


class _FakeClient:
    def __init__(self, messages: _FakeMessages) -> None:
        self.messages = messages


def _enabled_gateway(messages: _FakeMessages) -> Gateway:
    gw = Gateway(make_cfg(ai_enabled=True, anthropic_api_key="NOT-A-REAL-KEY-x"))
    gw._client = _FakeClient(messages)
    return gw


def test_disabled_without_key():
    gw = Gateway(make_cfg(ai_enabled=True, anthropic_api_key=None))
    assert gw.enabled is False
    result = asyncio.run(gw.reply("hi", mode="mention"))
    assert (result.text, result.reason) == (None, "disabled")


def test_disabled_by_flag_even_with_key():
    # Invariant 9: AI_ENABLED=false keeps everything deterministic.
    gw = Gateway(make_cfg(ai_enabled=False, anthropic_api_key="NOT-A-REAL-KEY-x"))
    assert gw.enabled is False


def test_empty_payload_passes_without_api_call():
    messages = _FakeMessages(_Response("never"))
    gw = _enabled_gateway(messages)
    result = asyncio.run(gw.reply("   ", mode="mention"))
    assert result.reason == "pass"
    assert messages.calls == 0


def test_ok_reply_carries_text_model_and_usage():
    messages = _FakeMessages(_Response("Hello web-friend"))
    gw = _enabled_gateway(messages)
    result = asyncio.run(gw.reply("payload", mode="mention"))
    assert result.text == "Hello web-friend"
    assert result.reason == "ok"
    assert result.model == "claude-opus-5"
    assert (result.input_tokens, result.output_tokens) == (10, 5)


def test_reply_truncated_below_discord_limit():
    messages = _FakeMessages(_Response("a" * 3000))
    gw = _enabled_gateway(messages)
    result = asyncio.run(gw.reply("payload", mode="mention"))
    assert len(result.text) == 1990


def test_pass_sentinel_and_empty_text_become_pass():
    for raw in ("PASS", "  PASS  ", ""):
        messages = _FakeMessages(_Response(raw))
        result = asyncio.run(_enabled_gateway(messages).reply("payload", mode="initiative"))
        assert (result.text, result.reason) == (None, "pass"), raw


def test_timeout_degrades():
    messages = _FakeMessages(_Response("late"), delay=0.2)
    gw = _enabled_gateway(messages)
    result = asyncio.run(gw.reply("payload", mode="mention", timeout_s=0.02))
    assert (result.text, result.reason) == (None, "timeout")


def test_unexpected_exception_degrades_not_raises():
    messages = _FakeMessages(exc=ValueError("boom"))
    gw = _enabled_gateway(messages)
    result = asyncio.run(gw.reply("payload", mode="mention"))
    assert (result.text, result.reason) == (None, "error")


def test_request_shape_model_effort_and_instruction():
    messages = _FakeMessages(_Response("ok"))
    gw = _enabled_gateway(messages)
    asyncio.run(gw.reply("payload", mode="initiative"))
    kwargs = messages.kwargs
    assert kwargs["model"] == "claude-opus-5"
    assert kwargs["output_config"] == {"effort": "low"}
    assert kwargs["max_tokens"] == 1000
    # System prompt is the stable cache-marked block, payload a single user turn.
    assert kwargs["system"] is gw._system
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    [turn] = kwargs["messages"]
    assert turn["role"] == "user"
    assert "PASS" in turn["content"]  # initiative instruction rides along

    asyncio.run(gw.reply("payload", mode="mention"))
    assert "directly mentioned" in messages.kwargs["messages"][0]["content"]
