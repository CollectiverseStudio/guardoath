"""Execute approved checks with deadlines and explicit failure decisions."""

import asyncio
from copy import deepcopy
from time import perf_counter
from uuid import uuid4

from jsonschema import Draft202012Validator

from valinator.checkers import Checker
from valinator.config import validate_local_schema
from valinator.formats import validate_schema_formats
from valinator.groups import GROUPS, group_results
from valinator.models import Action, CheckerResult, CheckRequest, CheckResponse, Policy, Stage, RedactionResult

PRIORITY = {Action.ALLOW: 0, Action.FLAG: 1, Action.REDACT: 2, Action.BLOCK: 3}


class Engine:
    def __init__(self, policies: list[Policy], checkers: dict[str, Checker]):
        self._policies: dict[tuple[str, str], Policy] = {}
        for policy in policies:
            key = (policy.application_id, policy.version)
            if key in self._policies:
                raise ValueError("Duplicate policy version")
            ids = [r.checker_id for r in policy.rules]
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate checker in policy")
            if len({r.rule_id for r in policy.comparisons}) != len(policy.comparisons):
                raise ValueError("Duplicate field comparison")
            if policy.comparisons and "cross_field" not in ids:
                raise ValueError("Field comparisons require a cross_field checker rule")
            if len({r.rule_id for r in policy.required_sections}) != len(policy.required_sections):
                raise ValueError("Duplicate completeness rule")
            for stage in Stage:
                labels = [r.label.lower() for r in policy.required_sections if stage in r.stages]
                if len(set(labels)) != len(labels):
                    raise ValueError("Duplicate completeness label")
            if policy.required_sections and "completeness" not in ids:
                raise ValueError("Required sections require a completeness checker rule")
            for rule in policy.rules:
                if rule.checker_id != "pii" and Action.REDACT in {rule.on_finding, rule.on_input_finding, rule.on_output_finding}:
                    raise ValueError("Redaction actions are supported only by the pii checker")
            for schema in (policy.input_schema, policy.output_schema):
                if schema is not None:
                    validate_local_schema(schema)
                    Draft202012Validator.check_schema(schema)
                    validate_schema_formats(schema)
            self._policies[key] = deepcopy(policy)
        self._checkers = dict(checkers)

    def has_policy(self, application_id: str, version: str) -> bool:
        return (application_id, version) in self._policies

    def describe_policies(self, application_id: str) -> list[dict]:
        """Expose only safe, application-scoped configuration for the dashboard."""
        return [{
            "version": policy.version, "mode": policy.mode,
            "checks": [{"id": rule.checker_id, "required": rule.required,
                        "available": rule.checker_id in self._checkers,
                        "simulated": bool(getattr(self._checkers.get(rule.checker_id), "simulated", False)),
                        "stages": list(rule.stages)}
                       for rule in policy.rules],
        } for (owner, _), policy in self._policies.items() if owner == application_id]

    async def check(self, request: CheckRequest, stage: Stage) -> CheckResponse:
        policy = self._policies[(request.application_id, request.policy_version)]
        rules = [rule for rule in policy.rules if stage in rule.stages]
        started = perf_counter()

        async def execute(checker_id: str, content_request: CheckRequest = request) -> CheckerResult:
            tick = perf_counter()
            checker = self._checkers.get(checker_id)
            if checker is None:
                return CheckerResult(checker_id=checker_id, checker_version="unknown",
                                     status="unsupported", error_code="checker_unavailable")
            simulated = bool(getattr(checker, "simulated", False))
            version = getattr(checker, "checker_version", "unknown")
            try:
                result = await asyncio.wait_for(
                    checker(content_request, policy, stage), timeout=policy.timeout_ms / 1000,
                )
                if result.checker_id != checker_id:
                    raise ValueError("Adapter returned wrong identity")
                if (result.status == "flagged") != bool(result.findings):
                    raise ValueError("Adapter returned inconsistent findings")
                result = result.model_copy(update={"simulated": simulated or result.simulated})
                if (result.simulated and policy.mode == "enforce"
                        and not policy.allow_simulated_enforcement):
                    result = result.model_copy(update={
                        "status": "error", "findings": (), "error_code": "simulator_not_enforceable",
                    })
            except TimeoutError:
                result = CheckerResult(checker_id=checker_id, checker_version=version,
                                       status="error", error_code="checker_timeout", simulated=simulated)
            except Exception:
                # Exception text can contain customer content or credentials.
                result = CheckerResult(checker_id=checker_id, checker_version=version,
                                       status="error", error_code="checker_failure", simulated=simulated)
            return result.model_copy(update={"latency_ms": round((perf_counter() - tick) * 1000, 3)})

        results = await asyncio.gather(*(execute(r.checker_id) for r in rules))
        actions = [Action.ALLOW]
        incomplete = False
        for rule, result in zip(rules, results):
            if result.status == "flagged":
                override = rule.on_input_finding if stage == Stage.INPUT else rule.on_output_finding
                actions.append(override or rule.on_finding)
            elif rule.required and result.status != "passed":
                incomplete = True
                actions.append(Action(policy.failure_action))
        redact_rule = next((r for r in rules if r.checker_id == "pii" and (
            (r.on_input_finding if stage == Stage.INPUT else r.on_output_finding) or r.on_finding
        ) == Action.REDACT), None)
        redaction = RedactionResult()
        if redact_rule:
            original = next(r for r in results if r.checker_id == "pii")
            if original.status == "passed":
                redaction = RedactionResult(status="not_needed", explanation="No supported identifier was detected; no content is returned.")
            elif original.status != "flagged" or original.transformed_content is None or incomplete:
                redaction = RedactionResult(status="withheld", explanation="A complete, valid transformation is unavailable; no transformed content is returned.")
                actions.append(Action.BLOCK)
            else:
                try:
                    transformed_request = CheckRequest.model_validate({
                        **request.model_dump(), "content": original.transformed_content,
                    })
                    rechecks = tuple(await asyncio.gather(*(execute(r.checker_id, transformed_request) for r in rules)))
                except Exception:
                    rechecks = ()
                if rechecks and all(c.status == "passed" and not c.simulated for c in rechecks):
                    redaction = RedactionResult(status="preview" if policy.mode == "observe" else "ready",
                        content=original.transformed_content,
                        explanation="Detected supported identifiers were replaced and selected checks rerun. Other sensitive information may remain.",
                        validation_results=rechecks)
                else:
                    redaction = RedactionResult(status="withheld", explanation="Transformed content did not pass all selected real checks; no content is returned.", validation_results=rechecks)
                    if not rechecks or any(c.status not in {"passed", "flagged"} for c in rechecks):
                        incomplete = True
                    actions.append(Action.BLOCK)
        recommended = max(actions, key=PRIORITY.__getitem__)
        return CheckResponse(
            check_id=str(uuid4()), application_id=request.application_id,
            policy_version=policy.version, stage=stage, mode=policy.mode,
            evaluation_status="incomplete" if incomplete else "complete",
            recommended_action=recommended,
            effective_action=Action.ALLOW if policy.mode == "observe" else recommended,
            findings=tuple(f for r in results for f in r.findings),
            checker_results=tuple(results), simulated=any(r.simulated for r in results),
            total_latency_ms=round((perf_counter() - started) * 1000, 3),
            groups=group_results(policy, results, stage),
            redaction=redaction,
        )
