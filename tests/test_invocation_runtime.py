"""Explicit startup configuration: no resources, model calls or secret exposure."""
import base64
from datetime import datetime, timedelta, timezone
import json
import sys
from types import SimpleNamespace

import pytest

from guardoath.invocations import adapters
from guardoath.invocations import runtime
from guardoath.tenant_record import TenantRecord


def config(provider="openai"):
    return {"application_id": "customer-a", "info": {
        "id": "approved", "version": "v1", "label": "Synthetic connection", "provider": provider,
        "region": "us-east-1" if provider == "aws_bedrock" else None,
        "models": [{"id": "model-a", "label": "Synthetic model",
                    "provider_model_id": "amazon.synthetic-v1:0" if provider == "aws_bedrock" else "synthetic-model-1",
                    "max_output_tokens": 512}],
        "monthly_call_limit": 10, "monthly_output_token_limit": 2000,
    }, "credentials": ({"kind": "aws_role", "role_arn": "arn:aws:iam::111122223333:role/synthetic-model-role"}
                        if provider == "aws_bedrock" else
                        {"kind": "openai_key", "secret_env": "GUARDOATH_PROVIDER_SECRET_SYNTHETIC"})}


@pytest.fixture
def environment(monkeypatch):
    calls = []

    def client(service, **kwargs):
        calls.append((service, kwargs))
        def forbidden(**unused):
            raise AssertionError("Configuration must not call AWS")
        return SimpleNamespace(get_item=forbidden, query=forbidden, transact_write_items=forbidden)

    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=client))
    monkeypatch.setitem(sys.modules, "botocore.config", SimpleNamespace(Config=lambda **kwargs: SimpleNamespace(**kwargs)))
    monkeypatch.setenv("GUARDOATH_INVOCATIONS_ENABLED", "1")
    monkeypatch.setenv("GUARDOATH_INVOCATION_TABLE", "synthetic-invocations")
    monkeypatch.setenv("GUARDOATH_INVOCATION_REGION", "us-east-1")
    monkeypatch.setenv("GUARDOATH_INVOCATION_SIGNING_KEY", base64.b64encode(b"synthetic-signing-key-0123456789abcdef").decode())
    monkeypatch.setenv("GUARDOATH_PROVIDER_SECRET_SYNTHETIC", "synthetic-secret-not-a-real-provider-key")
    monkeypatch.setenv("GUARDOATH_AI_CONNECTIONS_JSON", json.dumps([config()]))
    return calls


def test_disabled_runtime_makes_no_client_and_ignores_stray_provider_settings(environment, monkeypatch):
    monkeypatch.setenv("GUARDOATH_INVOCATIONS_ENABLED", "0")
    monkeypatch.setenv("GUARDOATH_AI_CONNECTIONS_JSON", "invalid and not read")
    result = runtime.from_environment()
    assert result.gateway is None and result.reason_code == "runtime_unconfigured"
    assert environment == []


