"""Dedicated CSV export validation. No spreadsheet execution or real customer data."""
import asyncio
import pytest

from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import CheckRequest, Policy, Rule, Stage


def check(content, *, mode="enforce", stage=Stage.OUTPUT, fmt="text"):
    policy = Policy(
        application_id="demo", version="csv-export-v1", mode=mode,
        rules=(Rule(checker_id="csv_formula", on_finding="block"),),
    )
    request = CheckRequest(application_id="demo", policy_version="csv-export-v1",
                           content=content, content_format=fmt)
    return asyncio.run(Engine([policy], default_checkers()).check(request, stage))


@pytest.mark.parametrize("cell", [
    "=1+1", "+SUM(A1:A2)", "-cmd", "@SUM(A1:A2)",
    " =1+1", "\ufeff=2+3", "\u00a0=2+3", "\t=HYPERLINK(\"https://example.invalid\")",
    "\r\n=2+2",
])
@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_formula_triggers_are_blocked_and_original_value_is_not_echoed(cell, stage):
    response = check('name,amount\nAlice,"' + cell.replace('"', '""') + '"', stage=stage)
    assert response.evaluation_status == "complete"
    assert response.recommended_action == "block"
    assert response.effective_action == "block"
    assert {f.rule_id for f in response.findings} == {"csv_formula.potential_formula"}
    assert cell not in response.model_dump_json()


@pytest.mark.parametrize("value", [
    "name,amount\nAlice,25", "name,amount\nAlice,'=1+1",
    'item,description\nA,"a + b = c"', "a,b\n\'- ,okay",
    "header", "id,amount\n1,0",
])
def test_benign_and_escaped_values_pass(value):
    result = check(value)
    assert result.evaluation_status == "complete"
    assert not result.findings
    assert result.effective_action == "allow"
    structure = next(group for group in result.groups if group.group_id == "structure")
    assert structure.status == "passed"
    assert "csv_formula" in structure.checker_ids
    assert all(group.group_id != "custom" for group in result.groups)


def test_observe_reports_without_enforcing():
    result = check("a,b\n1,=2+3", mode="observe")
    assert result.recommended_action == "block"
    assert result.effective_action == "allow"
    assert {f.rule_id for f in result.findings} == {"csv_formula.potential_formula"}


@pytest.mark.parametrize("value,fmt,error", [
    ('{"a":"=2+3"}', "json", "csv_text_required"),
    ("a,b\n\x00,ok", "text", "csv_invalid_content"),
    ('a,b\n"unclosed', "text", "csv_invalid_content"),
    ("x\n" * 1001, "text", "csv_inspection_budget_exceeded"),
    (",".join(["x"] * 257), "text", "csv_inspection_budget_exceeded"),
])
def test_invalid_format_and_bounds_fail_closed(value, fmt, error):
    result = check(value, fmt=fmt)
    assert result.evaluation_status == "incomplete"
    assert result.effective_action == "block"
    assert result.checker_results[0].error_code == error


@pytest.mark.parametrize("cell,error", [
    ("\x1b[31m=2+3", "unsafe_csv_control_characters"),
    ("\u202e=2+3", "unsafe_csv_control_characters"),
    ("\u009b[31m=2+3", "unsafe_csv_control_characters"),
    ("\u200b=2+3", "unsafe_csv_invisible_formula_prefix"),
    ("  \u200d  @SUM(2,3)", "unsafe_csv_invisible_formula_prefix"),
    ("\u2060-2+3", "unsafe_csv_invisible_formula_prefix"),
])
@pytest.mark.parametrize("delimiter", [",", "\t"])
@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_direct_text_scanner_rejects_hidden_formula_and_controls(cell, error, delimiter, stage):
    """The regular text API bypasses attachment probing but must use the same safety policy."""
    from guardoath.attachment_probe import classify_attachment

    content = "name" + delimiter + "value\nSynthetic" + delimiter + cell
    assert classify_attachment(content.encode("utf-8")).reason == error
    response = check(content, stage=stage)
    assert response.evaluation_status == "incomplete"
    assert response.recommended_action == "block"
    assert response.effective_action == "block"
    assert response.checker_results[0].status == "error"
    assert response.checker_results[0].error_code == error
    assert not response.findings
    assert cell not in response.model_dump_json()


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_alternate_delimiter_hidden_formula_cannot_pass_direct_text_check(stage):
    from guardoath.attachment_probe import classify_attachment

    content = "name,value\nSynthetic,plain\t\u200b=2+3"
    assert classify_attachment(content.encode("utf-8")).reason == "unsafe_csv_invisible_formula_prefix"
    result = check(content, stage=stage)
    assert result.evaluation_status == "incomplete"
    assert result.effective_action == "block"
    assert result.checker_results[0].error_code == "unsafe_csv_invisible_formula_prefix"


@pytest.mark.parametrize("cell", [
    "\u200bordinary", "middle\u200b=2+3", "'\u200b=2+3",
    "👩\u200d🔬", "\u200cفارسی", "ordinary value",
])
def test_legitimate_unicode_and_apostrophe_escape_still_pass_direct_csv_check(cell):
    content = "name,value\nSynthetic," + cell
    response = check(content)
    assert response.evaluation_status == "complete"
    assert response.effective_action == "allow"
    assert not response.findings
