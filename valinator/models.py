"""Strict contracts shared by the API, policy engine, and adapters."""

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Action(StrEnum):
    ALLOW = "allow"
    FLAG = "flag"
    REDACT = "redact"
    BLOCK = "block"


class Stage(StrEnum):
    INPUT = "input"
    OUTPUT = "output"


class CheckRequest(Contract):
    application_id: str = Field(min_length=1, max_length=100)
    policy_version: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=32_000)
    language: Literal["en"] = "en"
    # One canonical content value is used by every checker.
    content_format: Literal["text", "json"] = "text"
    request_context: str | None = Field(default=None, max_length=8_000)


class Finding(Contract):
    rule_id: str
    category: str
    severity: Literal["low", "medium", "high"]
    explanation: str
    # Never include source text or provider exception messages in findings.


class CheckerResult(Contract):
    checker_id: str
    checker_version: str
    status: Literal["passed", "flagged", "error", "skipped", "unsupported"]
    findings: tuple[Finding, ...] = ()
    latency_ms: float = 0
    error_code: str | None = None
    simulated: bool = False
    external: bool = False
    # Private intermediate value: never serialized as a checker finding/result.
    transformed_content: str | None = Field(default=None, exclude=True)


class Rule(Contract):
    checker_id: str
    required: bool = True
    on_finding: Action = Action.FLAG
    stages: tuple[Stage, ...] = Field(default=(Stage.INPUT, Stage.OUTPUT), min_length=1)
    on_input_finding: Action | None = None
    on_output_finding: Action | None = None


class FieldComparison(Contract):
    rule_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    left: str = Field(min_length=1, max_length=200)
    right: str = Field(min_length=1, max_length=200)
    operator: Literal["lt", "le", "eq", "ge", "gt", "ne"]
    value_type: Literal["number", "date"] = "number"
    stages: tuple[Stage, ...] = Field(default=(Stage.INPUT, Stage.OUTPUT), min_length=1)

    @field_validator("left", "right")
    @classmethod
    def pointer(cls, value):
        import re
        if not value.startswith("/") or re.search(r"~(?![01])", value):
            raise ValueError("Expected an absolute JSON Pointer")
        return value


class RequiredSection(Contract):
    rule_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    label: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9 _-]{0,79}$")
    min_characters: int = Field(default=1, ge=1, le=8_000)
    stages: tuple[Stage, ...] = Field(default=(Stage.INPUT, Stage.OUTPUT), min_length=1)

    @field_validator("label")
    @classmethod
    def trimmed_label(cls, value):
        if value != value.strip():
            raise ValueError("Section labels must not have surrounding whitespace")
        return value


class ConfidentialTerm(Contract):
    rule_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    term: str = Field(min_length=3,max_length=200)
    stages: tuple[Stage, ...] = Field(default=(Stage.INPUT,Stage.OUTPUT),min_length=1)

    @field_validator('term')
    @classmethod
    def meaningful(cls,value):
        if value != value.strip() or len(value.strip()) < 3:
            raise ValueError('Confidential terms require at least three non-padding characters')
        return value


class MatchedOutcomes(Contract):
    attributes: tuple[Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,49}$", strict=True)], ...] = Field(min_length=1, max_length=8)
    outcome_type: Literal["string", "boolean", "number"] = "string"

    @field_validator("attributes")
    @classmethod
    def unique_attributes(cls, value):
        if len({name.casefold() for name in value}) != len(value):
            raise ValueError("Matched attributes must be distinct")
        return value


class EvidenceSource(Contract):
    source_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$", strict=True)
    text: str = Field(min_length=1, max_length=8_000, strict=True)

    @field_validator("text")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("A configured evidence source must not be blank")
        return value


class EvidencePolicy(Contract):
    sources: tuple[EvidenceSource, ...] = Field(min_length=1, max_length=20)
    min_quote_characters: int = Field(default=20, ge=1, le=1_000, strict=True)

    @field_validator("sources")
    @classmethod
    def unique_sources(cls, value):
        if len({source.source_id for source in value}) != len(value):
            raise ValueError("Evidence source identifiers must be distinct")
        return value


