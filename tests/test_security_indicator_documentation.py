"""Keep the 161-indicator reference aligned with executable rule inventories.

This checks documentation completeness, not detection accuracy or UC001–UC200
implementation. It deliberately does not register any new production checker.
"""
from pathlib import Path
import re

from guardoath import (
    semantic_guards,
    agent_extended_guards,
    data_agent_guards,
    agent_workflow_guards,
    agent_boundary_guards,
    agent_config_guards,
)

DOC = Path(__file__).resolve().parents[1] / "docs" / "security-indicator-use-cases.md"

def inventory():
    groups = (
        set(semantic_guards.RULES),
        set(agent_extended_guards.RULES),
        set(data_agent_guards.RULES),
        set(agent_workflow_guards.RULES),
        set(agent_boundary_guards.CASES),
        set(agent_config_guards.CASES),
    )
    return groups

def test_documentation_contains_every_real_rule_once():
    groups = inventory()
    assert tuple(map(len, groups)) == (20, 20, 20, 20, 41, 40)
    all_ids = set().union(*groups)
    assert len(all_ids) == 161
    doc = DOC.read_text(encoding="utf-8")
    documented = re.findall(r"^\|\s*\d+\s*\|\s*`([a-z0-9_.]+)`\s*\|", doc, re.M)
    assert len(documented) == len(set(documented)) == 161
    assert set(documented) == all_ids

def test_documentation_has_161_nonempty_use_cases_and_examples():
    doc = DOC.read_text(encoding="utf-8")
    rows = [line for line in doc.splitlines() if re.match(r"^\|\s*\d+\s*\|\s*`", line)]
    assert len(rows) == 161
    for row in rows:
        cells = row.split("|")
        assert len(cells) >= 6
        assert len(cells[3].strip()) > 35, row
        assert cells[4].strip().startswith("`"), row

def test_documentation_does_not_claim_all_catalog_cases_complete():
    doc = DOC.read_text(encoding="utf-8")
    assert "not** 161 completed catalog use cases" in doc
    assert "UC001–UC200" in doc
    assert "not registered" in doc
