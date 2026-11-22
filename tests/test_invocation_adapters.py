"""Offline provider contracts.  No test may reach a network or paid model."""
from dataclasses import FrozenInstanceError, asdict
import json
import socket
from types import SimpleNamespace

import pytest

from guardoath.invocations import adapters
from guardoath.invocations.adapters import (
    AdapterConfigurationError, ApprovedModelCall, AwsCredentials,
    BedrockConverseAdapter, HttpResponse, MAX_INPUT_BYTES, MAX_OUTPUT_BYTES,
    MAX_RESPONSE_BYTES, OPENAI_ENDPOINT, OpenAIResponsesAdapter, ProviderResult, validate_bedrock_targets,
)


MODEL = "gpt-5-mini-2025-08-07"
AWS_MODEL = "amazon.nova-lite-v1:0"
SECRET = "test-private-provider-key-which-must-not-leak"
BINDING = {"tenant_id": "tenant-one", "connection_id": "connection-one", "connection_version": "1"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError("A provider adapter test attempted network access")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def call(model=MODEL, **overrides):
    return ApprovedModelCall(**{"text": "A checked prompt", "model_id": model,
                                "max_output_tokens": 128, "timeout_seconds": 3.0, **overrides})


def openai_reply(**overrides):
    return {"object": "response", "id": "resp_123", "model": MODEL,
            "status": "completed", "error": None, "incomplete_details": None,
            "background": False, "store": False, "service_tier": "default",
            "output": [{"id": "msg_123", "type": "message", "status": "completed", "role": "assistant",
                        "content": [{"type": "output_text", "text": "A safe reply", "annotations": []}]}],
            "usage": {"input_tokens": 8, "output_tokens": 4, "total_tokens": 12}, **overrides}


def bedrock_reply(**overrides):
    return {"output": {"message": {"role": "assistant", "content": [{"text": "A safe reply"}]}},
            "stopReason": "end_turn", "usage": {"inputTokens": 8, "outputTokens": 4, "totalTokens": 12},
            "metrics": {"latencyMs": 10}, "serviceTier": {"type": "default"},
            "ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": "aws-request-123", "RetryAttempts": 0},
            **overrides}


class FakeBedrock:
    def __init__(self, reply=None, error=None):
        self.meta = SimpleNamespace(
            service_model=SimpleNamespace(service_name="bedrock-runtime"),
            endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com",
            config=SimpleNamespace(region_name="us-east-1", connect_timeout=0.5, read_timeout=1.5,
                                   max_pool_connections=2,
                                   retries={"mode": "standard", "total_max_attempts": 1}),
        )
        self.reply = bedrock_reply() if reply is None else reply
        self.error = error
        self.calls = []
        self.closed = False

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.reply

    def close(self):
        self.closed = True


def aws_adapter(client=None, **overrides):
    return BedrockConverseAdapter(**{**BINDING, "region": "us-east-1", "allowed_models": (AWS_MODEL,),
                                     "client": FakeBedrock() if client is None else client, **overrides})


def openai_adapter(reply=None, *, response=None, error=None, **overrides):
    calls = []
    def transport(url, headers, body, timeout):
        calls.append((url, headers, json.loads(body), timeout))
        if error is not None:
            raise error
        if response is not None:
            return response
        return HttpResponse(200, json.dumps(openai_reply() if reply is None else reply).encode(), "req_456")
    return OpenAIResponsesAdapter(**{**BINDING, "api_key": SECRET, "allowed_models": (MODEL,),
                                      "transport": transport, **overrides}), calls


def assert_private(result):
    serialized = json.dumps(asdict(result))
    assert SECRET not in serialized
    assert "PRIVATE" not in serialized


