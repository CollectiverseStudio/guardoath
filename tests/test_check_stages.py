"""Which stages a tenant record's checks run on, and checks that need JSON or text (2026-11-04).

A check configured for one stage only runs on that stage. A check that cannot run on a message's format (a check
that needs JSON, sent text; completeness, sent JSON) is skipped, which makes the result incomplete, and the
record's failure action applies: Greg chose to keep that (2026-11-04), with a warning on the dashboard.
"""
import asyncio
import re
from pathlib import Path

import pytest

from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, Stage
from guardoath.tenant_record import TENANT_POLICY_VERSION, TenantRecord

CHECKERS = default_checkers()
ROOT = Path(__file__).resolve().parents[1]
SCHEMA = {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}


def record(**fields):
    return TenantRecord(tenant_id="acme", display_name="Acme", **fields)


def stages(rec, check_id):
    return next(rule.stages for rule in rec.to_policy().rules if rule.checker_id == check_id)


def run(rec, content, stage, content_format="json"):
    request = CheckRequest(application_id="acme", policy_version=TENANT_POLICY_VERSION, content=content,
                           content_format=content_format)
    return asyncio.run(Engine([rec.to_policy()], CHECKERS).check(request, stage))


def test_schema_runs_only_on_the_stages_that_have_a_schema():
    assert stages(record(check_schema="block", input_schema=SCHEMA), "schema") == (Stage.INPUT,)
    assert stages(record(check_schema="block", output_schema=SCHEMA), "schema") == (Stage.OUTPUT,)
    assert stages(record(check_schema="block", input_schema=SCHEMA, output_schema=SCHEMA), "schema") == (Stage.INPUT, Stage.OUTPUT)


def test_an_answer_is_not_blocked_by_a_schema_that_only_covers_input():
    rec = record(check_schema="block", input_schema=SCHEMA)
    answer = run(rec, '{"anything": 1}', Stage.OUTPUT)
    assert answer.evaluation_status == "complete" and answer.recommended_action == Action.ALLOW
    assert "schema" not in {c.checker_id for c in answer.checker_results}
    # The input is still checked against the schema.
    assert run(rec, '{"anything": 1}', Stage.INPUT).recommended_action == Action.BLOCK
    assert run(rec, '{"name": "Ann"}', Stage.INPUT).recommended_action == Action.ALLOW


def test_comparisons_and_sections_decide_their_checks_stages():
    one = {"rule_id": "order", "left": "/a", "right": "/b", "operator": "le", "stages": ["input"]}
    other = {**one, "rule_id": "other", "stages": ["output"]}
    assert stages(record(check_cross_field="flag", comparisons=[one]), "cross_field") == (Stage.INPUT,)
    assert stages(record(check_cross_field="flag", comparisons=[one, other]), "cross_field") == (Stage.INPUT, Stage.OUTPUT)
    section = {"rule_id": "summary", "label": "Summary", "stages": ["output"]}
    assert stages(record(check_completeness="flag", required_sections=[section]), "completeness") == (Stage.OUTPUT,)
    rec = record(check_completeness="block", required_sections=[section])
    question = run(rec, "Can you explain your return policy?", Stage.INPUT, "text")
    assert question.evaluation_status == "complete" and question.recommended_action == Action.ALLOW


@pytest.mark.parametrize("check_id", ["pii", "secrets", "text_security", "lexical_guards", "graph", "ledger", "integrity"])
def test_other_checks_run_on_both_stages(check_id):
    settings = {"graph": {"graph": {}}, "ledger": {"ledger": {"currency": "USD"}},
                "integrity": {"integrity": {"sha256": "0" * 64}}}.get(check_id, {})
    assert stages(record(**{"check_" + check_id: "flag"}, **settings), check_id) == (Stage.INPUT, Stage.OUTPUT)


def test_a_json_only_check_on_a_text_message_is_incomplete_and_uses_the_failure_action():
    # Greg 2026-11-04: keep this (blocking), with a dashboard warning. demo1 had Graph structure on Block.
    rec = record(check_secrets="block", check_graph="block", graph={})
    result = run(rec, "Can you explain your return policy?", Stage.INPUT, "text")
    graph = next(c for c in result.checker_results if c.checker_id == "graph")
    assert graph.status == "skipped" and graph.error_code == "graph_json_required"
    assert result.findings == () and result.evaluation_status == "incomplete"
    assert result.recommended_action == Action.BLOCK
    flagging = record(check_secrets="block", check_graph="block", graph={}, failure_action="flag")
    assert run(flagging, "Can you explain your return policy?", Stage.INPUT, "text").recommended_action == Action.FLAG


def test_the_dashboard_format_table_matches_the_checks_code():
    """dashboard.js warns about checks that need JSON or text; the table is checked against each check's code."""
    script = (ROOT / "guardoath" / "static" / "dashboard.js").read_text(encoding="utf-8")
    found = re.search(r"^const formatNeeds = (\{[^\n]*\});$", script, re.M)
    assert found
    table = dict(re.findall(r"(\w+):\s*\"(json|text)\"", found.group(1)))
    sources = {path.stem: path.read_text(encoding="utf-8") for path in (ROOT / "guardoath").glob("*.py")}
    needs = {}
    for source in sources.values():
        for name, body in re.findall(r"async def (\w+)_check\(request[^)]*\)[^:\n]*:\n((?:    .*\n|\n)+)", source):
            first = body.split("try:")[0]
            if re.search(r"content_format\s*!=\s*['\"]json['\"]", first):
                needs[name] = "json"
            elif re.search(r"content_format\s*!=\s*['\"]text['\"]", first):
                needs[name] = "text"
    assert table == needs
    assert {"schema", "graph", "completeness"} <= table.keys() and table["completeness"] == "text"