@pytest.mark.parametrize("name,value", [
    ("GUARDOATH_INVOCATIONS_ENABLED", "true"),
    ("GUARDOATH_INVOCATION_TABLE", "bad/table"),
    ("GUARDOATH_INVOCATION_REGION", "us-east-1/evil"),
    ("GUARDOATH_INVOCATION_REGION", "us-gov-west-1"),
    ("GUARDOATH_INVOCATION_SIGNING_KEY", "invalid-signing-key"),
    ("GUARDOATH_INVOCATION_SIGNING_KEY", base64.b64encode(b"too-short").decode()),
    ("GUARDOATH_AI_CONNECTIONS_JSON", "[]"),
    ("GUARDOATH_AI_CONNECTIONS_JSON", '{"connections":[]}'),
    ("GUARDOATH_AI_CONNECTIONS_JSON", '[{"application_id":"a","application_id":"b"}]'),
    ("GUARDOATH_PROVIDER_SECRET_SYNTHETIC", "bad\nsecret-value"),
])
def test_invalid_activation_is_fail_closed_without_partial_registry(environment, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    result = runtime.from_environment()
    assert result.gateway is None and result.reason_code == "runtime_invalid"
    assert environment == []


@pytest.mark.parametrize("provider", ["openai", "aws_bedrock"])
def test_valid_registry_is_tenant_bound_and_does_not_probe_model_or_roles(environment, monkeypatch, provider):
    monkeypatch.setenv("GUARDOATH_AI_CONNECTIONS_JSON", json.dumps([config(provider)]))
    result = runtime.from_environment()
    assert result.gateway is not None and result.reason_code == "ready"
    try:
        catalog = result.gateway.catalog(TenantRecord(tenant_id="customer-a", display_name="Synthetic",
                                                      revision=1, subscription_status="active", input_check_secrets="block"))
        assert catalog.available is True and catalog.connections[0].provider == provider
        public = catalog.model_dump_json()
        for hidden in ("synthetic-secret", "role_arn", "secret_env", "111122223333", "external_id"):
            assert hidden not in public
        assert [name for name, _ in environment] == ["dynamodb"]
        assert environment[0][1]["config"].retries["total_max_attempts"] == 1
    finally:
        result.gateway.workers.close()


@pytest.mark.parametrize("mutation", [
    lambda c: c["credentials"].update(secret_env="AWS_SECRET_ACCESS_KEY"),
    lambda c: c["info"].update(region="us-east-1"),
    lambda c: c["info"].update(provider="anthropic"),
    lambda c: c["info"].update(monthly_call_limit=0),
    lambda c: c.update(endpoint="https://example.invalid"),
    lambda c: c["info"]["models"][0].update(provider_model_id="https://example.invalid"),
])
def test_registry_rejects_unapproved_secret_names_endpoints_and_provider_shapes(environment, monkeypatch, mutation):
    value = config()
    mutation(value)
    monkeypatch.setenv("GUARDOATH_AI_CONNECTIONS_JSON", json.dumps([value]))
    assert runtime.from_environment().reason_code == "runtime_invalid"
    assert environment == []


def test_bedrock_prompt_resources_are_not_plain_model_targets(environment, monkeypatch):
    value = config("aws_bedrock")
    value["info"]["models"][0]["provider_model_id"] = "arn:aws:bedrock:us-east-1:111122223333:prompt/ABCDEFGHIJ:1"
    monkeypatch.setenv("GUARDOATH_AI_CONNECTIONS_JSON", json.dumps([value]))
    assert runtime.from_environment().reason_code == "runtime_invalid"
    assert environment == []


def test_role_credentials_are_explicit_scoped_temporary_and_cached(monkeypatch):
    assumed, received = [], []
    now = datetime.now(timezone.utc)

    def assume(**kwargs):
        assumed.append(kwargs)
        return {"Credentials": {"AccessKeyId": "ASIASYNTHETIC12345678", "SecretAccessKey": "synthetic-secret-value-0123456789",
                                "SessionToken": "synthetic-session-token-0123456789", "Expiration": now + timedelta(minutes=15)}}

    def bedrock(**kwargs):
        received.append(kwargs)
        return SimpleNamespace(invoke=lambda call: adapters.ProviderResult(outcome="rejected", reason_code="provider_rejected"))

    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=lambda service, **kwargs: SimpleNamespace(assume_role=assume, close=lambda: None)))
    monkeypatch.setitem(sys.modules, "botocore.config", SimpleNamespace(Config=lambda **kwargs: SimpleNamespace(**kwargs)))
    monkeypatch.setattr(adapters, "BedrockConverseAdapter", bedrock)
    adapter = runtime._RoleBedrockAdapter(tenant_id="customer-a", connection_id="approved", connection_version="v1",
        region="us-east-1", role_arn="arn:aws:iam::111122223333:role/synthetic", external_id="synthetic-external-id",
        allowed_models=("amazon.synthetic-v1:0",))
    call = adapters.ApprovedModelCall("Synthetic", "amazon.synthetic-v1:0", 64, 4)
    assert adapter.invoke(call).reason_code == "provider_rejected"
    assert adapter.invoke(call).reason_code == "provider_rejected"
    assert len(assumed) == len(received) == 1
    assert assumed[0]["RoleArn"] == adapter.role_arn and assumed[0]["ExternalId"] == "synthetic-external-id"
    assert received[0]["credentials"].session_token == "synthetic-session-token-0123456789"
    assert received[0]["tenant_id"] == "customer-a"
    assert "synthetic-external-id" not in repr(adapter)


