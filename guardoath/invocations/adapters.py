"""Small, synchronous provider adapters for approved, buffered text invocations.

The caller owns tenant admission, durable dispatch intent, a bounded worker pool,
the overall deadline, and all Engine checks.  Constructors are trusted server
configuration; no credentials, endpoints, tools, or provider kwargs come from an
invocation request.  A socket timeout cannot cancel work already sent upstream.

Converse is buffered by Botocore before this module receives its result.  Its
limits below bound accepted response structure, not Botocore's wire allocation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import http.client
import json
import math
import re
import socket
import ssl
import time
from typing import Any, Callable, Literal, Mapping


MAX_INPUT_BYTES = 16_384
MAX_OUTPUT_BYTES = 32_000
MAX_OUTPUT_TOKENS = 4_096
MAX_RESPONSE_BYTES = 262_144
MAX_TIMEOUT_SECONDS = 10.0
OPENAI_ENDPOINT = "https://api.openai.com/v1/responses"

Outcome = Literal["completed", "withheld", "rejected", "unknown"]
ReasonCode = Literal[
    "completed", "request_invalid", "model_not_allowed", "configuration_unavailable",
    "unsupported_output", "incomplete_output", "provider_filtered", "provider_refused",
    "provider_policy_mismatch", "provider_rejected", "provider_authentication_failed",
    "provider_permission_denied", "provider_rate_limited", "provider_model_unavailable",
    "provider_timeout", "provider_unavailable", "provider_response_invalid",
    "provider_response_too_large",
]
_REASONS: dict[str, frozenset[str]] = {
    "completed": frozenset({"completed"}),
    "withheld": frozenset({"unsupported_output", "incomplete_output", "provider_filtered",
                           "provider_refused", "provider_policy_mismatch"}),
    "rejected": frozenset({"request_invalid", "model_not_allowed", "configuration_unavailable",
                           "provider_rejected", "provider_authentication_failed",
                           "provider_permission_denied", "provider_rate_limited",
                           "provider_model_unavailable"}),
    "unknown": frozenset({"provider_timeout", "provider_unavailable", "provider_response_invalid",
                          "provider_response_too_large"}),
}
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z", re.ASCII)
_PROVIDER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}\Z", re.ASCII)
_OPENAI_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z", re.ASCII)
_REGION = re.compile(r"(?:us|eu|ap|sa|ca|me|af|il|mx|cn)(?:-[a-z]+){1,3}-[1-9][0-9]?\Z", re.ASCII)
_BEDROCK_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\.[a-z0-9][a-z0-9.-]{0,184}(?::[0-9]{1,8})?\Z", re.ASCII)
_INFERENCE_PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z", re.ASCII)
_SERVICE_TIERS = frozenset({"default", "priority", "flex", "reserved", "scale", "auto", "fast", "ultrafast"})


def _integer(value: Any, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _timeout(value: Any) -> bool:
    return type(value) in (int, float) and 0 < value <= MAX_TIMEOUT_SECONDS and math.isfinite(value)


def _text(value: Any, limit: int, *, empty: bool = False) -> bool:
    if type(value) is not str or len(value) > limit or (not empty and not value):
        return False
    try:
        return len(value.encode("utf-8")) <= limit
    except UnicodeError:
        return False


def _identifier(value: Any) -> bool:
    return type(value) is str and _PROVIDER_ID.fullmatch(value) is not None


class AdapterConfigurationError(ValueError):
    """A fixed, non-sensitive configuration error."""

    def __init__(self) -> None:
        super().__init__("Invalid provider adapter configuration")


@dataclass(frozen=True, slots=True)
class ApprovedModelCall:
    text: str = field(repr=False)
    model_id: str
    max_output_tokens: int
    timeout_seconds: float

    def __post_init__(self) -> None:
        if (not _text(self.text, MAX_INPUT_BYTES)
                or not _text(self.model_id, 2_048)
                or not _integer(self.max_output_tokens, 1, MAX_OUTPUT_TOKENS)
                or not _timeout(self.timeout_seconds)):
            raise ValueError("Invalid approved model call")


@dataclass(frozen=True, slots=True)
class ProviderResult:
    text: str | None = field(default=None, repr=False)
    input_tokens: int | None = None
    output_tokens: int | None = None
    provider_request_id: str | None = None
    response_id: str | None = None
    observed_model: str | None = None
    observed_service_tier: str | None = None
    outcome: Outcome = "unknown"
    reason_code: ReasonCode = "provider_response_invalid"

    def __post_init__(self) -> None:
        valid = (type(self.outcome) is str and self.outcome in _REASONS
                 and type(self.reason_code) is str
                 and self.reason_code in _REASONS.get(self.outcome, ()))
        valid = valid and ((self.input_tokens is None) == (self.output_tokens is None))
        for value in (self.input_tokens, self.output_tokens):
            valid = valid and (value is None or _integer(value, 0, 1_000_000))
        for value in (self.provider_request_id, self.response_id, self.observed_model):
            valid = valid and (value is None or _identifier(value))
        valid = valid and (self.observed_service_tier is None or
                           (type(self.observed_service_tier) is str
                            and self.observed_service_tier in _SERVICE_TIERS))
        if self.outcome == "completed":
            valid = valid and _text(self.text, MAX_OUTPUT_BYTES) and self.input_tokens is not None
        else:
            valid = valid and self.text is None
        if not valid:
            raise ValueError("Invalid normalized provider result")


@dataclass(frozen=True, slots=True)
class AwsCredentials:
    """Explicit temporary credentials resolved for this approved connection."""

    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    session_token: str = field(repr=False)

    def __post_init__(self) -> None:
        if not (type(self.access_key_id) is str
                and re.fullmatch(r"[A-Z0-9]{16,128}", self.access_key_id, re.ASCII)
                and _secret(self.secret_access_key, 16, 256)
                and _secret(self.session_token, 16, 8_192)):
            raise AdapterConfigurationError()


def _secret(value: Any, minimum: int, maximum: int) -> bool:
    return (type(value) is str and minimum <= len(value) <= maximum
            and all(33 <= ord(char) <= 126 for char in value))


def _binding(tenant_id: str, connection_id: str, connection_version: str,
             allowed_models: tuple[str, ...], max_output_tokens: int) -> None:
    if (any(type(value) is not str or _IDENTITY.fullmatch(value) is None
            for value in (tenant_id, connection_id, connection_version))
            or type(allowed_models) is not tuple or not 1 <= len(allowed_models) <= 32
            or any(type(value) is not str for value in allowed_models)
            or len(set(allowed_models)) != len(allowed_models)
            or not _integer(max_output_tokens, 1, MAX_OUTPUT_TOKENS)):
        raise AdapterConfigurationError()


def _preflight(call: ApprovedModelCall, allowed_models: tuple[str, ...],
               max_output_tokens: int) -> ProviderResult | None:
    if type(call) is not ApprovedModelCall:
        return ProviderResult(outcome="rejected", reason_code="request_invalid")
    # Revalidate even frozen objects: trusted Python code can bypass __init__.
    try:
        call.__post_init__()
    except Exception:
        return ProviderResult(outcome="rejected", reason_code="request_invalid")
    if call.model_id not in allowed_models:
        return ProviderResult(outcome="rejected", reason_code="model_not_allowed")
    if call.max_output_tokens > max_output_tokens:
        return ProviderResult(outcome="rejected", reason_code="request_invalid")
    return None


class _ResponseInvalid(Exception):
    pass


class _ResponseTooLarge(_ResponseInvalid):
    pass


def _bounded_tree(value: Any) -> None:
    """Bound accepted JSON structure without serializing an unbounded object."""
    pending = [(value, 0)]
    nodes = 0
    size = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > 8_192 or depth > 16:
            raise _ResponseTooLarge()
        if type(item) is dict:
            if len(item) > 128:
                raise _ResponseTooLarge()
            for key, child in item.items():
                if type(key) is not str or not _text(key, 256, empty=True):
                    raise _ResponseInvalid()
                size += len(key.encode("utf-8")) + 4
                pending.append((child, depth + 1))
        elif type(item) is list:
            if len(item) > 256:
                raise _ResponseTooLarge()
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            if not _text(item, MAX_RESPONSE_BYTES, empty=True):
                raise _ResponseTooLarge()
            size += len(item.encode("utf-8")) + 2
        elif type(item) is int:
            if item.bit_length() > 64:
                raise _ResponseInvalid()
            size += 24
        elif type(item) is float:
            if not math.isfinite(item):
                raise _ResponseInvalid()
            size += 24
        elif item is None or type(item) is bool:
            size += 5
        else:
            raise _ResponseInvalid()
        if size > MAX_RESPONSE_BYTES:
            raise _ResponseTooLarge()


def _usage(value: Any, input_key: str, output_key: str, total_key: str,
           output_ceiling: int) -> dict[str, int]:
    if type(value) is not dict:
        raise _ResponseInvalid()
    input_tokens, output_tokens, total = (value.get(input_key), value.get(output_key), value.get(total_key))
    if (not _integer(input_tokens, 0, 1_000_000)
            or not _integer(output_tokens, 0, output_ceiling)
            or not _integer(total, 0, 1_000_000 + MAX_OUTPUT_TOKENS)
            or total != input_tokens + output_tokens):
        raise _ResponseInvalid()
    return {"input_tokens": input_tokens, "output_tokens": output_tokens}


def _optional_identifier(value: Any) -> str | None:
    if value is not None and not _identifier(value):
        raise _ResponseInvalid()
    return value


def _tier(value: Any) -> str | None:
    if value is not None and (type(value) is not str or value not in _SERVICE_TIERS):
        raise _ResponseInvalid()
    return value


def _join_text(parts: list[str]) -> str:
    text = "\n".join(parts)
    if not _text(text, MAX_OUTPUT_BYTES):
        raise _ResponseTooLarge()
    return text


def _bedrock_endpoint(region: str) -> str:
    suffix = "amazonaws.com.cn" if region.startswith("cn-") else "amazonaws.com"
    return f"https://bedrock-runtime.{region}.{suffix}"


def _bedrock_model(model_id: str, region: str) -> bool:
    if not model_id.startswith("arn:"):
        return _BEDROCK_NAME.fullmatch(model_id) is not None
    parts = model_id.split(":", 5)
    partition = "aws-cn" if region.startswith("cn-") else "aws-us-gov" if region.startswith("us-gov-") else "aws"
    if len(parts) != 6 or parts[:4] != ["arn", partition, "bedrock", region]:
        return False
    account, resource = parts[4:]
    if resource.startswith("foundation-model/"):
        return account == "" and _BEDROCK_NAME.fullmatch(resource[len("foundation-model/"):]) is not None
    if resource.startswith("inference-profile/"):
        return (re.fullmatch(r"[0-9]{12}", account, re.ASCII) is not None
                and _INFERENCE_PROFILE.fullmatch(resource[len("inference-profile/"):]) is not None)
    return False


def validate_bedrock_targets(region: str, allowed_models: tuple[str, ...]) -> None:
    """Validate trusted target configuration without resolving any credentials."""
    if (type(region) is not str or _REGION.fullmatch(region) is None
            or type(allowed_models) is not tuple or not 1 <= len(allowed_models) <= 32
            or any(type(model) is not str for model in allowed_models)
            or len(set(allowed_models)) != len(allowed_models)
            or any(not _bedrock_model(model, region) for model in allowed_models)):
        raise AdapterConfigurationError()


def _bedrock_client(credentials: AwsCredentials, region: str, timeout_seconds: float) -> Any:
    # Imports stay optional; construction never falls back to ambient credentials.
    import boto3
    from botocore.config import Config

    connect_timeout = min(1.0, timeout_seconds / 2)
    session = boto3.session.Session(
        aws_access_key_id=credentials.access_key_id,
        aws_secret_access_key=credentials.secret_access_key,
        aws_session_token=credentials.session_token,
        region_name=region,
    )
    return session.client(
        "bedrock-runtime", region_name=region, endpoint_url=_bedrock_endpoint(region),
        verify=True, config=Config(
            connect_timeout=connect_timeout, read_timeout=timeout_seconds - connect_timeout,
            retries={"mode": "standard", "total_max_attempts": 1}, max_pool_connections=2,
            proxies={}, user_agent_extra="GuardOath/approved-text-invocation",
        ),
    )


def _configured_client(client: Any, region: str, timeout_seconds: float | None = None) -> bool:
    try:
        config = client.meta.config
        retries = config.retries
        valid = (callable(client.converse)
                 and client.meta.service_model.service_name == "bedrock-runtime"
                 and client.meta.endpoint_url == _bedrock_endpoint(region)
                 and config.region_name == region
                 and type(retries) is dict and type(retries.get("total_max_attempts")) is int
                 and retries.get("total_max_attempts") == 1
                 and retries.get("mode") == "standard"
                 and _timeout(config.connect_timeout) and _timeout(config.read_timeout)
                 and _integer(config.max_pool_connections, 1, 4))
        if timeout_seconds is not None:
            valid = valid and config.connect_timeout + config.read_timeout <= timeout_seconds + 1e-9
        return bool(valid)
    except Exception:
        return False


def _close_client(client: Any) -> None:
    try:
        client.close()
    except Exception:
        pass


@dataclass(frozen=True, slots=True, kw_only=True)
class BedrockConverseAdapter:
    tenant_id: str
    connection_id: str
    connection_version: str
    allowed_models: tuple[str, ...]
    region: str
    credentials: AwsCredentials | None = field(default=None, repr=False)
    client: Any = field(default=None, repr=False, compare=False)
    max_output_tokens: int = MAX_OUTPUT_TOKENS

    def __post_init__(self) -> None:
        _binding(self.tenant_id, self.connection_id, self.connection_version,
                 self.allowed_models, self.max_output_tokens)
        validate_bedrock_targets(self.region, self.allowed_models)
        if ((self.credentials is None) == (self.client is None)
                or (self.credentials is not None and type(self.credentials) is not AwsCredentials)
                or (self.client is not None and not _configured_client(self.client, self.region))):
            raise AdapterConfigurationError()

    def invoke(self, call: ApprovedModelCall) -> ProviderResult:
        started = time.monotonic()
        refusal = _preflight(call, self.allowed_models, self.max_output_tokens)
        if refusal is not None:
            return refusal
        deadline = started + call.timeout_seconds
        client = self.client
        owned = client is None
        try:
            if owned:
                # Leave local setup headroom. A single client is constructed;
                # exhausted setup never triggers a rebuild or paid dispatch.
                client = _bedrock_client(self.credentials, self.region, _remaining(deadline) * 0.75)
            if not _configured_client(client, self.region, _remaining(deadline)):
                if owned:
                    _close_client(client)
                return ProviderResult(outcome="rejected", reason_code="configuration_unavailable")
            _remaining(deadline)
        except Exception:
            if owned and client is not None:
                _close_client(client)
            return ProviderResult(outcome="rejected", reason_code="configuration_unavailable")
        try:
            try:
                response = client.converse(
                    modelId=call.model_id,
                    messages=[{"role": "user", "content": [{"text": call.text}]}],
                    inferenceConfig={"maxTokens": call.max_output_tokens},
                    serviceTier={"type": "default"},
                )
            except Exception as error:
                return _bedrock_failure(error)
            try:
                return _parse_bedrock(response, call)
            except _ResponseTooLarge:
                return ProviderResult(outcome="unknown", reason_code="provider_response_too_large")
            except Exception:
                return ProviderResult(outcome="unknown", reason_code="provider_response_invalid")
        finally:
            if owned:
                _close_client(client)


def _bedrock_failure(error: Exception) -> ProviderResult:
    request_id = None
    try:
        response = error.response
        if type(response) is not dict:
            raise _ResponseInvalid()
        request_id = _optional_identifier(response.get("ResponseMetadata", {}).get("RequestId"))
        code = response.get("Error", {}).get("Code")
        mapping = {
            "AccessDeniedException": "provider_permission_denied",
            "UnrecognizedClientException": "provider_authentication_failed",
            "ExpiredTokenException": "provider_authentication_failed",
            "InvalidSignatureException": "provider_authentication_failed",
            "ThrottlingException": "provider_rate_limited",
            "TooManyRequestsException": "provider_rate_limited",
            "ResourceNotFoundException": "provider_model_unavailable",
            "ModelNotReadyException": "provider_model_unavailable",
            "ValidationException": "provider_rejected",
        }
        if type(code) is str and code in mapping:
            return ProviderResult(outcome="rejected", reason_code=mapping[code], provider_request_id=request_id)
        if code == "ModelTimeoutException":
            return ProviderResult(outcome="unknown", reason_code="provider_timeout", provider_request_id=request_id)
    except Exception:
        pass
    name = type(error).__name__
    timeout = isinstance(error, (TimeoutError, socket.timeout)) or name in {"ReadTimeoutError", "ConnectTimeoutError"}
    return ProviderResult(outcome="unknown", reason_code="provider_timeout" if timeout else "provider_unavailable",
                          provider_request_id=request_id)


def _parse_bedrock(response: Any, call: ApprovedModelCall) -> ProviderResult:
    _bounded_tree(response)
    if type(response) is not dict:
        raise _ResponseInvalid()
    metadata = response.get("ResponseMetadata")
    if (type(metadata) is not dict or metadata.get("HTTPStatusCode") != 200
            or type(metadata.get("HTTPStatusCode")) is not int
            or not _integer(metadata.get("RetryAttempts", 0), 0, 0)):
        raise _ResponseInvalid()
    request_id = _optional_identifier(metadata.get("RequestId"))
    if request_id is None:
        raise _ResponseInvalid()
    tier = response.get("serviceTier")
    if tier is not None and (type(tier) is not dict or set(tier) != {"type"}):
        raise _ResponseInvalid()
    common = {**_usage(response.get("usage"), "inputTokens", "outputTokens", "totalTokens", call.max_output_tokens),
              "provider_request_id": request_id,
              "observed_service_tier": _tier(tier.get("type") if tier is not None else None)}
    if common["observed_service_tier"] not in (None, "default"):
        return ProviderResult(outcome="withheld", reason_code="provider_policy_mismatch", **common)
    stop = response.get("stopReason")
    if stop in {"guardrail_intervened", "content_filtered"}:
        return ProviderResult(outcome="withheld", reason_code="provider_filtered", **common)
    if stop in {"max_tokens", "stop_sequence", "model_context_window_exceeded"}:
        return ProviderResult(outcome="withheld", reason_code="incomplete_output", **common)
    if stop in {"tool_use", "malformed_tool_use"}:
        return ProviderResult(outcome="withheld", reason_code="unsupported_output", **common)
    if stop != "end_turn":
        raise _ResponseInvalid()
    output = response.get("output")
    if type(output) is not dict or set(output) != {"message"}:
        raise _ResponseInvalid()
    message = output["message"]
    if type(message) is not dict or set(message) != {"role", "content"} or message.get("role") != "assistant":
        raise _ResponseInvalid()
    content = message.get("content")
    if type(content) is not list or not 1 <= len(content) <= 32:
        raise _ResponseInvalid()
    parts = []
    for block in content:
        if type(block) is not dict:
            raise _ResponseInvalid()
        if set(block) != {"text"}:
            return ProviderResult(outcome="withheld", reason_code="unsupported_output", **common)
        if not _text(block["text"], MAX_OUTPUT_BYTES):
            raise _ResponseInvalid()
        parts.append(block["text"])
    return ProviderResult(text=_join_text(parts), outcome="completed", reason_code="completed", **common)


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    body: bytes = field(repr=False)
    request_id: str | None = None

    def __post_init__(self) -> None:
        if (not _integer(self.status_code, 100, 599) or type(self.body) is not bytes
                or (self.request_id is not None and not _identifier(self.request_id))):
            raise ValueError("Invalid provider HTTP response")


OpenAITransport = Callable[[str, Mapping[str, str], bytes, float], HttpResponse]


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError()
    return remaining


def _openai_https_post(url: str, headers: Mapping[str, str], body: bytes,
                      timeout_seconds: float) -> HttpResponse:
    """One direct HTTPS request.  Redirects and ambient proxy routing are absent."""
    if url != OPENAI_ENDPOINT:
        raise _ResponseInvalid()
    deadline = time.monotonic() + timeout_seconds
    connection = http.client.HTTPSConnection(
        "api.openai.com", 443, timeout=min(1.0, timeout_seconds), context=ssl.create_default_context(),
    )
    response = None
    try:
        connection.connect()
        sock = connection.sock
        if sock is None:
            raise _ResponseInvalid()
        sock.settimeout(_remaining(deadline))
        connection.request("POST", "/v1/responses", body=body, headers=dict(headers))
        sock.settimeout(_remaining(deadline))
        response = connection.getresponse()
        request_id = _optional_identifier(response.getheader("x-request-id"))
        if response.status != 200:
            # Error bodies can contain secrets and are never read or retained.
            return HttpResponse(response.status, b"", request_id)
        if response.getheader("Content-Encoding", "identity").strip().lower() != "identity":
            raise _ResponseInvalid()
        content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise _ResponseInvalid()
        length = response.getheader("Content-Length")
        if length is not None:
            if not re.fullmatch(r"[0-9]{1,10}", length, re.ASCII):
                raise _ResponseInvalid()
            if int(length) > MAX_RESPONSE_BYTES:
                raise _ResponseTooLarge()
        chunks: list[bytes] = []
        received = 0
        while True:
            sock.settimeout(_remaining(deadline))
            chunk = response.read1(min(8_192, MAX_RESPONSE_BYTES + 1 - received))
            if not chunk:
                break
            received += len(chunk)
            if received > MAX_RESPONSE_BYTES:
                raise _ResponseTooLarge()
            chunks.append(chunk)
        _remaining(deadline)
        if length is not None and received != int(length):
            raise _ResponseInvalid()
        return HttpResponse(200, b"".join(chunks), request_id)
    finally:
        try:
            if response is not None:
                response.close()
        finally:
            connection.close()


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenAIResponsesAdapter:
    tenant_id: str
    connection_id: str
    connection_version: str
    allowed_models: tuple[str, ...]
    api_key: str = field(repr=False)
    max_output_tokens: int = MAX_OUTPUT_TOKENS
    transport: OpenAITransport = field(default=_openai_https_post, repr=False, compare=False)

    def __post_init__(self) -> None:
        _binding(self.tenant_id, self.connection_id, self.connection_version,
                 self.allowed_models, self.max_output_tokens)
        if (not _secret(self.api_key, 16, 512) or not callable(self.transport)
                or any(_OPENAI_MODEL.fullmatch(model) is None for model in self.allowed_models)):
            raise AdapterConfigurationError()

    def invoke(self, call: ApprovedModelCall) -> ProviderResult:
        started = time.monotonic()
        refusal = _preflight(call, self.allowed_models, self.max_output_tokens)
        if refusal is not None:
            return refusal
        deadline = started + call.timeout_seconds
        payload = {
            "model": call.model_id,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": call.text}]}],
            "max_output_tokens": call.max_output_tokens,
            "store": False, "stream": False, "background": False,
            "tools": [], "tool_choice": "none", "service_tier": "default", "truncation": "disabled",
        }
        try:
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
            remaining = _remaining(deadline)
        except Exception:
            return ProviderResult(outcome="rejected", reason_code="configuration_unavailable")
        try:
            response = self.transport(OPENAI_ENDPOINT, {
                "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                "Accept": "application/json", "Accept-Encoding": "identity",
                "User-Agent": "GuardOath/approved-text-invocation",
            }, body, remaining)
        except _ResponseTooLarge:
            return ProviderResult(outcome="unknown", reason_code="provider_response_too_large")
        except _ResponseInvalid:
            return ProviderResult(outcome="unknown", reason_code="provider_response_invalid")
        except (TimeoutError, socket.timeout):
            return ProviderResult(outcome="unknown", reason_code="provider_timeout")
        except Exception:
            return ProviderResult(outcome="unknown", reason_code="provider_unavailable")
        try:
            if type(response) is not HttpResponse:
                raise _ResponseInvalid()
            response.__post_init__()
            if response.status_code != 200:
                return _http_failure(response.status_code, response.request_id)
            if len(response.body) > MAX_RESPONSE_BYTES:
                raise _ResponseTooLarge()
            document = json.loads(response.body.decode("utf-8"), object_pairs_hook=_unique_keys,
                                  parse_constant=_invalid_constant)
            return _parse_openai(document, call, response.request_id)
        except _ResponseTooLarge:
            return ProviderResult(outcome="unknown", reason_code="provider_response_too_large")
        except Exception:
            return ProviderResult(outcome="unknown", reason_code="provider_response_invalid")


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise _ResponseInvalid()
        result[key] = value
    return result


def _invalid_constant(_: str) -> None:
    raise _ResponseInvalid()


def _http_failure(status: int, request_id: str | None) -> ProviderResult:
    reason = {401: "provider_authentication_failed", 403: "provider_permission_denied",
              404: "provider_model_unavailable", 429: "provider_rate_limited"}.get(status)
    if reason is not None or 400 <= status < 500 and status not in (408, 409):
        return ProviderResult(outcome="rejected", reason_code=reason or "provider_rejected",
                              provider_request_id=request_id)
    return ProviderResult(outcome="unknown", reason_code="provider_timeout" if status == 408 else "provider_unavailable",
                          provider_request_id=request_id)


def _parse_openai(response: Any, call: ApprovedModelCall, request_id: str | None) -> ProviderResult:
    _bounded_tree(response)
    if type(response) is not dict or response.get("object") != "response":
        raise _ResponseInvalid()
    response_id = _optional_identifier(response.get("id"))
    model = _optional_identifier(response.get("model"))
    if response_id is None or model is None:
        raise _ResponseInvalid()
    common = {"provider_request_id": request_id, "response_id": response_id,
              "observed_model": model, "observed_service_tier": _tier(response.get("service_tier"))}
    status = response.get("status")
    if status == "failed" and type(response.get("error")) is dict:
        if response.get("usage") is not None:
            common.update(_usage(response["usage"], "input_tokens", "output_tokens", "total_tokens", call.max_output_tokens))
        return ProviderResult(outcome="unknown", reason_code="provider_unavailable", **common)
    common.update(_usage(response.get("usage"), "input_tokens", "output_tokens", "total_tokens", call.max_output_tokens))
    if (model != call.model_id or common["observed_service_tier"] not in (None, "default")
            or response.get("background", False) is not False
            or response.get("store", False) is not False
            or response.get("tools", []) != []
            or response.get("tool_choice", "none") != "none"
            or response.get("instructions") is not None
            or response.get("prompt") is not None
            or response.get("previous_response_id") is not None
            or response.get("conversation") is not None):
        return ProviderResult(outcome="withheld", reason_code="provider_policy_mismatch", **common)
    if status == "incomplete":
        return ProviderResult(outcome="withheld", reason_code="incomplete_output", **common)
    if status != "completed" or response.get("error") is not None or response.get("incomplete_details") is not None:
        raise _ResponseInvalid()
    output = response.get("output")
    if type(output) is not list or not 1 <= len(output) <= 32:
        raise _ResponseInvalid()
    parts = []
    for item in output:
        if type(item) is not dict:
            raise _ResponseInvalid()
        if item.get("type") == "reasoning":
            if (set(item) - {"type", "id", "summary", "status", "content", "encrypted_content"}
                    or not _identifier(item.get("id")) or item.get("summary") != []
                    or item.get("status") not in (None, "completed")
                    or item.get("content") not in (None, [])
                    or item.get("encrypted_content") is not None):
                return ProviderResult(outcome="withheld", reason_code="unsupported_output", **common)
            # No reasoning content is returned, retained, or replayed.
            continue
        if item.get("type") != "message":
            return ProviderResult(outcome="withheld", reason_code="unsupported_output", **common)
        if (set(item) - {"id", "type", "status", "role", "content", "phase"}
                or not _identifier(item.get("id")) or item.get("role") != "assistant"
                or item.get("status") != "completed"
                or item.get("phase") not in (None, "final_answer")):
            raise _ResponseInvalid()
        content = item.get("content")
        if type(content) is not list or not 1 <= len(content) <= 32:
            raise _ResponseInvalid()
        for block in content:
            if type(block) is not dict:
                raise _ResponseInvalid()
            if block.get("type") == "refusal":
                return ProviderResult(outcome="withheld", reason_code="provider_refused", **common)
            if block.get("type") != "output_text":
                return ProviderResult(outcome="withheld", reason_code="unsupported_output", **common)
            if (set(block) - {"type", "text", "annotations", "logprobs"}
                    or block.get("annotations") != [] or block.get("logprobs", []) != []
                    or not _text(block.get("text"), MAX_OUTPUT_BYTES)):
                raise _ResponseInvalid()
            parts.append(block["text"])
    if not parts:
        return ProviderResult(outcome="withheld", reason_code="incomplete_output", **common)
    return ProviderResult(text=_join_text(parts), outcome="completed", reason_code="completed", **common)