class ToolDefinition(Contract):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,49}$", strict=True)
    arguments_schema: dict[str, Any]


class ToolPolicy(Contract):
    tools: tuple[ToolDefinition, ...] = Field(min_length=1, max_length=20)

    @field_validator("tools")
    @classmethod
    def unique_tools(cls, value):
        if len({tool.name for tool in value}) != len(value):
            raise ValueError("Configured tool names must be distinct")
        return value


class LedgerPolicy(Contract):
    currency: str = Field(pattern=r"^[A-Z]{3}$", min_length=3, max_length=3, strict=True)
    decimal_places: int = Field(default=2, ge=0, le=4, strict=True)
    max_entries: int = Field(default=100, ge=2, le=200, strict=True)


class TimestampPolicy(Contract):
    allow_equal: bool = Field(default=False, strict=True)
    max_samples: int = Field(default=200, ge=2, le=500, strict=True)


class GraphPolicy(Contract):
    max_nodes: int = Field(default=100, ge=1, le=200, strict=True)
    max_edges: int = Field(default=500, ge=1, le=1_000, strict=True)


class IntegrityPolicy(Contract):
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$", min_length=64, max_length=64, strict=True)
    encoding: Literal["utf8", "base64"] = "utf8"
    max_bytes: int = Field(default=16_384, ge=1, le=262_144, strict=True)

    @field_validator("sha256")
    @classmethod
    def normalized_digest(cls, value):
        return value.lower()


class Policy(Contract):
    application_id: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)
    mode: Literal["observe", "enforce"] = "observe"
    rules: tuple[Rule, ...]
    timeout_ms: int = Field(default=250, ge=1, le=5_000)
    failure_action: Literal["block", "flag"] = "block"
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    allow_simulated_enforcement: bool = False
    external_checks: Literal["disabled", "after_local_pass"] = "disabled"
    comparisons: tuple[FieldComparison, ...] = Field(default=(), max_length=50)
    required_sections: tuple[RequiredSection, ...] = Field(default=(), max_length=50)
    detect_labeled_sensitive_data: bool = Field(default=False, strict=True)
    detect_secret_assignments: bool = Field(default=False,strict=True)
    inspect_encoded_secrets: bool = Field(default=False,strict=True)
    confidential_terms: tuple[ConfidentialTerm, ...] = Field(default=(),max_length=50)
    matched_outcomes: MatchedOutcomes | None = None
    evidence: EvidencePolicy | None = None
    tool_calls: ToolPolicy | None = None
    ledger: LedgerPolicy | None = None
    timestamps: TimestampPolicy | None = None
    graph: GraphPolicy | None = None
    integrity: IntegrityPolicy | None = None


class GroupResult(Contract):
    group_id: str
    name: str
    status: Literal["passed", "findings", "incomplete", "simulated", "partial", "disabled", "not_applicable", "not_implemented"]
    checker_ids: tuple[str, ...] = ()
    limitations: str


class RedactionResult(Contract):
    status: Literal["not_requested", "not_needed", "preview", "ready", "withheld"] = "not_requested"
    content: str | None = None
    explanation: str = "No redaction was requested by the policy."
    validation_results: tuple[CheckerResult, ...] = ()


class CheckResponse(Contract):
    check_id: str
    application_id: str
    policy_version: str
    stage: Stage
    mode: str
    evaluation_status: Literal["complete", "incomplete"]
    recommended_action: Action
    effective_action: Action
    findings: tuple[Finding, ...]
    checker_results: tuple[CheckerResult, ...]
    total_latency_ms: float
    cost_usd: float | None = 0
    cost_basis: Literal["local_only", "provider_unpriced"] = "local_only"
    simulated: bool
    groups: tuple[GroupResult, ...] = ()
    redaction: RedactionResult = Field(default_factory=RedactionResult)
