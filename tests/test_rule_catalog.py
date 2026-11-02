"""Every rule a tenant can set one by one: its example is reported and its look-alike is not, by the real
detectors, with only that rule's check on."""
import asyncio

import pytest

pytest.importorskip("tldextract")
pytest.importorskip("phonenumbers")

from guardoath import agent_boundary_guards, agent_extended_guards, agent_workflow_guards, data_agent_guards, semantic_guards
from guardoath.checkers import default_checkers
from guardoath.control_registry import CONTROLS, registry_payload
from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, Policy, Rule, Stage
from guardoath.privacy_catalog import CORE_ENTITIES, PATTERN_ENTITIES
from guardoath.rule_catalog import LABELED, RULE_ACTIONS, RULES, rule_payload, rules_for
from guardoath.synthetic_secrets import provider_examples
from guardoath.tenant_record import CHECK_IDS


@pytest.fixture(scope="module")
def checkers():
    return default_checkers(enable_pii=True)


def reported(checkers, rule, content):
    settings = {rule.needs_setting: True} if rule.needs_setting else {}
    policy = Policy(application_id="demo", version="v1", timeout_ms=2000,
                    rules=(Rule(checker_id=rule.control_id, on_finding=Action.FLAG),), **settings)
    request = CheckRequest(application_id="demo", policy_version="v1", content=content)
    result = asyncio.run(Engine([policy], checkers).check(request, Stage.INPUT))
    assert result.evaluation_status == "complete", result.checker_results
    return {finding.rule_id for finding in result.findings}


@pytest.mark.parametrize("rule_id", sorted(RULES))
def test_every_example_is_reported(checkers, rule_id):
    rule = RULES[rule_id]
    assert rule_id in reported(checkers, rule, rule.example)


@pytest.mark.parametrize("rule_id", sorted(RULES))
def test_no_look_alike_is_reported(checkers, rule_id):
    rule = RULES[rule_id]
    assert rule_id not in reported(checkers, rule, rule.look_alike)


def test_rule_counts():
    assert len(RULES) == 239
    assert {control: len(rules_for(control)) for control in ("lexical_guards", "pii", "secrets", "text_security")} == {
        "lexical_guards": 121, "pii": 97, "secrets": 17, "text_security": 4}
    assert len(rules_for("pii")) == len(PATTERN_ENTITIES) + len(CORE_ENTITIES) + len(LABELED)
    assert {rule.control_id for rule in RULES.values()} <= set(CHECK_IDS)


def test_every_lexical_rule_is_listed_once():
    modules = {**semantic_guards.RULES, **agent_extended_guards.RULES, **data_agent_guards.RULES,
               **agent_workflow_guards.RULES, **agent_boundary_guards.CASES}
    assert {rule.rule_id for rule in rules_for("lexical_guards")} == set(modules)


def test_every_secret_detector_is_listed_once():
    assert {rule.rule_id for rule in rules_for("secrets")} == (
        {"secrets." + name for name in provider_examples()} | {"secrets.credential_assignment"})


def test_entries_are_complete_and_distinct():
    titles = [rule.title for rule in RULES.values()]
    assert len(titles) == len(set(titles))
    for rule in RULES.values():
        assert rule.use_case.startswith("Turn this on") and rule.title in rule.use_case
        assert rule.example != rule.look_alike
        assert rule.severity in {"low", "medium", "high"}
        assert rule.needs_setting in {None, "detect_labeled_sensitive_data", "detect_secret_assignments"}
    assert RULE_ACTIONS == ("off", "allow", "flag", "block")


def test_rules_with_a_needed_setting_stay_quiet_without_it(checkers):
    for rule in RULES.values():
        if rule.needs_setting:
            policy = Policy(application_id="demo", version="v1", timeout_ms=2000,
                            rules=(Rule(checker_id=rule.control_id, on_finding=Action.FLAG),))
            request = CheckRequest(application_id="demo", policy_version="v1", content=rule.example)
            result = asyncio.run(Engine([policy], checkers).check(request, Stage.INPUT))
            assert rule.rule_id not in {finding.rule_id for finding in result.findings}, rule.rule_id


def test_the_registry_lists_every_rule_under_its_control():
    body = registry_payload(default_checkers(enable_pii=True))
    assert body["rule_count"] == len(RULES)
    listed = {}
    for domain in body["domains"]:
        for item in domain["controls"]:
            assert item["rule_count"] == len(item["rules"])
            for entry in item["rules"]:
                listed[entry["rule_id"]] = item["control_id"]
                assert entry == rule_payload(RULES[entry["rule_id"]])
    assert listed == {rule_id: rule.control_id for rule_id, rule in RULES.items()}
    assert {item.control_id for item in CONTROLS} >= set(listed.values())
