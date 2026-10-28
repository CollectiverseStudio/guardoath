"""Strict contracts shared by the API, policy engine, and adapters."""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Action(StrEnum):
    ALLOW = "allow"
    FLAG = "flag"
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


class Rule(Contract):
    checker_id: str
    required: bool = True
    on_finding: Action = Action.FLAG


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
    cost_usd: float = 0
    cost_basis: Literal["local_only"] = "local_only"
    simulated: bool
