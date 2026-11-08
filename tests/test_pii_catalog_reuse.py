"""PII 500-ms contention optimization: prebuild recognizers once, never cache tenant data."""
import asyncio
import pytest

pytest.importorskip("tldextract")
pytest.importorskip("phonenumbers")

from guardoath.pii import build_pii_checker
from guardoath.models import Action, CheckRequest, Policy, Rule, Stage


def test_process_shared_immutable_pii_setup_does_not_cache_customer_content_or_actions():
    checker_a = build_pii_checker()
    checker_b = build_pii_checker()
    assert checker_a is checker_b
    assert build_pii_checker.cache_info().maxsize == 1
    assert checker_a.checker_version == checker_b.checker_version

    async def run_for(tenant, action, message):
        policy = Policy(application_id=tenant, version="test",
                        timeout_ms=500, rules=(Rule(checker_id="pii", on_finding=action),))
        request = CheckRequest(application_id=tenant, policy_version="test",
                               content=message, content_format="text")
        return await checker_b(request, policy, Stage.INPUT)

    async def test():
        first = await run_for("tenant-a", Action.REDACT, "Email synthetic@example.com")
        assert first.status == "flagged" and first.transformed_content is not None
        assert "synthetic@example.com" not in first.transformed_content
        second = await run_for("tenant-b", Action.FLAG, "There is no email here.")
        assert second.status == "passed"
        assert second.findings == () and second.transformed_content is None
        third = await run_for("tenant-b", Action.FLAG, "Email another@example.com")
        assert third.status == "flagged" and third.transformed_content is None
        assert [f.rule_id for f in first.findings] == [f.rule_id for f in third.findings]
        assert "synthetic@example.com" not in repr(third)
    asyncio.run(test())


def test_shared_catalog_retains_tenant_privacy_options_and_stage_off():
    checker = build_pii_checker()
    policy = Policy(application_id="tenant-a", version="test", timeout_ms=500,
                    rules=(Rule(checker_id="pii", on_finding=Action.FLAG),),
                    rule_actions={"pii.email_address": "off"})
    request = CheckRequest(application_id="tenant-a", policy_version="test",
                           content="Email synthetic@example.com", content_format="text")
    result = asyncio.run(checker(request, policy, Stage.INPUT))
    assert all(f.rule_id != "pii.email_address" for f in result.findings)
