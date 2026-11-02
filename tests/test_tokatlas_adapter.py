"""Offline, no-cost Tokatlas adapter contracts (never call external API)."""
import asyncio
import json

import pytest

from guardoath.gateway_prototype.service import ProviderReply
from guardoath.gateway_prototype.tokatlas import (
    ENDPOINT, MAX_RESPONSE_BYTES, TokatlasChatAdapter, TokatlasProviderError,
)


def make_adapter(reply, recorder):
    def transport(url, headers, body, timeout):
        recorder.append((url, headers, json.loads(body), timeout))
        return json.dumps(reply).encode()
    return TokatlasChatAdapter(api_key="test-secret-without-network", transport=transport)


def sample_reply(content="safe reply", *, wrapped=False):
    result = {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    }
    return {"data": result} if wrapped else result


@pytest.mark.parametrize("wrapped", [False, True])
def test_documented_chat_protocol_and_measured_usage(wrapped):
    calls = []
    provider = make_adapter(sample_reply(wrapped=wrapped), calls)
    reply = asyncio.run(provider.generate(message="Hello", model="gpt-5"))
    assert isinstance(reply, ProviderReply)
    assert reply.text == "safe reply"
    assert (reply.input_tokens, reply.output_tokens) == (7, 3)
    assert len(calls) == 1
    url, headers, payload, timeout = calls[0]
    assert url == ENDPOINT
    assert url.startswith("https://api.tokatlas.com/")
    assert headers["Authorization"] == "Bearer test-secret-without-network"
    assert payload == {"model": "gpt-5", "messages": [{"role": "user", "content": "Hello"}], "stream": False}
    assert 0 < timeout <= 120


def test_tool_calls_are_never_executed_and_marked_for_gateway_withholding():
    calls = []
    reply = sample_reply()
    reply["choices"][0]["message"]["tool_calls"] = [{"id": "sensitive-123", "type": "function",
        "function": {"name": "dangerous_action", "arguments": "{}"}}]
    result = asyncio.run(make_adapter(reply, calls).generate(message="Hi", model="gpt-5"))
    assert result.tool_calls == ("proposed_tool",)
    assert "sensitive-123" not in str(result.model_dump())


@pytest.mark.parametrize("reply", [
    {}, {"choices": []},
    {"choices": [{"message": {"content": ""}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
    {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": True, "completion_tokens": 1}},
    {"choices": [{"message": {"content": "ok"}}], "usage": None},
    {"choices": [{"message": {"content": ["unvalidated"]}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
])
def test_malformed_responses_fail_closed_without_echo(reply):
    adapter = make_adapter(reply, [])
    with pytest.raises(TokatlasProviderError) as error:
        asyncio.run(adapter.generate(message="SECRET-PROMPT", model="gpt-5"))
    assert "SECRET-PROMPT" not in str(error.value)


def test_response_size_limit_and_no_body_leakage():
    def transport(*args):
        return b"x" * (MAX_RESPONSE_BYTES + 1)
    adapter = TokatlasChatAdapter(api_key="test-secret-without-network", transport=transport)
    with pytest.raises(TokatlasProviderError):
        asyncio.run(adapter.generate(message="Hi", model="gpt-5"))


@pytest.mark.parametrize("message,model", [
    ("", "gpt-5"), ("Hi", "../bad"), ("Hi", "bad/model"), ("Hi", ""),
])
def test_invalid_request_never_reaches_provider(message, model):
    calls = []
    adapter = make_adapter(sample_reply(), calls)
    with pytest.raises(TokatlasProviderError):
        asyncio.run(adapter.generate(message=message, model=model))
    assert calls == []


def test_transport_errors_are_sanitized():
    def broken(*args):
        raise RuntimeError("secret-key-and-private-response")
    adapter = TokatlasChatAdapter(api_key="test-secret-without-network", transport=broken)
    with pytest.raises(TokatlasProviderError) as error:
        asyncio.run(adapter.generate(message="Hi", model="gpt-5"))
    assert "secret-key" not in str(error.value)


def test_key_and_timeout_validate_before_use():
    with pytest.raises(ValueError):
        TokatlasChatAdapter(api_key="bad\nkey-unsafe")
    with pytest.raises(ValueError):
        TokatlasChatAdapter(api_key="test-secret-without-network", timeout_seconds=0)
