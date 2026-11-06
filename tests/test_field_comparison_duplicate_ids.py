"""Duplicate Field Comparison IDs must not silently collapse tenant rules.

These tests cover standalone Policy and the authoritative tenant record; a
comparison count limit is not introduced. Only synthetic pointers are used.
"""
import pytest
from pydantic import ValidationError

from guardoath.models import FieldComparison, Policy
from guardoath.record_rules import record_rule_ids, record_rules
from guardoath.tenant_record import TenantRecord


def comparison(rule_id, operator="lt", left="/amount", right="/limit"):
    return FieldComparison(rule_id=rule_id, left=left, right=right, operator=operator)


def tenant(*comparisons, **fields):
    return TenantRecord(tenant_id="collision-test", display_name="Collision Test", mode="enforce",
                        input_check_cross_field="block", comparisons=tuple(comparisons), **fields)


@pytest.mark.parametrize("factory", [
    lambda items: Policy(application_id="demo", version="v1", rules=(), comparisons=tuple(items)),
    lambda items: tenant(*items),
])
def test_duplicate_comparison_ids_are_rejected_without_silently_dropping_a_rule(factory):
    first = comparison("same_id")
    second = comparison("same_id", operator="ge", left="/tax", right="/base")
    with pytest.raises(ValidationError, match="Comparison rule IDs must be distinct"):
        factory((first, second))


def test_distinct_comparison_ids_preserve_both_record_rules_and_tenant_actions():
    record = tenant(comparison("less_than"), comparison("greater_than", "gt", "/tax", "/base"),
                    input_rule_actions={"business.less_than": "off", "business.greater_than": "flag"})
    ids = record_rule_ids(record)
    assert ids == {"business.less_than": "cross_field", "business.greater_than": "cross_field"}
    assert set(record_rules(record)) == set(ids)
    policy = record.to_policy()
    assert len(policy.comparisons) == 2
    assert policy.input_rule_actions == {"business.less_than": "off", "business.greater_than": "flag"}


def test_no_arbitrary_tenant_comparison_count_cap_is_reintroduced():
    comparisons = tuple(
        comparison(f"comparison_{i:03d}", left=f"/left_{i}", right=f"/right_{i}")
        for i in range(51)
    )
    record = tenant(*comparisons)
    assert len(record.comparisons) == len(record.to_policy().comparisons) == 51
    policy = Policy(application_id="demo", version="v1", rules=(), comparisons=comparisons)
    assert len(policy.comparisons) == 51
