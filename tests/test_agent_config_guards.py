"""40 positive/negative configuration indicator cases plus bounds."""
import pytest
from guardoath.agent_config_guards import CASES, FIELDS, inspect

def test_inventory():
    assert len(FIELDS) == len(CASES) == 40
    assert len(set(FIELDS)) == 40

@pytest.mark.parametrize("rule_id", sorted(CASES))
def test_positive_and_negative(rule_id):
    _regex, positive, negative = CASES[rule_id]
    assert rule_id in {s.rule_id for s in inspect(positive)}
    assert rule_id not in {s.rule_id for s in inspect(negative)}

def test_no_substring_field_confusion():
    assert not inspect("prefix_approval_required=false")

def test_no_customer_content_in_results():
    assert "customer-secret" not in repr(inspect("approval_required=false customer-secret"))

def test_budget_and_types():
    with pytest.raises(TypeError):
        inspect(None)
    with pytest.raises(ValueError):
        inspect("a"*32001)
