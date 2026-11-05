"""Standalone synthetic ledger validation contracts across both stages.

This validates declared arithmetic and safe failures, not financial authorization,
account balances, provenance, or fraud prevention.
"""
import asyncio
import json
from pathlib import Path

import pytest

from guardoath.checkers import default_checkers
from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.models import CheckRequest, Stage


ROOT = Path(__file__).resolve().parents[1]
ENGINE = Engine(load_policies(ROOT / "policies" / "ledger-batch.json"), default_checkers())


def assess(document, stage, *, fmt="json"):
    content = document if isinstance(document, str) else json.dumps(document, separators=(",", ":"))
    request = CheckRequest(application_id="demo", policy_version="ledger-enforce-v1",
                           content=content, content_format=fmt)
    return asyncio.run(ENGINE.check(request, stage))


def ledger(*entries, currency="USD"):
    return {"currency": currency, "entries": [
        {"entry_id": identifier, "side": side, "amount": amount}
        for identifier, side, amount in entries
    ]}


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("document", [
    ledger(("d1", "debit", "0.10"), ("d2", "debit", "0.20"),
           ("c1", "credit", "0.30")),
    ledger(("d1", "debit", "0.00"), ("c1", "credit", "0.00")),
    ledger(("d1", "debit", "999999999999.99"),
           ("c1", "credit", "999999999999.99")),
])
def test_exact_integer_minor_units_balance_without_floating_rounding(stage, document):
    response = assess(document, stage)
    assert response.mode == "enforce"
    assert response.evaluation_status == "complete"
    assert not response.findings
    assert response.recommended_action == "allow"
    assert response.effective_action == "allow"
    assert any(c.checker_id == "ledger" and c.status == "passed"
               for c in response.checker_results)


@pytest.mark.parametrize("stage,expected_action", [
    (Stage.INPUT, "flag"), (Stage.OUTPUT, "block"),
])
@pytest.mark.parametrize("document,expected", [
    (ledger(("d1", "debit", "0.10"), ("c1", "credit", "0.11")),
     {"business.ledger_unbalanced"}),
    (ledger(("d1", "debit", "0.10"), ("c1", "credit", "0.10"), currency="EUR"),
     {"business.ledger_currency"}),
    (ledger(("d1", "debit", "0.10"), ("c1", "credit", "0.11"), currency="EUR"),
     {"business.ledger_currency", "business.ledger_unbalanced"}),
])
def test_independent_financial_findings_survive_combination(stage, expected_action, document, expected):
    response = assess(document, stage)
    assert response.evaluation_status == "complete"
    assert {finding.rule_id for finding in response.findings} == expected
    assert len(response.findings) == len(expected)
    assert response.recommended_action == expected_action
    assert response.effective_action == expected_action
    # Latency metadata may contain the digits 0.11; findings must not disclose amounts.
    assert all("0.11" not in finding.explanation for finding in response.findings)


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("document,error_code", [
    (ledger(("same", "debit", "0.10"), ("same", "credit", "0.10")), "ledger_duplicate_entry"),
    (ledger(("d1", "debit", "0.10"), ("d2", "debit", "0.10")), "ledger_sides_required"),
    (ledger(("d1", "debit", "01.00"), ("c1", "credit", "1.00")), "ledger_invalid_amount"),
    (ledger(("d1", "debit", "-1.00"), ("c1", "credit", "1.00")), "ledger_invalid_amount"),
    (ledger(("d1", "debit", "1e2"), ("c1", "credit", "1.00")), "ledger_invalid_amount"),
    (ledger(("d1", "debit", "1.0"), ("c1", "credit", "1.00")), "ledger_invalid_amount"),
    (ledger(("d1", "debit", "1000000000000.00"), ("c1", "credit", "1.00")), "ledger_invalid_amount"),
    (ledger(("d1", "debit", 1.0), ("c1", "credit", "1.00")), "ledger_invalid_document"),
])
def test_invalid_ledger_input_fails_closed_without_values(stage, document, error_code):
    response = assess(document, stage)
    assert response.evaluation_status == "incomplete"
    assert not response.findings
    assert response.recommended_action == "block"
    assert response.effective_action == "block"
    assert any(c.checker_id == "ledger" and c.status == "error"
               and c.error_code == error_code for c in response.checker_results)
    assert "1000000000000.00" not in response.model_dump_json()


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("content", [
    '{"currency":"USD","currency":"EUR","entries":[]}',
    '{"currency":"USD","entries":[{"entry_id":"d1","side":"debit","amount":"1.00"}',
])
def test_strict_ledger_json_rejects_duplicate_keys_and_truncation(stage, content):
    response = assess(content, stage)
    assert response.evaluation_status == "incomplete"
    assert response.recommended_action == "block"
    assert response.effective_action == "block"
    assert not response.findings
    assert any(c.checker_id == "ledger" and c.status == "error"
               and c.error_code == "ledger_invalid_document" for c in response.checker_results)
    assert content not in response.model_dump_json()


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
def test_ledger_wrong_format_is_incomplete_not_safe(stage):
    response = assess("debits 1, credits 1", stage, fmt="text")
    assert response.evaluation_status == "incomplete"
    assert response.recommended_action == "block"
    assert response.effective_action == "block"
    assert any(c.checker_id == "ledger" and c.status == "skipped"
               and c.error_code == "ledger_json_required" for c in response.checker_results)


def entries_at_limit(extra=False):
    """A balanced 100-row ledger, optionally with one extra debit row."""
    rows = [(f"d{i:03d}", "debit", "1.00") for i in range(50)]
    rows += [(f"c{i:03d}", "credit", "1.00") for i in range(50)]
    if extra:
        rows.append(("over_limit", "debit", "1.00"))
    return ledger(*rows)


@pytest.mark.parametrize("stage", [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize("over_limit", [False, True])
def test_exact_configured_ledger_entry_limit(stage, over_limit):
    response = assess(entries_at_limit(over_limit), stage)
    if over_limit:
        assert response.evaluation_status == "incomplete"
        assert response.recommended_action == "block"
        assert response.effective_action == "block"
        assert not response.findings
        assert any(c.checker_id == "ledger" and c.status == "error"
                   and c.error_code == "ledger_entry_limit" for c in response.checker_results)
    else:
        assert response.evaluation_status == "complete"
        assert not response.findings
        assert response.recommended_action == "allow"
        assert response.effective_action == "allow"
        assert any(c.checker_id == "ledger" and c.status == "passed"
                   for c in response.checker_results)
    assert "over_limit" not in response.model_dump_json()
