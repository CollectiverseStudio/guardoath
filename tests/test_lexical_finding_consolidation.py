"""Contract tests for lexical finding consolidation."""
import pytest

from guardoath.lexical_finding_consolidation import ALIASES, consolidate

def test_alias_inventory_is_explicit():
    assert len(ALIASES) == 8
    assert len(set(ALIASES.values())) == 8

@pytest.mark.parametrize("alias,canonical", sorted(ALIASES.items()))
def test_alias_and_original_collapse_to_one(alias, canonical):
    assert consolidate((alias, canonical)) == (canonical,)
    assert consolidate((canonical, alias)) == (canonical,)
    assert consolidate((alias, alias)) == (canonical,)

def test_distinct_risks_are_preserved():
    ids = ("agent5.disable_tls_cert", "agent5.disable_audit_setting",
           "workflow.cross_tenant", "data.sql_disable_rls")
    assert consolidate(ids) == ids

def test_unknown_ids_are_not_silently_dropped():
    assert consolidate(("new.rule", "new.rule", "another.rule")) == ("new.rule", "another.rule")

def test_stable_order_and_no_input_echo():
    ids = ("workflow.cross_tenant", "config6.verify_tls", "agent5.disable_tls_cert")
    assert consolidate(ids) == ("workflow.cross_tenant", "agent5.disable_tls_cert")

@pytest.mark.parametrize("invalid", ["string", b"bytes", ("",), (None,), ("x"*129,)])
def test_invalid_input_rejected(invalid):
    with pytest.raises((TypeError, ValueError)):
        consolidate(invalid)

def test_no_rules_are_executed():
    # Consolidation only changes identifiers; the host must still enforce its policy.
    assert consolidate(()) == ()
