"""Secrets 500-ms startup optimization retains live request/tenant isolation."""
import asyncio

from guardoath.models import Action, CheckRequest, Policy, Rule, Stage
from guardoath.secrets_check import build_secret_checker


def test_fixed_secret_catalog_is_reused_without_caching_tenant_payloads():
    checker = build_secret_checker()
    assert build_secret_checker() is checker
    assert build_secret_checker.cache_info().maxsize == 1

    async def evaluate(tenant, text, assignments):
        policy = Policy(
            application_id=tenant, version="synthetic", mode="enforce",
            timeout_ms=500,
            rules=(Rule(checker_id="secrets", on_finding=Action.BLOCK),),
            detect_secret_assignments=assignments,
        )
        return await checker(CheckRequest(
            application_id=tenant, policy_version="synthetic",
            content=text, content_format="text"), policy, Stage.INPUT)

    async def run():
        bad = await evaluate("tenant-a", 'password = "synthetic-secret-987"', True)
        assert bad.status == "flagged"
        assert "secrets.credential_assignment" in {f.rule_id for f in bad.findings}
        clean = await evaluate("tenant-b", "Ordinary status message.", True)
        assert clean.status == "passed" and not clean.findings
        disabled = await evaluate("tenant-b", 'password = "synthetic-secret-987"', False)
        assert "secrets.credential_assignment" not in {f.rule_id for f in disabled.findings}
        again = await evaluate("tenant-a", 'password = "synthetic-secret-654"', True)
        assert "secrets.credential_assignment" in {f.rule_id for f in again.findings}
        assert "synthetic-secret-987" not in repr(again)
    asyncio.run(run())


def test_shared_secret_checker_remains_deterministic_under_concurrent_tenants():
    checker = build_secret_checker()

    async def run_one(i):
        tenant = "tenant-a" if i % 2 else "tenant-b"
        policy = Policy(
            application_id=tenant, version="synthetic", timeout_ms=500,
            rules=(Rule(checker_id="secrets", on_finding=Action.FLAG),),
            detect_secret_assignments=bool(i % 2),
        )
        content = 'password = "synthetic-secret-123"' if i % 2 else "Read-only status."
        result = await checker(CheckRequest(
            application_id=tenant, policy_version="synthetic",
            content=content), policy, Stage.INPUT)
        assert result.status == ("flagged" if i % 2 else "passed")
        assert bool(result.findings) == bool(i % 2)

    async def run():
        # Within the existing four-worker capacity; no mock worker or raised deadline.
        await asyncio.gather(*(run_one(i) for i in range(4)))
    asyncio.run(run())