def test_role_failure_is_known_local_refusal_without_a_model_attempt(monkeypatch, caplog):
    def fail(*args, **kwargs):
        raise RuntimeError("PRIVATE upstream account and credential detail")
    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=fail))
    monkeypatch.setitem(sys.modules, "botocore.config", SimpleNamespace(Config=lambda **kwargs: SimpleNamespace(**kwargs)))
    adapter = runtime._RoleBedrockAdapter(tenant_id="customer-a", connection_id="approved", connection_version="v1",
        region="us-east-1", role_arn="arn:aws:iam::111122223333:role/synthetic", allowed_models=("amazon.synthetic-v1:0",))
    result = adapter.invoke(adapters.ApprovedModelCall("Synthetic", "amazon.synthetic-v1:0", 64, 4))
    assert result.outcome == "rejected" and result.reason_code == "configuration_unavailable"
    assert "PRIVATE" not in repr(result) and "PRIVATE" not in caplog.text


def test_ledger_region_endpoint_and_proxy_policy_are_explicit(environment, monkeypatch):
    monkeypatch.setenv("AWS_ENDPOINT_URL_DYNAMODB", "https://synthetic-unapproved.invalid")
    monkeypatch.setenv("HTTPS_PROXY", "https://synthetic-proxy.invalid")
    result = runtime.from_environment()
    try:
        assert result.reason_code == "ready"
        _, kwargs = environment[0]
        assert kwargs["endpoint_url"] == "https://dynamodb.us-east-1.amazonaws.com"
        assert kwargs["region_name"] == "us-east-1" and kwargs["verify"] is True
        assert kwargs["config"].proxies == {} and kwargs["config"].ignore_configured_endpoint_urls is True
    finally:
        result.gateway.workers.close()


def test_sts_setup_cannot_start_model_after_deadline(monkeypatch):
    clock, models, clients = [0.0], [], []
    monkeypatch.setattr(runtime, "monotonic", lambda: clock[0])
    def assume(**kwargs):
        clock[0] = 5.0
        return {"Credentials": {"AccessKeyId": "ASIASYNTHETIC12345678", "SecretAccessKey": "synthetic-secret-value-0123456789",
                                "SessionToken": "synthetic-session-token-0123456789",
                                "Expiration": datetime.now(timezone.utc) + timedelta(minutes=15)}}
    def client(service, **kwargs):
        clients.append(kwargs)
        return SimpleNamespace(assume_role=assume, close=lambda: clients.append("closed"))
    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=client))
    monkeypatch.setitem(sys.modules, "botocore.config", SimpleNamespace(Config=lambda **kwargs: SimpleNamespace(**kwargs)))
    monkeypatch.setattr(adapters, "BedrockConverseAdapter", lambda **kwargs: SimpleNamespace(invoke=lambda call: models.append(call)))
    adapter = runtime._RoleBedrockAdapter(tenant_id="customer-a", connection_id="approved", connection_version="v1",
        region="us-east-1", role_arn="arn:aws:iam::111122223333:role/synthetic", allowed_models=("amazon.synthetic-v1:0",))
    result = adapter.invoke(adapters.ApprovedModelCall("Synthetic", "amazon.synthetic-v1:0", 64, 4))
    assert result.outcome == "rejected" and result.reason_code == "configuration_unavailable"
    assert models == [] and clients[-1] == "closed"
    assert clients[0]["endpoint_url"] == "https://sts.us-east-1.amazonaws.com"
    assert clients[0]["config"].proxies == {} and clients[0]["config"].ignore_configured_endpoint_urls is True
