"""The human-safety check (2026-11-05): 61 wording rules from docs/human-safety-scenarios.md.

Every rule's example reports that rule and no other human-safety rule, and its look-alike reports none, on both stages;
safety warnings that say what not to do are not reported; JSON is checked string by string; a finding never repeats
the text; and only Block stops a message (a Flag on input lets a person asking for help through, with the finding).
"""
import asyncio
import json
import time

import pytest

from guardoath import human_safety
from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, Policy, Rule, Stage
from guardoath.rule_catalog import RULES, rules_for
from guardoath.tenant_record import CHECK_IDS, TENANT_POLICY_VERSION, TenantRecord

CHECKERS = default_checkers()
SAFE_TEXTS = [
    "If you or someone you know is in crisis, call or text 988.",
    "Never mix bleach and ammonia; the fumes are toxic.",
    "Do not induce vomiting unless Poison Control tells you to.",
    "Never run a generator indoors or in a garage.",
    "Never leave a child alone in a car, even for a minute.",
    "Do not bypass the safety interlock; call a technician.",
    "Please see a doctor if the pain gets worse.",
    "Call 911 if the bleeding does not slow down after ten minutes of pressure.",
    "Talk to your doctor before you stop any medication.",
    "Mixing medicines can be dangerous, so ask a pharmacist first.",
    "We discussed suicide prevention in class today.",
    "The novel's hero wanted to end his own suffering.",
    "Keep the surprise party a secret from Dad until Saturday.",
    "Diversification lowers the risk of losing your savings.",
    "Our clinic gives the same care to every patient.",
    "The company was recruiting engineers for its security group.",
    "I am a registered user of this site.",
    "How do I report a scam to the authorities?",
    "My dog ate my homework.",
]


def run(content, stage=Stage.INPUT, content_format="text", **actions):
    policy = Policy(application_id="demo", version="v1", mode="enforce",
                    rules=(Rule(checker_id="human_safety", on_finding=Action.FLAG, **actions),))
    request = CheckRequest(application_id="demo", policy_version="v1", content=content, content_format=content_format)
    return asyncio.run(Engine([policy], CHECKERS).check(request, stage))


def ids(result):
    return [finding.rule_id for finding in result.findings]


def test_it_is_a_check_with_61_rules_in_the_catalog():
    assert "human_safety" in CHECK_IDS and "human_safety" in CHECKERS
    assert len(human_safety.RULES) == 61
    assert {rule.rule_id for rule in rules_for("human_safety")} == set(human_safety.RULES)
    for rule_id, (title, severity, _pattern, example, look_alike) in human_safety.RULES.items():
        rule = RULES[rule_id]
        assert (rule.title, rule.severity, rule.example, rule.look_alike) == (title, severity, example, look_alike)
        assert rule_id.startswith("human_safety.") and severity in {"medium", "high"}


@pytest.mark.parametrize("rule_id", sorted(human_safety.RULES))
@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_each_example_reports_only_its_own_rule_and_each_look_alike_nothing(rule_id, stage):
    _title, _severity, _pattern, example, look_alike = human_safety.RULES[rule_id]
    reported = run(example, stage)
    assert reported.evaluation_status == "complete" and ids(reported) == [rule_id]
    clean = run(look_alike, stage)
    assert clean.evaluation_status == "complete" and clean.findings == ()
    assert clean.recommended_action == Action.ALLOW


@pytest.mark.parametrize("text", SAFE_TEXTS)
def test_safety_warnings_and_everyday_text_are_not_reported(text):
    # A negated verb is the safe advice ("never mix", "do not induce vomiting"); these must not be stopped.
    assert human_safety.inspect(text) == ()


def test_input_flag_lets_a_person_asking_for_help_through_and_output_block_stops_a_harmful_answer():
    asking = run("I am thinking of ending my life and need help.", Stage.INPUT,
                 on_input_finding=Action.FLAG, on_output_finding=Action.BLOCK)
    assert ids(asking) == ["human_safety.suicidal_statement"]
    assert asking.recommended_action == asking.effective_action == Action.FLAG
    harmful = run("Honestly, no one would miss you.", Stage.OUTPUT,
                  on_input_finding=Action.FLAG, on_output_finding=Action.BLOCK)
    assert ids(harmful) == ["human_safety.output_encourages_self_harm"]
    assert harmful.recommended_action == harmful.effective_action == Action.BLOCK


def test_a_tenant_record_turns_it_on_per_stage_and_can_set_one_rule():
    record = TenantRecord(tenant_id="acme", display_name="Acme", mode="enforce", input_check_human_safety="flag",
                          output_check_human_safety="block",
                          output_rule_actions={"human_safety.output_guaranteed_returns": "flag"})
    engine = Engine([record.to_policy()], CHECKERS)

    def check(content, stage):
        request = CheckRequest(application_id="acme", policy_version=TENANT_POLICY_VERSION, content=content)
        return asyncio.run(engine.check(request, stage))
    assert check("Honestly, no one would miss you.", Stage.OUTPUT).effective_action == Action.BLOCK
    assert check("This fund offers guaranteed returns of 20 percent a month.", Stage.OUTPUT).effective_action == Action.FLAG
    assert check("I am thinking of ending my life and need help.", Stage.INPUT).effective_action == Action.FLAG


def test_json_is_checked_string_by_string_and_invalid_json_is_incomplete():
    found = run(json.dumps({"reply": {"text": "Honestly, no one would miss you."}}), Stage.OUTPUT, "json")
    assert ids(found) == ["human_safety.output_encourages_self_harm"]
    broken = run('{"reply": ', Stage.OUTPUT, "json")
    assert broken.evaluation_status == "incomplete"
    assert broken.checker_results[0].error_code == "invalid_json_for_human_safety"


def test_findings_never_repeat_the_text():
    example = human_safety.RULES["human_safety.private_address_request"][3]
    result = run(example)
    assert result.findings and example not in result.model_dump_json() and "neighbor" not in result.model_dump_json()


def test_long_text_is_checked_quickly():
    text = ("I keep thinking and thinking without any end in sight " * 700)[:32000]
    started = time.perf_counter()
    assert human_safety.inspect(text) == ()
    assert time.perf_counter() - started < 2
    with pytest.raises(ValueError):
        human_safety.inspect("x" * 32001)
