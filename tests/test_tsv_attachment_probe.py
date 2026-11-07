"""TSV byte classification and formula detection with bounded parser semantics."""
import asyncio
import hashlib

import pytest

from guardoath.attachment_probe import classify_attachment
from guardoath.csv_formula import csv_formula_check
from guardoath.models import CheckRequest, Policy, Rule, Stage


@pytest.mark.parametrize("data", [
    b"name\tvalue\nSynthetic\t3",
    b"name\tvalue\r\nSynthetic\t3",
    b"name\tvalue\nSynthetic\t'=2+3",
    b'name\tvalue\nSynthetic\t"contains, comma"',
    b'\xef\xbb\xbfname\tvalue\nSynthetic\t3',
])
def test_structural_tsv_supported_without_mime_or_filename(data):
    probe = classify_attachment(data)
    assert (probe.kind, probe.status, probe.reason) == (
        "tsv", "recognized", "tsv_structure_recognized_only"
    )
    assert probe.sha256 == hashlib.sha256(data).hexdigest()
    assert probe.byte_count == len(data)


@pytest.mark.parametrize("data, expected", [
    (b"name\tvalue\nSynthetic\t=2+3", "flagged"),
    (b"name\tvalue\nSynthetic\t+2+3", "flagged"),
    (b"name\tvalue\nSynthetic\t-2+3", "flagged"),
    (b"name\tvalue\nSynthetic\t@SUM(2,3)", "flagged"),
    (b"name\tvalue\nSynthetic\t'=2+3", "passed"),
    (b"name\tvalue\nSynthetic\t2+3", "passed"),
])
def test_existing_formula_checker_inspects_true_tsv_cells(data, expected):
    policy = Policy(application_id="synthetic", version="v1",
                    rules=(Rule(checker_id="csv_formula", on_finding="block"),))
    request = CheckRequest(application_id="synthetic", policy_version="v1",
                           content=data.decode("utf-8"), content_format="text")
    outcome = asyncio.run(csv_formula_check(request, policy, Stage.INPUT))
    assert outcome.status == expected
    assert {finding.rule_id for finding in outcome.findings} == (
        {"csv_formula.potential_formula"} if expected == "flagged" else set()
    )


@pytest.mark.parametrize("data,expected_reason", [
    (b"name\tvalue\nSynthetic\t3\textra", "inconsistent_csv_columns"),
    (b"name\tvalue\n\"unclosed\t3", "malformed_csv"),
    (b"name\tvalue", "ambiguous_plain_text"),
    (b"name\tvalue\nSynthetic\t\x1b=2+3", "unsafe_csv_control_characters"),
    ("name\tvalue\nSynthetic\t\u202e=2+3".encode("utf-8"), "unsafe_csv_control_characters"),
    ("name\tvalue\nSynthetic\t\u200b=2+3".encode("utf-8"), "unsafe_csv_invisible_formula_prefix"),
])
def test_malformed_or_ambiguous_tsv_cannot_be_recognized(data, expected_reason):
    probe = classify_attachment(data)
    assert probe.status != "recognized"
    assert probe.reason == expected_reason
    assert not probe.csv_text_supported


def test_comma_csv_with_tab_inside_cell_remains_comma_csv():
    probe = classify_attachment(b"name,value\nSynthetic,tab\there")
    assert (probe.kind, probe.status) == ("csv", "recognized")


@pytest.mark.parametrize("data,expected_kind", [
    (b"name\tvalue\nSynthetic\tplain,=2+3", "tsv"),
    (b"name,value\nSynthetic,plain\t=2+3", "csv"),
])
def test_mixed_delimiter_export_cannot_hide_formula_under_other_import_dialect(data, expected_kind):
    receipt = classify_attachment(data)
    assert (receipt.kind, receipt.status) == (expected_kind, "recognized")
    policy = Policy(application_id="synthetic", version="v1",
                    rules=(Rule(checker_id="csv_formula", on_finding="block"),))
    request = CheckRequest(application_id="synthetic", policy_version="v1",
                           content=data.decode("utf-8"), content_format="text")
    result = asyncio.run(csv_formula_check(request, policy, Stage.INPUT))
    assert result.status == "flagged"
    assert {item.rule_id for item in result.findings} == {"csv_formula.potential_formula"}


@pytest.mark.parametrize("data", [
    "name\tvalue\nSynthetic\tplain,\u200b=2+3".encode("utf-8"),
    "name,value\nSynthetic,plain\t\u200b=2+3".encode("utf-8"),
])
def test_hidden_formula_in_other_delimiter_view_is_a_hard_structural_failure(data):
    receipt = classify_attachment(data)
    assert receipt.status == "invalid"
    assert receipt.kind == "unknown"
    assert receipt.reason == "unsafe_csv_invisible_formula_prefix"