@pytest.mark.parametrize("overrides", [
    {"text": ""}, {"text": "x" * (MAX_INPUT_BYTES + 1)}, {"text": "é" * (MAX_INPUT_BYTES // 2 + 1)},
    {"text": "\ud800"}, {"text": None}, {"model_id": ""}, {"model_id": False},
    {"max_output_tokens": True}, {"max_output_tokens": 0}, {"max_output_tokens": 4_097},
    {"timeout_seconds": True}, {"timeout_seconds": 0}, {"timeout_seconds": float("nan")},
    {"timeout_seconds": float("inf")}, {"timeout_seconds": 10.01},
    {"timeout_seconds": 10 ** 400},
])
def test_approved_call_is_strict_and_errors_never_echo_input(overrides):
    with pytest.raises(ValueError, match="^Invalid approved model call$"):
        call(**overrides)


def test_inputs_results_and_bindings_are_frozen_and_content_is_not_in_repr():
    request = call(text="PRIVATE checked input")
    result = ProviderResult(text="PRIVATE checked output", input_tokens=1, output_tokens=1,
                            outcome="completed", reason_code="completed")
    provider, _ = openai_adapter()
    for obj, name, value in [(request, "text", "replacement"), (result, "text", "replacement"),
                              (provider, "tenant_id", "tenant-two")]:
        with pytest.raises(FrozenInstanceError):
            setattr(obj, name, value)
    assert "PRIVATE" not in repr(request) + repr(result)
    assert SECRET not in repr(provider)


@pytest.mark.parametrize("fields", [
    {"outcome": "completed", "reason_code": "completed", "text": "ok"},
    {"text": "unreleased private text"}, {"input_tokens": 1},
    {"input_tokens": True, "output_tokens": 1}, {"input_tokens": -1, "output_tokens": 0},
    {"outcome": "unknown", "reason_code": "completed"}, {"reason_code": "raw private provider error"},
    {"provider_request_id": "id\nPRIVATE"}, {"provider_request_id": "x" * 201},
    {"observed_service_tier": "arbitrary-tier"},
])
def test_normalized_result_rejects_inconsistent_or_unbounded_metadata(fields):
    with pytest.raises(ValueError, match="^Invalid normalized provider result$"):
        ProviderResult(**fields)


@pytest.mark.parametrize("kind", ["aws", "openai"])
def test_exact_fixed_protocol_and_provider_reported_observations(kind):
    if kind == "aws":
        client = FakeBedrock()
        result = aws_adapter(client).invoke(call(AWS_MODEL))
        assert client.calls == [{"modelId": AWS_MODEL, "messages": [{"role": "user", "content": [{"text": "A checked prompt"}]}],
                                 "inferenceConfig": {"maxTokens": 128}, "serviceTier": {"type": "default"}}]
        assert result.provider_request_id == "aws-request-123"
        assert result.observed_model is None and result.response_id is None
        assert client.closed is False  # Injected client lifetime belongs to the trusted caller.
    else:
        provider, calls = openai_adapter()
        result = provider.invoke(call())
        assert len(calls) == 1
        url, headers, payload, timeout = calls[0]
        assert url == OPENAI_ENDPOINT
        assert headers["Authorization"] == f"Bearer {SECRET}"
        assert headers["Accept-Encoding"] == "identity" and 0 < timeout <= 3.0
        assert payload == {"model": MODEL, "input": [{"role": "user", "content": [{"type": "input_text", "text": "A checked prompt"}]}],
                           "max_output_tokens": 128, "store": False, "stream": False, "background": False,
                           "tools": [], "tool_choice": "none", "service_tier": "default", "truncation": "disabled"}
        assert result.provider_request_id == "req_456" and result.response_id == "resp_123"
        assert result.observed_model == MODEL
    assert result.outcome == "completed" and result.text == "A safe reply"
    assert (result.input_tokens, result.output_tokens) == (8, 4)
    assert result.observed_service_tier == "default"


@pytest.mark.parametrize("kind", ["aws", "openai"])
def test_unapproved_model_and_connector_token_ceiling_never_dispatch(kind):
    client = FakeBedrock()
    provider, calls = (aws_adapter(client, max_output_tokens=64), client.calls) if kind == "aws" else openai_adapter(max_output_tokens=64)
    result = provider.invoke(call("unapproved-model"))
    assert (result.outcome, result.reason_code) == ("rejected", "model_not_allowed")
    result = provider.invoke(call(AWS_MODEL if kind == "aws" else MODEL))
    assert (result.outcome, result.reason_code) == ("rejected", "request_invalid")
    assert provider.invoke({"text": "PRIVATE"}).reason_code == "request_invalid"
    forged = call(AWS_MODEL if kind == "aws" else MODEL, max_output_tokens=32)
    object.__setattr__(forged, "timeout_seconds", float("nan"))
    assert provider.invoke(forged).reason_code == "request_invalid"
    assert calls == []


@pytest.mark.parametrize("kind", ["aws", "openai"])
@pytest.mark.parametrize("overrides", [
    {"tenant_id": ""}, {"connection_id": "../connection"}, {"connection_version": True},
    {"allowed_models": []}, {"allowed_models": ()}, {"allowed_models": ("same", "same")},
    {"allowed_models": (None,)}, {"max_output_tokens": True}, {"max_output_tokens": 4_097},
])
def test_trusted_binding_configuration_is_strict(kind, overrides):
    with pytest.raises(AdapterConfigurationError, match="^Invalid provider adapter configuration$"):
        aws_adapter(**overrides) if kind == "aws" else openai_adapter(**overrides)


@pytest.mark.parametrize("model", [
    "arn:aws:bedrock:us-east-1:123456789012:prompt/ABCDEFGHIJ:1",
    "arn:aws:bedrock:us-east-1:123456789012:prompt-router/router",
    "arn:aws:bedrock:us-east-1:123456789012:default-prompt-router/router",
    "arn:aws:bedrock:us-east-1:123456789012:agent/ABCDEFGHIJ",
    "arn:aws:sagemaker:us-east-1:123456789012:endpoint/endpoint",
    "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/profile",
    "arn:aws:bedrock:us-west-2::foundation-model/amazon.nova-lite-v1:0",
    "arn:aws:bedrock:us-east-1:123456789012:foundation-model/amazon.nova-lite-v1:0",
    "arn:aws:bedrock:us-east-1::inference-profile/profile",
    "https://evil.example/model", "amazon.model?extra=1", "../model", "arbitrary-prompt-id",
    "amazon.model:", "amazon.model::0", "amazon.model:not-a-version",
])
def test_bedrock_allows_only_approved_model_resource_kinds_and_region(model):
    with pytest.raises(AdapterConfigurationError):
        aws_adapter(allowed_models=(model,))


@pytest.mark.parametrize("model", [
    AWS_MODEL, "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0",
    "arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.anthropic.claude-sonnet-4-5-20250929-v1:0",
])
def test_bedrock_exact_resolved_model_ids_are_not_restricted_to_public_alias_regex(model):
    assert validate_bedrock_targets("us-east-1", (model,)) is None
    provider = aws_adapter(allowed_models=(model,))
    assert provider.invoke(call(model)).outcome == "completed"
    assert provider.client.calls[0]["modelId"] == model


@pytest.mark.parametrize("region,models", [("us-east-1.evil.example", (AWS_MODEL,)), ("", (AWS_MODEL,)),
                                          ("us-east-1", ()), ("us-east-1", [AWS_MODEL]),
                                          ("us-east-1", (AWS_MODEL, AWS_MODEL)), ("us-east-1", (False,)),
                                          ("us-east-1", ("arn:aws:bedrock:us-east-1:123456789012:prompt/ABC",))])
def test_pure_target_validation_rejects_bad_startup_configuration_without_credentials(region, models):
    with pytest.raises(AdapterConfigurationError):
        validate_bedrock_targets(region, models)


@pytest.mark.parametrize("field,value", [("read_timeout", 60), ("connect_timeout", float("nan")),
                                          ("max_pool_connections", 10),
                                          ("retries", {"mode": "standard", "total_max_attempts": 2}),
                                          ("retries", {"mode": "standard", "total_max_attempts": True}),
                                          ("region_name", "us-west-2")])
def test_injected_aws_client_must_have_bounded_single_attempt_configuration(field, value):
    client = FakeBedrock()
    setattr(client.meta.config, field, value)
    with pytest.raises(AdapterConfigurationError):
        aws_adapter(client)
    assert client.calls == []


def test_injected_aws_client_cannot_override_endpoint_or_exceed_current_budget():
    client = FakeBedrock()
    client.meta.endpoint_url = "https://evil.example"
    with pytest.raises(AdapterConfigurationError):
        aws_adapter(client)
    client.meta.endpoint_url = "https://bedrock-runtime.us-east-1.amazonaws.com"
    result = aws_adapter(client).invoke(call(AWS_MODEL, timeout_seconds=0.5))
    assert result.reason_code == "configuration_unavailable" and client.calls == []


@pytest.mark.parametrize("kind", ["aws", "openai"])
@pytest.mark.parametrize("usage", [None, {}, {"input": True, "output": 1, "total": 2},
                                   {"input": -1, "output": 1, "total": 0},
                                   {"input": 8, "output": 129, "total": 137},
                                   {"input": 8, "output": 4, "total": 999}])
def test_missing_or_untrusted_usage_is_unknown_never_zero(kind, usage):
    normalized = None if usage is None else {
        (f"{key}Tokens" if kind == "aws" else f"{key}_tokens"): value for key, value in usage.items()
    }
    if kind == "aws":
        result = aws_adapter(FakeBedrock(bedrock_reply(usage=normalized))).invoke(call(AWS_MODEL))
    else:
        provider, _ = openai_adapter(openai_reply(usage=normalized))
        result = provider.invoke(call())
    assert result.outcome == "unknown" and result.text is None
    assert result.input_tokens is result.output_tokens is None


@pytest.mark.parametrize("stop,reason", [("tool_use", "unsupported_output"), ("malformed_tool_use", "unsupported_output"),
                                        ("max_tokens", "incomplete_output"), ("stop_sequence", "incomplete_output"),
                                        ("content_filtered", "provider_filtered"), ("guardrail_intervened", "provider_filtered")])
def test_bedrock_completed_but_withheld_outcomes_retain_measured_usage(stop, reason):
    result = aws_adapter(FakeBedrock(bedrock_reply(stopReason=stop))).invoke(call(AWS_MODEL))
    assert (result.outcome, result.reason_code, result.text) == ("withheld", reason, None)
    assert (result.input_tokens, result.output_tokens) == (8, 4)


@pytest.mark.parametrize("block", [{"toolUse": {"name": "PRIVATE", "input": {"secret": SECRET}}},
                                   {"reasoningContent": {"reasoningText": {"text": "PRIVATE"}}},
                                   {"image": {"source": "PRIVATE"}}, {"unknown": "PRIVATE"},
                                   {"text": "safe", "unknown": "PRIVATE"}])
def test_bedrock_unexpected_union_is_withheld_without_content_or_tools(block):
    response = bedrock_reply(output={"message": {"role": "assistant", "content": [block]}})
    result = aws_adapter(FakeBedrock(response)).invoke(call(AWS_MODEL))
    assert result.outcome == "withheld" and result.text is None
    assert_private(result)


@pytest.mark.parametrize("patch", [
    {"stopReason": "unknown"}, {"output": {"message": {"role": "user", "content": [{"text": "PRIVATE"}]}}},
    {"ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": "id\nPRIVATE"}},
    {"ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": "id", "RetryAttempts": 1}},
    {"ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": "id", "RetryAttempts": False}},
    {"output": {"message": {"role": "assistant", "content": []}}},
])
def test_bedrock_invalid_terminal_schema_is_unknown(patch):
    result = aws_adapter(FakeBedrock(bedrock_reply(**patch))).invoke(call(AWS_MODEL))
    assert result.outcome == "unknown" and result.text is None
    assert_private(result)


