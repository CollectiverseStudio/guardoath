"""Enforcement contracts: overlapping batch-5/6 identifiers remain independent.

These are staged acceptance tests for Director-owned registration of config6.
No production checker registration or alias rewrite is performed by this file.
"""
import asyncio

import pytest

from guardoath.agent_boundary_guards import inspect as inspect_boundary
from guardoath.agent_config_guards import inspect as inspect_config
from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, CheckerResult, Finding, Policy, Rule, Stage


@pytest.mark.parametrize("setting,config_id,boundary_id", [
    ("verify_tls", "config6.verify_tls", "agent5.disable_tls_cert"),
    ("verify_webhook_signature", "config6.verify_webhook_signature", "agent5.disable_webhook_signature"),
    ("audit_logging_enabled", "config6.audit_logging_enabled", "agent5.disable_audit_setting"),
    ("secret_scanning_enabled", "config6.secret_scanning_enabled", "agent5.disable_secret_scan"),
    ("content_filter_enabled", "config6.content_filter_enabled", "agent5.disable_content_filter"),
    ("dlp_enabled", "config6.dlp_enabled", "agent5.disable_dlp_setting"),
    ("backup_enabled", "config6.backup_enabled", "agent5.disable_backup_setting"),
    ("encrypt_at_rest", "config6.encrypt_at_rest", "agent5.disable_encryption_setting"),
])
def test_overlapping_ids_are_not_semantically_equivalent(setting, config_id, boundary_id):
    off = setting + "=off"
    false = setting + "=false"
    off_config = {s.rule_id for s in inspect_config(off)}
    off_boundary = {s.rule_id for s in inspect_boundary(off)}
    false_config = {s.rule_id for s in inspect_config(false)}
    false_boundary = {s.rule_id for s in inspect_boundary(false)}
    assert config_id in off_config
    assert boundary_id not in off_boundary
    assert config_id in false_config
    assert boundary_id in false_boundary


def run_engine(message, actions, owner="tenant-one"):
    matches = tuple(
        Finding(rule_id=s.rule_id, category="lexical_security", severity="high",
                explanation="Lexical indicator matched")
        for s in (*inspect_config(message), *inspect_boundary(message))
        if s.rule_id in ("config6.verify_tls", "agent5.disable_tls_cert")
    )

    async def lexical(request, policy, stage):
        return CheckerResult(
            checker_id="lexical_guards", checker_version="fixture",
            status="flagged" if matches else "passed", findings=matches,
        )

    lexical.checker_version = "fixture"
    policy = Policy(
        application_id=owner, version="tenant", mode="enforce",
        rules=(Rule(checker_id="lexical_guards", on_finding=Action.ALLOW),),
        rule_actions=actions,
    )
    engine = Engine([policy], {"lexical_guards": lexical})
    return asyncio.run(engine.check(
        CheckRequest(application_id=owner, policy_version="tenant", content=message),
        Stage.INPUT,
    ))


@pytest.mark.parametrize("actions,expected", [
    ({"config6.verify_tls": "off", "agent5.disable_tls_cert": "allow"}, "allow"),
    ({"config6.verify_tls": "allow", "agent5.disable_tls_cert": "off"}, "allow"),
    ({"config6.verify_tls": "block", "agent5.disable_tls_cert": "off"}, "block"),
    ({"config6.verify_tls": "off", "agent5.disable_tls_cert": "block"}, "block"),
    ({"config6.verify_tls": "flag", "agent5.disable_tls_cert": "allow"}, "flag"),
    ({"config6.verify_tls": "allow", "agent5.disable_tls_cert": "flag"}, "flag"),
])
def test_distinct_finding_actions_control_actual_engine_decision(actions, expected):
    result = run_engine("verify_tls=false", actions)
    assert result.effective_action.value == expected
    expected_ids = {"config6.verify_tls", "agent5.disable_tls_cert"} - {
        rule_id for rule_id, action in actions.items() if action == "off"
    }
    assert {f.rule_id for f in result.findings} == expected_ids


def test_off_only_config6_finding_cannot_be_substituted_with_agent5_id():
    result = run_engine("verify_tls=off", {
        "config6.verify_tls": "block",
        "agent5.disable_tls_cert": "off",
    })
    assert result.effective_action == Action.BLOCK
    assert {f.rule_id for f in result.findings} == {"config6.verify_tls"}


def test_tenant_rule_override_does_not_leak_to_second_tenant():
    # One shared Engine, one checker and concurrent requests: exercise the actual
    # tenant-policy lookup rather than two independently constructed engines.
    async def lexical(request, policy, stage):
        matches = tuple(
            Finding(rule_id=s.rule_id, category="lexical_security",
                    severity="high", explanation="Lexical indicator matched")
            for s in (*inspect_config(request.content), *inspect_boundary(request.content))
            if s.rule_id in ("config6.verify_tls", "agent5.disable_tls_cert")
        )
        return CheckerResult(
            checker_id="lexical_guards", checker_version="fixture",
            status="flagged" if matches else "passed", findings=matches,
        )

    lexical.checker_version = "fixture"
    shared_rule = Rule(checker_id="lexical_guards", on_finding=Action.ALLOW)
    engine = Engine([
        Policy(application_id="tenant-block", version="tenant", mode="enforce",
               rules=(shared_rule,), rule_actions={"config6.verify_tls": "block",
                                                   "agent5.disable_tls_cert": "allow"}),
        Policy(application_id="tenant-allow", version="tenant", mode="enforce",
               rules=(shared_rule,), rule_actions={"config6.verify_tls": "allow",
                                                   "agent5.disable_tls_cert": "allow"}),
    ], {"lexical_guards": lexical})

    async def check_tenants():
        return await asyncio.gather(*(
            engine.check(CheckRequest(application_id=owner, policy_version="tenant",
                                      content="verify_tls=false"), Stage.INPUT)
            for owner in ("tenant-block", "tenant-allow")
        ))

    blocked, allowed = asyncio.run(check_tenants())
    assert blocked.evaluation_status == allowed.evaluation_status == "complete"
    assert blocked.effective_action == Action.BLOCK
    assert allowed.effective_action == Action.ALLOW
    assert {f.rule_id for f in blocked.findings} == {f.rule_id for f in allowed.findings} == {
        "config6.verify_tls", "agent5.disable_tls_cert"
    }


def test_benign_and_unicode_safety_boundaries():
    assert not any(s.rule_id == "config6.verify_tls"
                   for s in inspect_config("verify_tls=true"))
    assert not any(s.rule_id == "agent5.disable_tls_cert"
                   for s in inspect_boundary("verify_tls=true"))
    # Boundary family performs NFKC normalization and may recognize keys
    # that config6 does not. No alias should erase that provenance.
    unicode_text = "ｖｅｒｉｆｙ＿ｔｌｓ=false"
    assert "agent5.disable_tls_cert" in {s.rule_id for s in inspect_boundary(unicode_text)}
