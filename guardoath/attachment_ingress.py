"""Authenticated attachment checks on actual bounded bytes, using the existing Engine.

The API envelope is Base64 because the existing ingress is strict JSON, but only decoded,
server-received bytes activate the file probe. User-supplied names and MIME are ignored.
This module supports *only* structural UTF-8 CSV + the existing csv_formula checker.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Annotated

from pydantic import Field, field_validator

from guardoath.attachment_probe import MAX_ATTACHMENT_BYTES, classify_attachment
from guardoath.engine import Engine, PRIORITY
from guardoath.models import (Action, ApiCheckRequest, CheckRequest, CheckerResult, Finding,
                             Policy, Rule, Stage)
from guardoath.tenant_record import TENANT_POLICY_VERSION, TenantRecord

MAX_FILES_PER_CHECK = 3
# Allows a slightly over-budget attachment to be classified invalid, never forwarded.
MAX_ENCODED_CHARS = 45_000


class AttachmentCheckRequest(ApiCheckRequest):
    """One normal chat check plus real Base64 file bytes in the same authenticated call."""

    attachments_b64: tuple[Annotated[str, Field(min_length=1, max_length=MAX_ENCODED_CHARS)], ...] = Field(
        min_length=1, max_length=MAX_FILES_PER_CHECK
    )

    @field_validator("attachments_b64")
    @classmethod
    def strict_base64(cls, values):
        for value in values:
            try:
                base64.b64decode(value, validate=True)
            except (ValueError, binascii.Error, UnicodeError):
                raise ValueError("Invalid Base64 attachment") from None
        return values

    def attachment_bytes(self) -> tuple[bytes, ...]:
        return tuple(base64.b64decode(value, validate=True) for value in self.attachments_b64)


@dataclass(frozen=True)
class AttachmentBatch:
    """Safe file receipts + the existing checker findings and action decision."""

    receipts: tuple[dict, ...]
    checker_results: tuple[CheckerResult, ...]
    findings: tuple[Finding, ...]
    recommended_action: Action
    incomplete: bool


async def inspect_attachments(record: TenantRecord, stage: Stage, contents: tuple[bytes, ...], checkers: dict) -> AttachmentBatch:
    """Inspect every file without treating names, MIME or message text as bytes.

    The tenant controls stage and rule actions; the shared Engine executes csv_formula.
    An unsupported or invalid attachment is never reported clean. Every result is
    privacy-safe and format probes do not pretend to scan PDF/Office files.
    """
    if not 1 <= len(contents) <= MAX_FILES_PER_CHECK:
        raise ValueError("Attachment count exceeded")
    mode = record.input_attachment_csv_mode if stage == Stage.INPUT else record.output_attachment_csv_mode
    stage_action = record.action("csv_formula", stage) or Action.BLOCK
    own = record.rule_actions_on(stage).get("csv_formula.potential_formula")
    rule_actions = {"csv_formula.potential_formula": own} if own else {}
    policy = Policy(
        application_id=record.tenant_id, version=TENANT_POLICY_VERSION,
        mode=record.mode, timeout_ms=record.timeout_ms, failure_action=record.failure_action,
        rules=(Rule(checker_id="csv_formula", on_finding=stage_action, stages=(stage,)),),
        input_rule_actions=rule_actions if stage == Stage.INPUT else {},
        output_rule_actions=rule_actions if stage == Stage.OUTPUT else {},
    )
    engine = Engine([policy], {"csv_formula": checkers["csv_formula"]}) if mode == "auto" else None
    receipts = []
    results = []
    findings = []
    actions = [Action.ALLOW]
    incomplete = False
    for index, raw in enumerate(contents):
        probe = classify_attachment(raw)
        receipt = {"index": index, "kind": probe.kind, "status": probe.status,
                   "byte_count": probe.byte_count, "sha256": probe.sha256, "reason": probe.reason}
        if probe.status != "recognized":
            # Unsupported, malformed and budget-exceeded formats fail closed, not falsely 'passed'.
            result = CheckerResult(checker_id="attachment_probe", checker_version="1",
                                   status="unsupported" if probe.status == "unsupported" else "error",
                                   error_code=probe.reason)
            actions.append(Action.BLOCK)
            incomplete = True
            receipt["evaluation_status"] = "incomplete"
        elif mode == "off":
            # Explicit tenant Off is authoritative: classification is not inspection.
            result = CheckerResult(checker_id="csv_formula", checker_version="1", status="skipped",
                                   error_code="attachment_policy_off")
            receipt["evaluation_status"] = "not_inspected_policy_off"
        else:
            request = CheckRequest(application_id=record.tenant_id, policy_version=TENANT_POLICY_VERSION,
                                   content=raw.decode("utf-8-sig"), content_format="text")
            evaluated = await engine.check(request, stage)
            # A recognized format is not equivalent to passing a security check.
            result = evaluated.checker_results[0]
            findings.extend(evaluated.findings)
            actions.append(evaluated.recommended_action)
            if evaluated.evaluation_status == "incomplete":
                incomplete = True
            receipt["evaluation_status"] = evaluated.evaluation_status
        results.append(result)
        receipts.append(receipt)
    return AttachmentBatch(tuple(receipts), tuple(results), tuple(findings),
                           max(actions, key=PRIORITY.__getitem__), incomplete)