@pytest.mark.parametrize("kind", ["aws", "openai"])
def test_output_byte_budget_counts_utf8_and_join_separators(kind):
    if kind == "aws":
        response = bedrock_reply(output={"message": {"role": "assistant", "content": [{"text": "é" * (MAX_OUTPUT_BYTES // 2)}, {"text": "x"}]}})
        result = aws_adapter(FakeBedrock(response)).invoke(call(AWS_MODEL))
    else:
        response = openai_reply()
        response["output"][0]["content"][0]["text"] = "é" * (MAX_OUTPUT_BYTES // 2 + 1)
        provider, _ = openai_adapter(response)
        result = provider.invoke(call())
    assert result.outcome == "unknown" and result.text is None


@pytest.mark.parametrize("item_type", ["function_call", "web_search_call", "mcp_call", "file_search_call", "computer_call", "new_unknown_type"])
def test_openai_tool_and_unknown_items_are_withheld_not_executed(item_type):
    provider, calls = openai_adapter(openai_reply(output=[{"type": item_type, "arguments": SECRET}]))
    result = provider.invoke(call())
    assert result.outcome == "withheld" and result.reason_code == "unsupported_output"
    assert result.output_tokens == 4 and len(calls) == 1
    assert_private(result)


def test_openai_empty_reasoning_metadata_is_discarded_and_only_assistant_text_is_returned():
    reply = openai_reply()
    reply["output"].insert(0, {"id": "rs_123", "type": "reasoning", "summary": [], "encrypted_content": None})
    provider, _ = openai_adapter(reply)
    result = provider.invoke(call())
    assert result.outcome == "completed" and result.text == "A safe reply"
    assert "rs_123" not in json.dumps(asdict(result))


@pytest.mark.parametrize("patch", [{"summary": [{"type": "summary_text", "text": "PRIVATE"}]},
                                    {"encrypted_content": SECRET}, {"content": ["PRIVATE"]},
                                    {"extra": "PRIVATE"}])
def test_openai_reasoning_content_or_unknown_metadata_is_withheld(patch):
    reply = openai_reply()
    reply["output"].insert(0, {"id": "rs_123", "type": "reasoning", "summary": [], **patch})
    provider, _ = openai_adapter(reply)
    result = provider.invoke(call())
    assert result.outcome == "withheld" and result.text is None
    assert_private(result)


@pytest.mark.parametrize("patch", [{"model": "different-model"}, {"service_tier": "priority"}, {"background": True},
                                    {"store": True}, {"previous_response_id": "resp_prior"},
                                    {"conversation": {"id": "conv_other"}}, {"tools": [{"type": "web_search"}]},
                                    {"tool_choice": "auto"}, {"instructions": "PRIVATE"}, {"prompt": {"id": "remote"}}])
def test_openai_observed_policy_mismatch_is_withheld_with_usage(patch):
    provider, _ = openai_adapter(openai_reply(**patch))
    result = provider.invoke(call())
    assert (result.outcome, result.reason_code) == ("withheld", "provider_policy_mismatch")
    assert (result.input_tokens, result.output_tokens) == (8, 4)
    assert_private(result)


def test_bedrock_observed_priority_tier_is_withheld_not_relabelled_default():
    result = aws_adapter(FakeBedrock(bedrock_reply(serviceTier={"type": "priority"}))).invoke(call(AWS_MODEL))
    assert result.outcome == "withheld" and result.observed_service_tier == "priority"


def test_openai_incomplete_and_refusal_responses_never_release_partial_text():
    provider, _ = openai_adapter(openai_reply(status="incomplete", incomplete_details={"reason": "max_output_tokens"}))
    result = provider.invoke(call())
    assert (result.outcome, result.reason_code, result.text) == ("withheld", "incomplete_output", None)
    reply = openai_reply()
    reply["output"][0]["content"] = [{"type": "refusal", "refusal": "PRIVATE provider refusal"}]
    provider, _ = openai_adapter(reply)
    result = provider.invoke(call())
    assert result.reason_code == "provider_refused" and result.output_tokens == 4
    assert_private(result)


def test_openai_failed_response_cannot_claim_no_generation_or_zero_usage():
    provider, _ = openai_adapter(openai_reply(status="failed", error={"message": SECRET}, usage=None))
    result = provider.invoke(call())
    assert result.outcome == "unknown" and result.input_tokens is result.output_tokens is None
    assert_private(result)


@pytest.mark.parametrize("body", [b'{"id":"one","id":"two"}', b'{"usage":NaN}', b'{"usage":Infinity}',
                                  b'not json PRIVATE', b'\xff', b'x' * (MAX_RESPONSE_BYTES + 1)])
def test_openai_raw_body_is_bounded_strict_json_without_leakage(body):
    provider, calls = openai_adapter(response=HttpResponse(200, body, "req_123"))
    result = provider.invoke(call())
    assert result.outcome == "unknown" and result.text is None and len(calls) == 1
    assert_private(result)


def test_duplicate_keys_in_an_otherwise_valid_completed_reply_are_rejected():
    raw = json.dumps(openai_reply()).replace('"id": "resp_123"', '"id": "resp_old", "id": "resp_123"', 1).encode()
    provider, _ = openai_adapter(response=HttpResponse(200, raw, "req_123"))
    assert provider.invoke(call()).reason_code == "provider_response_invalid"


@pytest.mark.parametrize("kind", ["aws", "openai"])
def test_bounded_structure_refuses_deep_nesting_after_dispatch(kind):
    nested = "PRIVATE"
    for _ in range(20):
        nested = [nested]
    if kind == "aws":
        result = aws_adapter(FakeBedrock(bedrock_reply(extra=nested))).invoke(call(AWS_MODEL))
    else:
        provider, _ = openai_adapter(openai_reply(extra=nested))
        result = provider.invoke(call())
    assert result.outcome == "unknown" and result.reason_code == "provider_response_too_large"
    assert_private(result)


@pytest.mark.parametrize("status,outcome,reason", [(302, "unknown", "provider_unavailable"),
                                                  (401, "rejected", "provider_authentication_failed"),
                                                  (403, "rejected", "provider_permission_denied"),
                                                  (404, "rejected", "provider_model_unavailable"),
                                                  (408, "unknown", "provider_timeout"),
                                                  (409, "unknown", "provider_unavailable"),
                                                  (429, "rejected", "provider_rate_limited"),
                                                  (500, "unknown", "provider_unavailable")])
def test_openai_error_status_is_single_attempt_sanitized_and_usage_unknown(status, outcome, reason):
    provider, calls = openai_adapter(response=HttpResponse(status, SECRET.encode(), "req_error"))
    result = provider.invoke(call())
    assert (result.outcome, result.reason_code) == (outcome, reason)
    assert result.provider_request_id == "req_error"
    assert result.input_tokens is result.output_tokens is None and len(calls) == 1
    assert_private(result)


@pytest.mark.parametrize("kind", ["aws", "openai"])
@pytest.mark.parametrize("error", [TimeoutError(SECRET), ConnectionError(SECRET), RuntimeError(SECRET)])
def test_transport_failure_is_unknown_once_and_never_echoes_private_error(kind, error):
    if kind == "aws":
        client = FakeBedrock(error=error)
        result = aws_adapter(client).invoke(call(AWS_MODEL))
        count = len(client.calls)
    else:
        provider, calls = openai_adapter(error=error)
        result = provider.invoke(call())
        count = len(calls)
    assert result.outcome == "unknown" and result.input_tokens is result.output_tokens is None
    assert count == 1
    assert_private(result)


def test_explicit_temporary_credentials_are_required_and_not_printed():
    credentials = AwsCredentials("ASIA" + "A" * 16, "s" * 40, "t" * 80)
    assert "s" * 40 not in repr(credentials) and "t" * 80 not in repr(credentials)
    with pytest.raises(AdapterConfigurationError):
        AwsCredentials("ASIA" + "A" * 16, "s" * 40, "")
    with pytest.raises(AdapterConfigurationError):
        BedrockConverseAdapter(**BINDING, region="us-east-1", allowed_models=(AWS_MODEL,))
    with pytest.raises(AdapterConfigurationError):
        aws_adapter(credentials=credentials)


def test_bedrock_factory_pins_credentials_endpoint_one_attempt_and_stubber_protocol(monkeypatch):
    pytest.importorskip("boto3")
    from botocore.stub import Stubber

    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://evil.example")
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "50")
    credentials = AwsCredentials("ASIA" + "A" * 16, "s" * 40, "t" * 80)
    client = adapters._bedrock_client(credentials, "us-east-1", 3.0)
    try:
        assert client.meta.endpoint_url == "https://bedrock-runtime.us-east-1.amazonaws.com"
        assert client.meta.config.retries == {"mode": "standard", "total_max_attempts": 1}
        assert client.meta.config.connect_timeout + client.meta.config.read_timeout == 3.0
        assert client.meta.config.proxies == {} and client.meta.config.max_pool_connections == 2
        frozen = client._request_signer._credentials.get_frozen_credentials()
        assert (frozen.access_key, frozen.secret_key, frozen.token) == (credentials.access_key_id, credentials.secret_access_key, credentials.session_token)
        with Stubber(client) as stubber:
            stubber.add_response("converse", bedrock_reply(), {
                "modelId": AWS_MODEL, "messages": [{"role": "user", "content": [{"text": "A checked prompt"}]}],
                "inferenceConfig": {"maxTokens": 128}, "serviceTier": {"type": "default"},
            })
            result = aws_adapter(client).invoke(call(AWS_MODEL, timeout_seconds=4.0))
            assert result.outcome == "completed" and result.text == "A safe reply"
            stubber.assert_no_pending_responses()
    finally:
        client.close()


@pytest.mark.parametrize("code,outcome,reason", [("AccessDeniedException", "rejected", "provider_permission_denied"),
                                                ("ThrottlingException", "rejected", "provider_rate_limited"),
                                                ("ModelTimeoutException", "unknown", "provider_timeout"),
                                                ("InternalServerException", "unknown", "provider_unavailable")])
def test_bedrock_stubber_errors_never_leak_provider_message_or_retry(code, outcome, reason):
    pytest.importorskip("boto3")
    from botocore.stub import Stubber

    client = adapters._bedrock_client(AwsCredentials("ASIA" + "A" * 16, "s" * 40, "t" * 80), "us-east-1", 3.0)
    try:
        with Stubber(client) as stubber:
            stubber.add_client_error("converse", service_error_code=code, service_message=SECRET,
                                     response_meta={"RequestId": "aws-error-123"})
            result = aws_adapter(client).invoke(call(AWS_MODEL, timeout_seconds=4.0))
            assert (result.outcome, result.reason_code) == (outcome, reason)
            assert result.provider_request_id == "aws-error-123" and result.input_tokens is None
            assert_private(result)
            stubber.assert_no_pending_responses()
    finally:
        client.close()


def test_owned_aws_client_is_closed_and_factory_failure_is_not_dispatched(monkeypatch):
    client = FakeBedrock()
    credentials = AwsCredentials("ASIA" + "A" * 16, "s" * 40, "t" * 80)
    provider = BedrockConverseAdapter(**BINDING, region="us-east-1", allowed_models=(AWS_MODEL,), credentials=credentials)
    monkeypatch.setattr(adapters, "_bedrock_client", lambda *_args: client)
    assert provider.invoke(call(AWS_MODEL)).outcome == "completed" and client.closed
    def broken(*_args):
        raise RuntimeError(SECRET)
    monkeypatch.setattr(adapters, "_bedrock_client", broken)
    result = provider.invoke(call(AWS_MODEL))
    assert result.outcome == "rejected" and result.reason_code == "configuration_unavailable"
    assert_private(result)


class FakeSocket:
    def __init__(self):
        self.timeouts = []

    def settimeout(self, timeout):
        assert 0 < timeout <= 3
        self.timeouts.append(timeout)


class FakeHttpBody:
    def __init__(self, body, *, status=200, headers=None):
        self.status = status
        self.headers = {"Content-Type": "application/json", "x-request-id": "req_http", **(headers or {})}
        self.body = body
        self.bytes_read = 0
        self.read_sizes = []

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read1(self, size):
        self.read_sizes.append(size)
        chunk, self.body = self.body[:size], self.body[size:]
        self.bytes_read += len(chunk)
        return chunk

    def close(self):
        pass


def fake_https(monkeypatch, response):
    connections = []
    class Connection:
        def __init__(self, host, port, *, timeout, context):
            assert host == "api.openai.com" and port == 443
            assert context.check_hostname and context.verify_mode == adapters.ssl.CERT_REQUIRED
            assert 0 < timeout <= 1
            self.sock = FakeSocket()
            self.requests = []
            self.closed = False
            connections.append(self)

        def connect(self):
            pass

        def request(self, method, path, *, body, headers):
            self.requests.append((method, path, json.loads(body), headers))

        def getresponse(self):
            return response

        def close(self):
            self.closed = True
    monkeypatch.setattr(adapters.http.client, "HTTPSConnection", Connection)
    return connections


def test_real_stdlib_transport_is_fixed_https_bounded_and_closes(monkeypatch):
    raw = json.dumps(openai_reply()).encode()
    body = FakeHttpBody(raw, headers={"Content-Length": str(len(raw))})
    connections = fake_https(monkeypatch, body)
    provider = OpenAIResponsesAdapter(**BINDING, allowed_models=(MODEL,), api_key=SECRET)
    result = provider.invoke(call())
    assert result.outcome == "completed" and result.provider_request_id == "req_http"
    assert len(connections) == 1 and connections[0].closed
    assert len(connections[0].requests) == 1 and connections[0].requests[0][:2] == ("POST", "/v1/responses")
    assert body.bytes_read == len(raw) and max(body.read_sizes) <= 8_192


@pytest.mark.parametrize("status", [302, 401, 429, 500])
def test_real_stdlib_transport_does_not_follow_redirects_read_error_body_or_retry(monkeypatch, status):
    body = FakeHttpBody(SECRET.encode(), status=status, headers={"Location": "https://evil.example"})
    connections = fake_https(monkeypatch, body)
    provider = OpenAIResponsesAdapter(**BINDING, allowed_models=(MODEL,), api_key=SECRET)
    result = provider.invoke(call())
    assert result.outcome in ("unknown", "rejected") and result.text is None
    assert len(connections) == 1 and len(connections[0].requests) == 1 and connections[0].closed
    assert body.bytes_read == 0
    assert_private(result)


@pytest.mark.parametrize("headers,bytes_read", [({"Content-Length": str(MAX_RESPONSE_BYTES + 1)}, 0),
                                              ({"Content-Encoding": "gzip"}, 0),
                                              ({"Content-Type": "text/html"}, 0), ({}, MAX_RESPONSE_BYTES + 1)])
def test_real_stdlib_transport_enforces_body_cap_before_json_decode(monkeypatch, headers, bytes_read):
    body = FakeHttpBody(b"x" * (MAX_RESPONSE_BYTES + 10_000), headers=headers)
    connections = fake_https(monkeypatch, body)
    provider = OpenAIResponsesAdapter(**BINDING, allowed_models=(MODEL,), api_key=SECRET)
    result = provider.invoke(call())
    assert result.outcome == "unknown" and result.text is None
    assert body.bytes_read == bytes_read and connections[0].closed


def test_stdlib_read_loop_enforces_elapsed_deadline_even_for_a_dribbling_peer(monkeypatch):
    body = FakeHttpBody(b"x" * 50_000)
    connections = fake_https(monkeypatch, body)
    ticks = iter([0, 0.1, 0.2, 0.3, 0.4, 0.5, 4.0])
    monkeypatch.setattr(adapters.time, "monotonic", lambda: next(ticks))
    provider = OpenAIResponsesAdapter(**BINDING, allowed_models=(MODEL,), api_key=SECRET)
    result = provider.invoke(call())
    assert result.reason_code == "provider_timeout" and body.bytes_read == 8_192
    assert connections[0].closed and len(connections[0].requests) == 1


@pytest.mark.parametrize("setup_elapsed", [1.1, 3.1])
def test_owned_bedrock_slow_setup_cannot_start_paid_inference_past_budget(monkeypatch, setup_elapsed):
    clock = [0.0]
    created = []
    client = FakeBedrock()
    def factory(credentials, region, timeout_seconds):
        assert 0 < timeout_seconds <= 2.25
        created.append(client)
        clock[0] = setup_elapsed
        return client
    monkeypatch.setattr(adapters.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(adapters, "_bedrock_client", factory)
    provider = BedrockConverseAdapter(**BINDING, region="us-east-1", allowed_models=(AWS_MODEL,),
                                     credentials=AwsCredentials("ASIA" + "A" * 16, "s" * 40, "t" * 80))
    result = provider.invoke(call(AWS_MODEL))
    assert (result.outcome, result.reason_code) == ("rejected", "configuration_unavailable")
    assert client.calls == [] and client.closed and created == [client]


def test_injected_bedrock_client_must_fit_remaining_not_original_allowance(monkeypatch):
    ticks = iter([0.0, 1.1])
    monkeypatch.setattr(adapters.time, "monotonic", lambda: next(ticks))
    client = FakeBedrock()
    result = aws_adapter(client).invoke(call(AWS_MODEL))
    assert result.reason_code == "configuration_unavailable" and client.calls == []
    assert client.closed is False


@pytest.mark.parametrize("serialization_seconds", [0.5, 3.1])
def test_openai_serialization_consumes_the_original_allowance(monkeypatch, serialization_seconds):
    clock = [0.0]
    dumps = adapters.json.dumps
    def slow_serialization(*args, **kwargs):
        result = dumps(*args, **kwargs)
        clock[0] = serialization_seconds
        return result
    monkeypatch.setattr(adapters.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(adapters.json, "dumps", slow_serialization)
    provider, calls = openai_adapter()
    result = provider.invoke(call())
    if serialization_seconds > 3:
        assert (result.outcome, result.reason_code) == ("rejected", "configuration_unavailable")
        assert calls == []
    else:
        assert result.outcome == "completed" and len(calls) == 1
        assert calls[0][3] == 3.0 - serialization_seconds
