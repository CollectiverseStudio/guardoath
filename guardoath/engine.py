"""Execute approved checks with deadlines and explicit failure decisions."""

import asyncio
from copy import deepcopy
from time import perf_counter
from uuid import uuid4

from jsonschema import Draft202012Validator

from guardoath.checkers import Checker
from guardoath.config import validate_local_schema
from guardoath.formats import validate_schema_formats
from guardoath.groups import GROUPS, group_results
from guardoath.models import Action, CheckerResult, CheckRequest, CheckResponse, Policy, Stage, RedactionResult
from guardoath.validation_packs import PackRegistry, PackSelectionError

PRIORITY = {Action.ALLOW: 0, Action.FLAG: 1, Action.REDACT: 2, Action.BLOCK: 3}


class Engine:
    def __init__(self, policies: list[Policy], checkers: dict[str, Checker], pack_registry: PackRegistry | None = None):
        self._policies: dict[tuple[str, str], Policy] = {}
        for policy in policies:
            key = (policy.application_id, policy.version)
            if key in self._policies:
                raise ValueError("Duplicate policy version")
            ids = [r.checker_id for r in policy.rules]
            for checker_id in ("ledger", "timestamps", "graph", "integrity"):
                if (getattr(policy, checker_id) is not None) != (checker_id in ids):
                    raise ValueError("Data-quality checks require their configuration and checker rule")
            if (policy.tool_calls is not None) != ("tool_calls" in ids):
                raise ValueError("Tool-call checks require their configuration and checker rule")
            if (policy.evidence is not None) != ("evidence" in ids):
                raise ValueError("Evidence checks require their configuration and checker rule")
            if (policy.matched_outcomes is not None) != ("matched_outcomes" in ids):
                raise ValueError("Matched-outcome checks require their configuration and checker rule")
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
            if policy.detect_labeled_sensitive_data and "pii" not in ids:
                raise ValueError("Labeled sensitive-data checks require a pii checker rule")
            if "pii" not in ids and (not policy.privacy_patterns or policy.disabled_privacy_entities
                                     or "privacy_min_score" in policy.model_fields_set):
                raise ValueError("Privacy pattern options require a pii checker rule")
            if policy.confidential_terms or policy.detect_secret_assignments or policy.inspect_encoded_secrets:
                if 'secrets' not in ids:
                    raise ValueError('Secret options require a secrets checker rule')
            if len({r.rule_id for r in policy.confidential_terms}) != len(policy.confidential_terms):
                raise ValueError('Duplicate confidential-term rule identifier')
            if policy.confidential_terms:
                from guardoath.secrets_check import normalize
                secret_rule=next(r for r in policy.rules if r.checker_id=='secrets')
                for stage in Stage:
                    terms=[normalize(r.term) for r in policy.confidential_terms if stage in r.stages]
                    if len(set(terms)) != len(terms):
                        raise ValueError('Duplicate normalized confidential term')
                    if terms and stage not in secret_rule.stages:
                        raise ValueError('Confidential terms require an active secrets stage')
            for rule in policy.rules:
                if rule.checker_id != "pii" and Action.REDACT in {rule.on_finding, rule.on_input_finding, rule.on_output_finding}:
                    raise ValueError("Redaction actions are supported only by the pii checker")
            schemas = [policy.input_schema, policy.output_schema]
            if policy.tool_calls is not None:
                schemas.extend(tool.arguments_schema for tool in policy.tool_calls.tools)
            for schema in schemas:
                if schema is not None:
                    validate_local_schema(schema)
                    Draft202012Validator.check_schema(schema)
                    validate_schema_formats(schema)
            self._policies[key] = deepcopy(policy)
        self._checkers = dict(checkers)
        self._pack_registry = pack_registry
        if pack_registry is not None:
            for (app, _version), policy in self._policies.items():
                core = pack_registry.core_checker_ids(app)
                if core is not None:
                    available = {rule.checker_id for rule in policy.rules}
                    optional = pack_registry.optional_checker_ids(app)
                    if (not set(core) <= available or not optional <= available
                            or available != set(core) | optional):
                        raise ValueError("Pack registry must partition policy checks into CORE and optional packs")

    def describe_validation_packs(self, application_id: str) -> list[dict]:
        if self._pack_registry is None:
            return []
        return self._pack_registry.describe(application_id)

    def has_policy(self, application_id: str, version: str) -> bool:
        return (application_id, version) in self._policies

    def describe_policies(self, application_id: str) -> list[dict]:
        """Expose only safe, application-scoped configuration for the dashboard."""
        return [{
            "version": policy.version, "mode": policy.mode, "external_checks": policy.external_checks,
            "checks": [{"id": rule.checker_id, "required": rule.required,
                        "available": rule.checker_id in self._checkers,
                        "simulated": bool(getattr(self._checkers.get(rule.checker_id), "simulated", False)),
                        "external": rule.checker_id == "content_safety" or bool(getattr(self._checkers.get(rule.checker_id), "external", False)),
                        "provider": getattr(self._checkers.get(rule.checker_id), "provider", None),
                        "stages": list(rule.stages),
                        # What a finding does on each stage the check runs on.
                        "actions": {stage.value: ((rule.on_input_finding if stage == Stage.INPUT else rule.on_output_finding)
                                                  or rule.on_finding).value for stage in rule.stages}}
                       for rule in policy.rules],
        } for (owner, _), policy in self._policies.items() if owner == application_id]

    async def check(self, request: CheckRequest, stage: Stage) -> CheckResponse:
        policy = self._policies[(request.application_id, request.policy_version)]
        applied_packs = ("CORE",)
        if self._pack_registry is None:
            if request.validation_codes:
                raise PackSelectionError("Validation packs are not configured")
            selected_ids = {rule.checker_id for rule in policy.rules}
        else:
            packs = self._pack_registry.resolve(request.application_id, request.validation_codes)
            applied_packs = ("CORE", *(pack.code + "@" + pack.version for pack in packs))
            available = {rule.checker_id for rule in policy.rules}
            active_at_stage = {rule.checker_id for rule in policy.rules if stage in rule.stages}
            if any(not (set(pack.checker_ids) & active_at_stage) for pack in packs):
                raise PackSelectionError("Selected validation pack is unavailable for this stage")
            optional = self._pack_registry.optional_checker_ids(request.application_id)
            if not optional <= available:
                raise PackSelectionError("Validation pack configuration is unavailable")
            core = self._pack_registry.core_checker_ids(request.application_id)
            selected_ids = (set(core) if core is not None else available - optional) | set(PackRegistry.checker_union((), packs))
        rules = [rule for rule in policy.rules if stage in rule.stages and rule.checker_id in selected_ids]
        started = perf_counter()

        async def execute(checker_id: str, content_request: CheckRequest = request) -> CheckerResult:
            tick = perf_counter()
            checker = self._checkers.get(checker_id)
            if checker is None:
                return CheckerResult(checker_id=checker_id, checker_version="unknown",
                                     status="unsupported", error_code="checker_unavailable", external=checker_id == "content_safety")
            simulated = bool(getattr(checker, "simulated", False))
            version = getattr(checker, "checker_version", "unknown")
            external = bool(getattr(checker, "external", False))
            try:
                result = await asyncio.wait_for(
                    checker(content_request, policy, stage), timeout=policy.timeout_ms / 1000,
                )
                if result.checker_id != checker_id:
                    raise ValueError("Adapter returned wrong identity")
                if (result.status == "flagged") != bool(result.findings):
                    raise ValueError("Adapter returned inconsistent findings")
                result = result.model_copy(update={"simulated": simulated or result.simulated, "external": external or result.external})
                if (result.simulated and policy.mode == "enforce"
                        and not policy.allow_simulated_enforcement):
                    result = result.model_copy(update={
                        "status": "error", "findings": (), "error_code": "simulator_not_enforceable",
                    })
            except TimeoutError:
                result = CheckerResult(checker_id=checker_id, checker_version=version,
                                       status="error", error_code="checker_timeout", simulated=simulated, external=external)
            except Exception:
                # Exception text can contain customer content or credentials.
                result = CheckerResult(checker_id=checker_id, checker_version=version,
                                       status="error", error_code="checker_failure", simulated=simulated, external=external)
            return result.model_copy(update={"latency_ms": round((perf_counter() - tick) * 1000, 3)})

        async def run_rules(content_request):
            local_rules = [r for r in rules if not bool(getattr(self._checkers.get(r.checker_id), "external", False))]
            external_rules = [r for r in rules if bool(getattr(self._checkers.get(r.checker_id), "external", False))]
            local_results = await asyncio.gather(*(execute(r.checker_id, content_request) for r in local_rules))
            gate_error = None
            if policy.external_checks != "after_local_pass":
                gate_error = "external_policy_disabled"
            elif not local_results or any(c.status != "passed" or c.simulated for c in local_results):
                gate_error = "external_local_gate"
            if gate_error:
                external_results = [CheckerResult(checker_id=r.checker_id,
                    checker_version=getattr(self._checkers[r.checker_id], "checker_version", "unknown"),
                    status="skipped", external=True, error_code=gate_error) for r in external_rules]
            else:
                external_results = await asyncio.gather(*(execute(r.checker_id, content_request) for r in external_rules))
            indexed = {c.checker_id: c for c in (*local_results, *external_results)}
            return tuple(indexed[r.checker_id] for r in rules)

        results = await run_rules(request)
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
                redaction = RedactionResult(status="not_needed", explanation="The raw PII checker found no supported identifier. Other checker findings and the overall decision still apply; no transformed content is returned.")
            elif original.status != "flagged" or original.transformed_content is None or incomplete:
                redaction = RedactionResult(status="withheld", explanation="A complete, valid transformation is unavailable; no transformed content is returned.")
                actions.append(Action.BLOCK)
            else:
                try:
                    transformed_request = CheckRequest.model_validate({
                        **request.model_dump(), "content": original.transformed_content,
                    })
                    rechecks = await run_rules(transformed_request)
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
        external = any(c.external for c in (*results, *redaction.validation_results))
        return CheckResponse(
            check_id=str(uuid4()), application_id=request.application_id,
            policy_version=policy.version, applied_validation_packs=applied_packs, stage=stage, mode=policy.mode,
            evaluation_status="incomplete" if incomplete else "complete",
            recommended_action=recommended,
            effective_action=Action.ALLOW if policy.mode == "observe" else recommended,
            findings=tuple(f for r in results for f in r.findings),
            checker_results=tuple(results), simulated=any(r.simulated for r in results),
            total_latency_ms=round((perf_counter() - started) * 1000, 3),
            groups=group_results(policy, results, stage),
            redaction=redaction,
            cost_usd=None if external else 0,
            cost_basis="provider_unpriced" if external else "local_only",
        )
