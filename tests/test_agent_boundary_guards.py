"""Positive and benign look-alike examples for every agent boundary signal."""
import pytest
from guardoath.agent_boundary_guards import CASES, inspect

def test_inventory():
    assert len(CASES) == 41

@pytest.mark.parametrize("rule_id", sorted(CASES))
def test_positive_and_lookalike(rule_id):
    _pattern,positive,negative = CASES[rule_id]
    assert rule_id in {x.rule_id for x in inspect(positive)}, rule_id
    assert rule_id not in {x.rule_id for x in inspect(negative)}, rule_id

def test_no_customer_data_returned():
    text="aws guardduty delete-detector secret-customer-string"
    assert "secret-customer-string" not in repr(inspect(text))

def test_invalid_inputs_fail_closed():
    with pytest.raises(TypeError):
        inspect(None)
    with pytest.raises(ValueError):
        inspect("a"*32001)
