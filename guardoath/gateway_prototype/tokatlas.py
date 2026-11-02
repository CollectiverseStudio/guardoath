"""Tokatlas Chat Completions adapter for the isolated GuardOath gateway.

Tokatlas API contract: https://www.tokatlas.com/en/docs-content/chat/overview
This module is not registered in the hosted application. No paid calls in tests.
"""
from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from guardoath.gateway_prototype.service import ProviderReply

ENDPOINT = "https://api.tokatlas.com/v1/chat/completions"
MAX_RESPONSE_BYTES = 262_144
MAX_REQUEST_CHARS = 32_000


class TokatlasProviderError(RuntimeError):
    """Provider failure; never include credentials, prompts or response bodies."""


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError('Ambiguous provider JSON object')
        result[name] = value
    return result


def _reject_constant(value):
    raise ValueError('Invalid provider JSON constant')


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        # urllib otherwise preserves Authorization on cross-origin/downgrade
        # redirects. This fixed-endpoint adapter accepts no redirect at all.
        raise TokatlasProviderError('Tokatlas redirects are prohibited')


def _http_post(url: str, headers: dict[str, str], body: bytes, timeout: float) -> bytes:
    if url != ENDPOINT:
        raise TokatlasProviderError('Tokatlas endpoint is not permitted')
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        opener = urllib.request.build_opener(_RejectRedirects())
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise TokatlasProviderError("Tokatlas returned an unsuccessful status")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TokatlasProviderError("Tokatlas request unavailable") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise TokatlasProviderError("Tokatlas response exceeds limit")
    return raw


@dataclass(frozen=True)
class TokatlasChatAdapter:
    """Use only with trusted tenant-admin provider bindings.

    Transport injection exists exclusively for deterministic offline tests.
    Host must enforce per-request budgets, egress controls and isolation.
    """
    api_key: str = field(repr=False)
    timeout_seconds: float = 10.0
    model_limits: tuple[tuple[str, int], ...] = ()
    transport: Callable[[str, dict[str, str], bytes, float], bytes] = field(default=_http_post, repr=False)

    def __post_init__(self):
        if not isinstance(self.api_key, str) or not (8 <= len(self.api_key) <= 512) or any(ord(c) < 33 or ord(c) > 126 for c in self.api_key):
            raise ValueError("Invalid trusted Tokatlas API key")
        if not isinstance(self.timeout_seconds, (int, float)) or isinstance(self.timeout_seconds, bool) or not 0 < self.timeout_seconds <= 120:
            raise ValueError("Invalid Tokatlas timeout")
        if not callable(self.transport):
            raise ValueError("Invalid trusted transport")
        if (type(self.model_limits) is not tuple or len(self.model_limits) > 32
                or any(type(row) is not tuple or len(row) != 2
                       or not isinstance(row[0], str) or not row[0] or len(row[0]) > 80
                       or type(row[1]) is not int or not 1 <= row[1] <= 4096
                       for row in self.model_limits)):
            raise ValueError('Invalid trusted model generation limits')
        if len(dict(self.model_limits)) != len(self.model_limits):
            raise ValueError('Duplicate trusted model generation limits')

    async def generate(self, *, message: str, model: str) -> ProviderReply:
        if not isinstance(message, str) or not 1 <= len(message) <= MAX_REQUEST_CHARS:
            raise TokatlasProviderError("Invalid validated input")
        if not isinstance(model, str) or not 1 <= len(model) <= 80 or not all(c.isascii() and (c.isalnum() or c in "._-") for c in model):
            raise TokatlasProviderError("Invalid configured model")
        limit = dict(self.model_limits).get(model)
        if limit is None:
            raise TokatlasProviderError('Model generation limits are not approved')
        if self.transport is _http_post:
            # The committed provider contract has no verified per-model upstream
            # generation/spend cap. Do not silently issue an uncapped paid call.
            # Offline injected transports remain available for integration tests.
            raise TokatlasProviderError("Live Tokatlas requests are disabled pending verified generation budgets")
        # Do not permit direct streaming: GuardOath must validate the complete output.
        payload = {"model": model, "messages": [{"role": "user", "content": message}],
                   "stream": False, "max_completion_tokens": limit, "n": 1, "store": False}
        body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        headers = {"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"}
        try:
            raw = await asyncio.to_thread(self.transport, ENDPOINT, headers, body, float(self.timeout_seconds))
            if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
                raise TokatlasProviderError("Tokatlas response exceeds limit")
            result = json.loads(raw, object_pairs_hook=_unique_object,
                                parse_constant=_reject_constant)
            if not isinstance(result, dict):
                raise ValueError("not object")
            if 'code' in result and (type(result['code']) is not int or result['code'] != 200):
                raise ValueError('unsuccessful application status')
            # Tokatlas documents both direct and data-wrapped completion envelopes.
            result = result.get("data", result)
            if not isinstance(result, dict):
                raise ValueError("invalid envelope")
            choices = result.get("choices")
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                raise ValueError("missing choices")
            choice = choices[0]
            reason = choice.get('finish_reason')
            if reason not in ('stop', 'tool_calls'):
                raise ValueError('incomplete or unsupported completion')
            reply = choice.get("message")
            if not isinstance(reply, dict):
                raise ValueError("missing message")
            content = reply.get("content")
            calls = reply.get("tool_calls")
            if calls is not None and not isinstance(calls, list):
                raise ValueError("invalid tool calls")
            if calls or reason == 'tool_calls':
                # Tool execution is prohibited by the current GuardOath gateway;
                # return sanitized opaque markers so the gateway withholds output.
                safe_calls = tuple("proposed_tool" for _ in (calls or [None])[:20])
                if calls and len(calls) > 20:
                    raise ValueError("excessive tool proposals")
            else:
                safe_calls = ()
            if content is None and safe_calls:
                # ProviderReply requires text; placeholder cannot leak output.
                content = "[tool invocation withheld]"
            if not isinstance(content, str) or not 1 <= len(content) <= MAX_REQUEST_CHARS:
                raise ValueError("invalid text")
            usage = result.get("usage")
            if not isinstance(usage, dict):
                raise ValueError("missing measured usage")
            input_tokens, output_tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")
            if any(type(v) is not int or not 0 <= v <= 1_000_000 for v in (input_tokens, output_tokens)):
                raise ValueError("invalid measured usage")
            if output_tokens > limit:
                raise ValueError('generation exceeded approved limit')
            return ProviderReply(text=content, input_tokens=input_tokens, output_tokens=output_tokens, tool_calls=safe_calls)
        except asyncio.CancelledError:
            raise
        except Exception:
            # No provider response text, keys or prompt content in caller-visible errors.
            raise TokatlasProviderError("Tokatlas response unavailable or invalid") from None
