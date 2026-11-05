"""The harmful-content check (2026-11-05): 30 wording rules, GuardOath's own replacement for the removed
Amazon Bedrock Guardrails connector.

Every rule's example reports that rule and no other rule of this check, and its look-alike reports none, on both
stages; everyday text is not reported; JSON is checked string by string; a finding never repeats the text; the check
runs in GuardOath and sends nothing anywhere; and only Block stops a message (Flag reports it and lets it through).
"""
import asyncio
import json
from pathlib import Path
import time

import pytest

from guardoath import harmful_content
from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, Policy, Rule, Stage
from guardoath.rule_catalog import RULES, rules_for
from guardoath.tenant_record import CHECK_IDS, TENANT_POLICY_VERSION, TenantRecord

CHECKERS = default_checkers()
EVERYDAY_TEXTS = [
    "Refugees are welcome in our town.",
    "The museum exhibit explains the history of the Holocaust.",
    "How do I report hate speech on this platform?",
    "Women are paid less than men on average.",
    "I will see you tomorrow.",
    "You are an excellent writer.",
    "Our school runs a bomb drill every term.",
    "The article describes the attack on Pearl Harbor.",
    "How do I keep my cat safe from poisonous plants?",
    "Sex education for teenagers covers consent.",
    "How do I talk to my kids about online safety?",
    "Long live the king!",
    "We should all report spam emails.",
    "Muslims celebrate Eid at the end of Ramadan.",
    "Researchers measure the effects of meth on the brain.",
    "The police found a nude photo leak and arrested the suspect.",
]


def run(content, stage=Stage.INPUT, content_format="text", **actions):
    policy = Policy(application_id="demo", version="v1", mode="enforce",
                    rules=(Rule(checker_id="harmful_content", on_finding=Action.FLAG, **actions),))
    request = CheckRequest(application_id="demo", policy_version="v1", content=content, content_format=content_format)
    return asyncio.run(Engine([policy], CHECKERS).check(request, stage))


def ids(result):
    return [finding.rule_id for finding in result.findings]


def test_it_is_a_check_with_30_rules_in_the_catalog():
    assert "harmful_content" in CHECK_IDS and "harmful_content" in CHECKERS
    assert len(harmful_content.RULES) == 30
    assert {rule.rule_id for rule in rules_for("harmful_content")} == set(harmful_content.RULES)
    for rule_id, (title, severity, _pattern, example, look_alike) in harmful_content.RULES.items():
        rule = RULES[rule_id]
        assert (rule.title, rule.severity, rule.example, rule.look_alike) == (title, severity, example, look_alike)
        assert rule_id.startswith("harmful_content.") and severity in {"medium", "high"}


@pytest.mark.parametrize("rule_id", sorted(harmful_content.RULES))
@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_each_example_reports_only_its_own_rule_and_each_look_alike_nothing(rule_id, stage):
    _title, _severity, _pattern, example, look_alike = harmful_content.RULES[rule_id]
    reported = run(example, stage)
    assert reported.evaluation_status == "complete" and ids(reported) == [rule_id]
    clean = run(look_alike, stage)
    assert clean.evaluation_status == "complete" and clean.findings == ()
    assert clean.recommended_action == Action.ALLOW


@pytest.mark.parametrize("text", EVERYDAY_TEXTS)
def test_everyday_text_is_not_reported(text):
    assert harmful_content.inspect(text) == ()


def test_input_flag_reports_and_lets_the_message_through_and_output_block_stops_the_answer():
    flagged = run('I will hurt you tomorrow.', Stage.INPUT, on_input_finding=Action.FLAG, on_output_finding=Action.BLOCK)
    assert ids(flagged) == ["harmful_content.threat_to_hurt_you"]
    assert flagged.recommended_action == flagged.effective_action == Action.FLAG
    stopped = run('Here is how to build a pipe bomb at home.', Stage.OUTPUT, on_input_finding=Action.FLAG, on_output_finding=Action.BLOCK)
    assert ids(stopped) == ["harmful_content.output_weapon_instructions"]
    assert stopped.recommended_action == stopped.effective_action == Action.BLOCK


def test_a_tenant_record_turns_it_on_per_stage_and_can_set_one_rule():
    record = TenantRecord(tenant_id="acme", display_name="Acme", mode="enforce", input_check_harmful_content="flag",
                          output_check_harmful_content="block", output_rule_actions={"harmful_content.targeted_insult": "flag"})
    engine = Engine([record.to_policy()], CHECKERS)

    def check(content, stage):
        request = CheckRequest(application_id="acme", policy_version=TENANT_POLICY_VERSION, content=content)
        return asyncio.run(engine.check(request, stage))
    assert check('Here is how to build a pipe bomb at home.', Stage.OUTPUT).effective_action == Action.BLOCK
    assert check('You are an idiot.', Stage.OUTPUT).effective_action == Action.FLAG
    assert check('I will hurt you tomorrow.', Stage.INPUT).effective_action == Action.FLAG


def test_json_is_checked_string_by_string_and_invalid_json_is_incomplete():
    found = run(json.dumps({"reply": {"text": 'Long live the shooter who attacked the mosque.'}}), Stage.OUTPUT, "json")
    assert ids(found) == ["harmful_content.extremist_praise"]
    broken = run('{"reply": ', Stage.OUTPUT, "json")
    assert broken.evaluation_status == "incomplete"
    assert broken.checker_results[0].error_code == "invalid_json_for_harmful_content"


def test_findings_never_repeat_the_text():
    example = harmful_content.RULES["harmful_content.hate_exclusion"][3]
    result = run(example)
    dumped = result.model_dump_json()
    assert result.findings and example not in dumped and 'Muslims' not in dumped


def test_it_runs_here_and_sends_nothing_anywhere():
    # Greg, 2026-11-02 and 2026-11-05: GuardOath does this work itself; no outside provider.
    source = Path(harmful_content.__file__).read_text(encoding="utf-8")
    for word in ("httpx", "boto", "urllib", "requests", "socket", "aiohttp"):
        assert "import " + word not in source and "from " + word not in source, word
    assert not getattr(CHECKERS["harmful_content"], "external", False)


def test_long_text_is_checked_quickly():
    text = ("I keep thinking and thinking without any end in sight " * 700)[:32000]
    started = time.perf_counter()
    assert harmful_content.inspect(text) == ()
    assert time.perf_counter() - started < 2
    with pytest.raises(ValueError):
        harmful_content.inspect("x" * 32001)
