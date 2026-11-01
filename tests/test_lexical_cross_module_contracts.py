"""Cross-module contracts for the two pending lexical batches.

These tests exercise all detectors as a combined signal surface, not just the
individual regex under test. Canonicalization is explicitly a proposed review
behavior and does not grant permission or block tool execution.
"""
import pytest

from guardoath import (
    semantic_guards, agent_extended_guards, data_agent_guards,
    agent_workflow_guards, agent_boundary_guards, agent_config_guards,
)
from guardoath.lexical_finding_consolidation import consolidate

def raw_ids(content):
    results = (
        semantic_guards.inspect_text(content)
        + agent_extended_guards.inspect_extended(content)
        + data_agent_guards.inspect_text(content)
        + agent_workflow_guards.inspect_text(content)
        + agent_boundary_guards.inspect(content)
        + agent_config_guards.inspect(content)
    )
    return tuple(item.rule_id for item in results)

@pytest.mark.parametrize("rule_id", sorted(agent_boundary_guards.CASES))
def test_boundary_positive_has_one_canonical_finding(rule_id):
    _, positive, negative = agent_boundary_guards.CASES[rule_id]
    assert consolidate(raw_ids(positive)) == (rule_id,)
    assert consolidate(raw_ids(negative)) == ()

@pytest.mark.parametrize("rule_id", sorted(agent_config_guards.CASES))
def test_configuration_positive_has_one_canonical_finding(rule_id):
    _, positive, negative = agent_config_guards.CASES[rule_id]
    expected = consolidate((rule_id,))
    assert consolidate(raw_ids(positive)) == expected
    assert consolidate(raw_ids(negative)) == ()

def test_all_rules_are_unique_across_source_modules():
    ids = (
        tuple(semantic_guards.RULES) + tuple(agent_extended_guards.RULES)
        + tuple(data_agent_guards.RULES) + tuple(agent_workflow_guards.RULES)
        + tuple(agent_boundary_guards.CASES) + tuple(agent_config_guards.CASES)
    )
    assert len(ids) == len(set(ids)) == 161
