"""Authenticated attachment checks on actual bounded bytes, using the existing Engine.

The API envelope is Base64 because the existing ingress is strict JSON, but only decoded,
server-received bytes activate the file probe. User-supplied names and MIME are ignored.
This module recognizes *only* structural UTF-8 CSV. Its cells use the existing csv_formula checker and tenant-enabled prompt_attack, secrets and optional local pii checkers, not a semantic file parser.
File redaction is not supported: a tenant PII Redact finding blocks the whole file request, including observe mode.
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

    The tenant controls stage and rule actions; the shared Engine executes CSV and enabled text security.
    An unsupported or invalid attachment is never reported clean. Every result is
    privacy-safe and format probes do not pretend to scan PDF/Office files.
    """
    if not 1 <= len(contents) <= MAX_FILES_PER_CHECK:
        raise ValueError("Attachment count exceeded")
    mode = record.input_attachment_csv_mode if stage == Stage.INPUT else record.output_attachment_csv_mode
    stage_action = record.action("csv_formula", stage) or Action.BLOCK
    # Recognized CSV bytes are lower-trust model input. If the tenant enabled
    # prompt_attack for this stage, run that SAME checker/rules on file text too.
    # Never enable a tenant-disabled check or bypass an explicit attachment Off.
    prompt_action = record.action("prompt_attack", stage)
    secrets_action = record.action("secrets", stage)
    pii_action = record.action("pii", stage)
    file_rules = [Rule(checker_id="csv_formula", on_finding=stage_action, stages=(stage,))]
    selected_checkers = {"csv_formula": checkers["csv_formula"]}
    if prompt_action is not None:
        file_rules.append(Rule(checker_id="prompt_attack", on_finding=prompt_action, stages=(stage,)))
        selected_checkers["prompt_attack"] = checkers["prompt_attack"]
    if secrets_action is not None:
        file_rules.append(Rule(checker_id="secrets", on_finding=secrets_action, stages=(stage,)))
        selected_checkers["secrets"] = checkers["secrets"]
    if pii_action is not None:
        # File bytes have no safe transformed-file return contract. A tenant Redact
        # policy must scan the CSV but BLOCK if a PII finding is present rather
        # than returning a fabricated/redacted chat-only preview. Do not modify
        # the tenant record or weaken its ordinary message redaction behavior.
        file_rules.append(Rule(checker_id="pii", on_finding=(Action.BLOCK if pii_action == Action.REDACT
                                                             else pii_action), stages=(stage,)))
        # Missing optional local PII dependencies yield checker_unavailable and
        # the existing Engine's required-check/incomplete fail-closed pathway.
        selected_checkers["pii"] = checkers.get("pii")
    allowed_rule_ids = {"csv_formula.potential_formula"}
    rule_actions = {
        rule_id: action for rule_id, action in record.rule_actions_on(stage).items()
        if rule_id in allowed_rule_ids or
        (prompt_action is not None and rule_id.startswith("prompt_attack.")) or
        (secrets_action is not None and (rule_id.startswith("secrets.") or rule_id.startswith("confidential."))) or
        (pii_action is not None and rule_id.startswith("pii.") and not rule_id.startswith("pii.encoded_"))
    }
    # The credential checker uses these exact tenant settings for ordinary text as
    # well. The file Engine has ONE active stage: filter and scope each term's
    # stages to that stage, without mutating the authoritative tenant record.
    # Do not change assignment/encoded/configured-term matching behavior for files.
    secret_settings = ({"detect_secret_assignments": record.detect_secret_assignments,
                        "inspect_encoded_secrets": record.inspect_encoded_secrets,
                        "confidential_terms": tuple(term.model_copy(update={"stages": (stage,)})
                                                    for term in record.confidential_terms
                                                    if stage in term.stages)}
                       if secrets_action is not None else {})
    privacy_settings = ({"detect_labeled_sensitive_data": record.detect_labeled_sensitive_data,
                         "privacy_patterns": record.privacy_patterns,
                         "disabled_privacy_entities": record.disabled_privacy_entities,
                         "privacy_min_score": record.privacy_min_score}
                        if pii_action is not None else {})
    policy = Policy(
        application_id=record.tenant_id, version=TENANT_POLICY_VERSION,
        mode=record.mode, timeout_ms=record.timeout_ms, failure_action=record.failure_action,
        rules=tuple(file_rules),
        input_rule_actions=rule_actions if stage == Stage.INPUT else {},
        output_rule_actions=rule_actions if stage == Stage.OUTPUT else {},
        **secret_settings,
        **privacy_settings,
    )
    engine = Engine([policy], selected_checkers) if mode == "auto" else None
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
            # Format, prompt and credential checks all use the existing Engine
            # and authoritative per-stage tenant rule actions. Retain all results.
            results.extend(evaluated.checker_results)
            findings.extend(evaluated.findings)
            actions.append(evaluated.recommended_action)
            # A PII Redact finding on a CSV requires a transformed FILE, which
            # this API cannot return or validate. No false clean/redacted claim.
            # The file must be withheld even in observe mode; non-PII rules and
            # all PII findings are still retained by the same Engine.
            unredactable_pii = (pii_action == Action.REDACT and
                                any(result.checker_id == "pii" and result.status == "flagged"
                                    for result in evaluated.checker_results))
            if unredactable_pii:
                actions.append(Action.BLOCK)
                incomplete = True
                receipt["reason"] = "attachment_redaction_unavailable"
            if evaluated.evaluation_status == "incomplete":
                # A partial file inspection is not safe to pass even if the
                # customer's ordinary text-check failure_action is Flag.
                actions.append(Action.BLOCK)
                incomplete = True
            receipt["evaluation_status"] = ("incomplete" if unredactable_pii else evaluated.evaluation_status)
            receipts.append(receipt)
            continue
        results.append(result)
        receipts.append(receipt)
    return AttachmentBatch(tuple(receipts), tuple(results), tuple(findings),
                           max(actions, key=PRIORITY.__getitem__), incomplete)
