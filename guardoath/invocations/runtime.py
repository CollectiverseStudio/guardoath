"""Explicit, disabled-by-default hosted configuration for model invocation.

Configuration is trusted deployment state, never customer request authority.
Loading an unconfigured runtime makes no AWS/model request and changes no tenant.
Provider secret values and role/external-id configuration are never in catalogs.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
from threading import Lock
from time import monotonic
from typing import Annotated, Literal

from pydantic import Field, model_validator

from guardoath.json_data import parse_json
from guardoath.models import Contract
from .contracts import ConnectionInfo


SECRET_ENV_PATTERN = r"^GUARDOATH_PROVIDER_SECRET_[A-Z0-9_]{1,80}$"


class OpenAICredential(Contract):
    kind: Literal["openai_key"]
    secret_env: str = Field(pattern=SECRET_ENV_PATTERN, strict=True)


class AWSRoleCredential(Contract):
    kind: Literal["aws_role"]
    role_arn: str = Field(pattern=r"^arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9_+=,.@/-]{1,512}$", strict=True)
    external_id_env: str | None = Field(default=None, pattern=SECRET_ENV_PATTERN, strict=True)


class ConfiguredConnection(Contract):
    application_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,39}$", strict=True)
    info: ConnectionInfo
    credentials: Annotated[OpenAICredential | AWSRoleCredential, Field(discriminator="kind")]

    @model_validator(mode="after")
    def provider_credentials(self):
        if self.info.provider == "aws_bedrock":
            if (not isinstance(self.credentials, AWSRoleCredential) or not self.info.region or
                    self.info.region.startswith(("cn-", "us-gov-", "us-iso"))):
                raise ValueError("Bedrock requires its bound role and region")
        elif not isinstance(self.credentials, OpenAICredential) or self.info.region is not None:
            raise ValueError("OpenAI requires its bound key and no AWS region")
        return self


@dataclass(frozen=True)
class InvocationRuntime:
    gateway: object | None = None
    reason_code: str = "runtime_unconfigured"


@dataclass
class _RoleBedrockAdapter:
    """Cache only this approved role's temporary credential binding.

    The Lambda host identity may assume exactly the configured role; it is never
    silently used as a customer's Bedrock inference identity.
    """
    tenant_id: str
    connection_id: str
    connection_version: str
    region: str
    role_arn: str = field(repr=False)
    external_id: str | None = field(default=None, repr=False)
    allowed_models: tuple[str, ...] = ()
    max_output_tokens: int = 4096
    _adapter: object | None = field(default=None, init=False, repr=False)
    _expires: float = field(default=0, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def invoke(self, call):
        from .adapters import ApprovedModelCall, AwsCredentials, BedrockConverseAdapter, ProviderResult
        deadline = monotonic() + call.timeout_seconds
        locked = False
        model_started = False
        try:
            locked = self._lock.acquire(timeout=max(0, deadline - monotonic()))
            if not locked:
                return ProviderResult(outcome="rejected", reason_code="configuration_unavailable")
            try:
                if self._adapter is None or self._expires <= datetime.now(timezone.utc).timestamp() + 60:
                    import boto3
                    from botocore.config import Config
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        return ProviderResult(outcome="rejected", reason_code="configuration_unavailable")
                    connect = min(0.5, remaining / 4)
                    config = Config(connect_timeout=connect, read_timeout=min(1.0, remaining / 2),
                                    retries={"mode": "standard", "total_max_attempts": 1}, max_pool_connections=2,
                                    proxies={}, ignore_configured_endpoint_urls=True)
                    client = boto3.client("sts", region_name=self.region,
                                         endpoint_url=f"https://sts.{self.region}.amazonaws.com", verify=True, config=config)
                    try:
                        if deadline - monotonic() <= 0:
                            return ProviderResult(outcome="rejected", reason_code="configuration_unavailable")
                        value = client.assume_role(RoleArn=self.role_arn,
                            RoleSessionName="guardoath-model-invocation", DurationSeconds=900,
                            **({"ExternalId": self.external_id} if self.external_id else {}))
                    finally:
                        client.close()
                    credentials = value["Credentials"]
                    expiration = credentials["Expiration"]
                    if not isinstance(expiration, datetime) or expiration.tzinfo is None:
                        raise ValueError("Unverified credential expiry")
                    expiry = expiration.timestamp()
                    if expiry <= datetime.now(timezone.utc).timestamp() + 60:
                        raise ValueError("Expired credential binding")
                    adapter = BedrockConverseAdapter(tenant_id=self.tenant_id,
                        connection_id=self.connection_id, connection_version=self.connection_version,
                        region=self.region, allowed_models=self.allowed_models,
                        max_output_tokens=self.max_output_tokens,
                        credentials=AwsCredentials(credentials["AccessKeyId"], credentials["SecretAccessKey"],
                                                   credentials["SessionToken"]))
                    self._adapter, self._expires = adapter, expiry
                adapter = self._adapter
            finally:
                self._lock.release()
                locked = False
            remaining = deadline - monotonic()
            if remaining <= 0:
                return ProviderResult(outcome="rejected", reason_code="configuration_unavailable")
            model_started = True
            return adapter.invoke(ApprovedModelCall(call.text, call.model_id, call.max_output_tokens, remaining))
        except Exception:
            # Even a role failure after durable intent retains the conservative
            # reservation. Never expose role IDs, SDK exceptions or credentials.
            return ProviderResult(outcome="unknown", reason_code="provider_unavailable") if model_started else \
                ProviderResult(outcome="rejected", reason_code="configuration_unavailable")
        finally:
            if locked:
                self._lock.release()


def from_environment() -> InvocationRuntime:
    enabled = os.getenv("GUARDOATH_INVOCATIONS_ENABLED", "0")
    if enabled == "0":
        return InvocationRuntime()
    if enabled != "1":
        return InvocationRuntime(reason_code="runtime_invalid")
    try:
        import re
        from .adapters import OpenAIResponsesAdapter
        from .service import ConnectionBinding, InvocationGateway
        from .store import DynamoDBInvocationStore
        table = os.environ["GUARDOATH_INVOCATION_TABLE"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,255}", table):
            raise ValueError("Invalid invocation table")
        region = os.environ["GUARDOATH_INVOCATION_REGION"]
        if (not re.fullmatch(r"(?:us|eu|ap|sa|ca|me|af|il|mx)(?:-[a-z]+){1,3}-[1-9][0-9]?", region) or
                region.startswith(("us-gov-", "us-iso"))):
            raise ValueError("Invalid approved invocation region")
        raw_key = os.environ["GUARDOATH_INVOCATION_SIGNING_KEY"]
        if len(raw_key) > 256:
            raise ValueError("Invalid signing key")
        signing_key = base64.b64decode(raw_key, validate=True)
        if not 32 <= len(signing_key) <= 64:
            raise ValueError("Invalid signing key")
        raw_connections = os.environ["GUARDOATH_AI_CONNECTIONS_JSON"]
        if len(raw_connections.encode("utf-8")) > 32_000:
            raise ValueError("Connection registry too large")
        values = parse_json(raw_connections)
        if not isinstance(values, list) or not 1 <= len(values) <= 20:
            raise ValueError("Invalid connection registry")
        configs = tuple(ConfiguredConnection.model_validate(v) for v in values)
        bindings = []
        for config in configs:
            info, credential = config.info, config.credentials
            common = dict(tenant_id=config.application_id, connection_id=info.id,
                          connection_version=info.version,
                          allowed_models=tuple(m.provider_model_id for m in info.models),
                          max_output_tokens=max(m.max_output_tokens for m in info.models))
            if isinstance(credential, OpenAICredential):
                secret = os.environ[credential.secret_env]
                adapter = OpenAIResponsesAdapter(api_key=secret, **common)
            else:
                # Validate model/resource kinds and region without a provider
                # call; the adapter will bind temporary credentials at dispatch.
                from .adapters import validate_bedrock_targets
                validate_bedrock_targets(info.region, common["allowed_models"])
                external_id = os.environ[credential.external_id_env] if credential.external_id_env else None
                if external_id is not None and (not 2 <= len(external_id) <= 1224 or
                        not re.fullmatch(r"[A-Za-z0-9_+=,.@:/-]+", external_id)):
                    raise ValueError("Invalid external ID")
                adapter = _RoleBedrockAdapter(region=info.region, role_arn=credential.role_arn,
                                             external_id=external_id, **common)
            bindings.append(ConnectionBinding(config.application_id, info, adapter))
        import boto3
        from botocore.config import Config
        client = boto3.client("dynamodb", region_name=region,
            endpoint_url=f"https://dynamodb.{region}.amazonaws.com", verify=True,
            config=Config(connect_timeout=0.5, read_timeout=1.0, proxies={}, ignore_configured_endpoint_urls=True,
                retries={"total_max_attempts": 1, "mode": "standard"}, max_pool_connections=4))
        store = DynamoDBInvocationStore(table, client, signing_key)
        return InvocationRuntime(InvocationGateway(store=store, connections=bindings), "ready")
    except Exception:
        # Fail closed for invocation while leaving existing validation available.
        return InvocationRuntime(reason_code="runtime_invalid")
