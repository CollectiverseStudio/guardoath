"""Strict cross-field timeout contracts: failure must be blocked, not treated as a clean scan."""
import asyncio

from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, CheckerResult, FieldComparison, Policy, Rule, Stage


def test_cross_field_checker_deadline_expires_fail_closed_without_late_findings():
    async def delayed(request, policy, stage):
        await asyncio.sleep(0.035)
        return CheckerResult(checker_id="cross_field", checker_version="2",
                             status="passed")

    policy = Policy(
        application_id="demo", version="bounded-timeout", mode="enforce",
        timeout_ms=5, failure_action="block",
        rules=(Rule(checker_id="cross_field", on_finding="block"),),
        comparisons=(FieldComparison(rule_id="required", left="/a", right="/b", operator="le"),))
    request = CheckRequest(application_id="demo", policy_version="bounded-timeout",
                           content='{"a":2,"b":1}', content_format="json")
    result = asyncio.run(Engine([policy], {"cross_field": delayed}).check(request, Stage.INPUT))
    assert result.evaluation_status == "incomplete"
    assert result.checker_results[0].status == "error"
    assert result.checker_results[0].error_code == "checker_timeout"
    assert result.findings == ()
    assert result.recommended_action == result.effective_action == Action.BLOCK
