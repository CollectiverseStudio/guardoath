"""Public invocation contracts and the content-free durable receipt schema.

Provider credentials, prompts, answers, full policies and provider error bodies
are deliberately absent from the receipt. Only the original POST can return a
checked answer; retrying a request returns its receipt without regenerating it.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from guardoath.models import Action, Contract


ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$"
DIGEST_PATTERN = r"^[0-9a-f]{64}$"
MAX_INPUT_BYTES = 16_384
MAX_OUTPUT_BYTES = 32_000


class ModelTarget(Contract):
    kind: Literal["model"] = "model"
    connection_id: str = Field(pattern=ID_PATTERN, strict=True)
    model_id: str = Field(pattern=ID_PATTERN, strict=True,
                          description="An approved model alias from GET /v1/connections; not an arbitrary provider model or ARN.")


class InvocationRequest(Contract):
    application_id: str = Field(min_length=1, max_length=100, strict=True)
    request_id: str = Field(pattern=ID_PATTERN, strict=True,
                            description="Unique caller idempotency identifier. Reuse it only for the identical request; retries return a receipt without another generation.")
    target: ModelTarget
    message: str = Field(min_length=1, max_length=MAX_INPUT_BYTES, strict=True)
    max_output_tokens: int = Field(ge=1, le=4096, strict=True)

    @field_validator("message")
    @classmethod
    def bounded_utf8(cls, value):
        if len(value.encode("utf-8")) > MAX_INPUT_BYTES:
            raise ValueError("Input exceeds the byte limit")
        return value


class TargetSnapshot(Contract):
    connection_id: str = Field(pattern=ID_PATTERN, strict=True)
    connection_version: str = Field(pattern=ID_PATTERN, strict=True)
    provider: Literal["aws_bedrock", "openai"]
    region: str | None = Field(default=None, max_length=40, strict=True)
    model_id: str = Field(pattern=ID_PATTERN, strict=True)
    provider_model_id: str = Field(min_length=1, max_length=1024, strict=True)


class CheckerEvidence(Contract):
    checker_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}$", strict=True)
    checker_version: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}$", strict=True)
    status: Literal["passed", "flagged", "error", "skipped", "unsupported"]


class CheckEvidence(Contract):
    check_id: str = Field(min_length=1, max_length=100, strict=True)
    decision: Action
    evaluation_status: Literal["complete", "incomplete"]
    simulated: bool = Field(strict=True)
    checker_ids: tuple[str, ...] = Field(max_length=64)
    finding_ids: tuple[str, ...] = Field(max_length=128)
    findings_truncated: bool = Field(default=False, strict=True)
    checker_results: tuple[CheckerEvidence, ...] = Field(default=(), max_length=64)

    @field_validator("checker_ids", "finding_ids")
    @classmethod
    def bounded_identifiers(cls, values):
        import re
        if any(not isinstance(x, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", x) for x in values):
            raise ValueError("Invalid check evidence identifier")
        return values


class ReportedUsage(Contract):
    source: Literal["provider_reported"] = "provider_reported"
    input_tokens: int | None = Field(default=None, ge=0, le=10_000_000, strict=True)
    output_tokens: int | None = Field(default=None, ge=0, le=10_000_000, strict=True)


InvocationStatus = Literal[
    "admitted", "input_checked", "dispatch_intent", "provider_result",
    "completed", "input_blocked", "input_incomplete", "output_blocked",
    "output_incomplete", "provider_withheld", "provider_rejected",
    "outcome_unknown", "quota_exceeded", "unavailable",
]
TERMINAL_STATUSES = frozenset({
    "completed", "input_blocked", "input_incomplete", "output_blocked",
    "output_incomplete", "provider_withheld", "provider_rejected",
    "outcome_unknown", "quota_exceeded", "unavailable",
})


class InvocationReceipt(Contract):
    schema_version: Literal[1] = 1
    application_id: str = Field(min_length=1, max_length=100, strict=True)
    interaction_id: str = Field(pattern=r"^[0-9a-f]{32}$", strict=True)
    request_id: str = Field(pattern=ID_PATTERN, strict=True)
    created_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", strict=True)
    updated_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", strict=True)
    status: InvocationStatus
    reason_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$", strict=True)
    target: TargetSnapshot
    policy_revision: int = Field(ge=1, strict=True)
    policy_digest: str = Field(pattern=DIGEST_PATTERN, strict=True)
    catalog_version: str = Field(min_length=1, max_length=100, strict=True)
    input_check: CheckEvidence | None = None
    output_check: CheckEvidence | None = None
    dispatch_state: Literal["not_dispatched", "intent_recorded", "response_observed", "outcome_unknown"] = "not_dispatched"
    provider_request_id: str | None = Field(default=None, max_length=200, strict=True)
    provider_response_id: str | None = Field(default=None, max_length=200, strict=True)
    observed_model: str | None = Field(default=None, max_length=1024, strict=True)
    observed_service_tier: str | None = Field(default=None, max_length=32, strict=True)
    usage: ReportedUsage | None = None
    reserved_output_tokens: int = Field(default=0, ge=0, le=4096, strict=True)
    quota_period: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$", strict=True)
    response_retained: Literal[False] = False
    sequence: int = Field(default=0, ge=0, le=20, strict=True)
    event_digest: str = Field(default="0" * 64, pattern=DIGEST_PATTERN, strict=True)

    @model_validator(mode="after")
    def coherent_terminal(self):
        if self.status == "completed" and (
            self.dispatch_state != "response_observed" or self.input_check is None or
            self.output_check is None or self.output_check.evaluation_status != "complete" or
            self.output_check.simulated or self.output_check.decision == Action.BLOCK
        ):
            raise ValueError("Completion requires checked output and an observed provider response")
        return self


class InvocationResponse(Contract):
    receipt: InvocationReceipt
    response: str | None = Field(default=None, max_length=MAX_OUTPUT_BYTES, strict=True,
                                 description="Only checked text from this original request. Receipts and retries never retain or regenerate it.")
    replayed: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def released_only_when_complete(self):
        if self.response is not None and (self.replayed or self.receipt.status != "completed"):
            raise ValueError("A response is available only with an original completed invocation")
        return self


class ModelInfo(Contract):
    id: str = Field(pattern=ID_PATTERN, strict=True)
    label: str = Field(min_length=1, max_length=120, strict=True)
    provider_model_id: str = Field(min_length=1, max_length=1024, strict=True)
    max_output_tokens: int = Field(ge=1, le=4096, strict=True)


class ConnectionInfo(Contract):
    id: str = Field(pattern=ID_PATTERN, strict=True)
    version: str = Field(pattern=ID_PATTERN, strict=True)
    label: str = Field(min_length=1, max_length=120, strict=True)
    provider: Literal["aws_bedrock", "openai"]
    region: str | None = Field(default=None, max_length=40, strict=True)
    models: tuple[ModelInfo, ...] = Field(min_length=1, max_length=20)
    monthly_call_limit: int = Field(ge=1, le=1_000_000, strict=True)
    monthly_output_token_limit: int = Field(ge=1, le=1_000_000_000, strict=True)


class InvocationCapabilities(Contract):
    buffered_text: Literal[True] = True
    agents: Literal[False] = False
    tools: Literal[False] = False
    streaming: Literal[False] = False
    files: Literal[False] = False
    response_retention: Literal[False] = False


class ConnectionCatalog(Contract):
    application_id: str = Field(min_length=1, max_length=100, strict=True)
    available: bool = Field(strict=True)
    reason_code: Literal["ready", "runtime_unconfigured", "runtime_invalid", "demo_not_enabled", "subscription_required", "enforcement_required", "no_connections"]
    capabilities: InvocationCapabilities = InvocationCapabilities()
    max_input_bytes: int = MAX_INPUT_BYTES
    connections: tuple[ConnectionInfo, ...] = Field(default=(), max_length=20)


class ReceiptList(Contract):
    items: tuple[InvocationReceipt, ...] = Field(max_length=20)
