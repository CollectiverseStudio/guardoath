"""Display grouping must preserve every original policy-controlled finding."""
import pytest

from guardoath.lexical_finding_consolidation import ALIASES
from guardoath.lexical_related_groups import related_groups
from guardoath.models import RULE_ID_PATTERN
import re


@pytest.mark.parametrize("alias,canonical", sorted(ALIASES.items()))
@pytest.mark.parametrize("reverse", [False, True])
def test_pair_preserves_both_ids_and_source_positions(alias, canonical, reverse):
    original = (canonical, alias) if reverse else (alias, canonical)
    results = related_groups(original)
    assert len(results) == 1
    assert results[0].concept_id == "display:" + canonical
    assert not re.fullmatch(RULE_ID_PATTERN, results[0].concept_id)
    assert results[0].rule_ids == original
    assert results[0].source_positions == (0, 1)


def test_distinct_risks_original_order_and_duplicates_are_lossless():
    original = ("workflow.cross_tenant", "config6.verify_tls",
                "agent5.disable_tls_cert", "config6.verify_tls",
                "data.sql_disable_rls", "workflow.cross_tenant")
    results = related_groups(original)
    assert tuple(group.concept_id for group in results) == (
        "display:workflow.cross_tenant", "display:agent5.disable_tls_cert",
        "display:data.sql_disable_rls")
    assert results[0].rule_ids == ("workflow.cross_tenant", "workflow.cross_tenant")
    assert results[1].rule_ids == ("config6.verify_tls", "agent5.disable_tls_cert",
                                   "config6.verify_tls")
    recovered = tuple(identifier for _, identifier in sorted(
        ((position, identifier) for group in results
         for position, identifier in zip(group.source_positions, group.rule_ids))))
    assert recovered == original


@pytest.mark.parametrize("invalid", ["identifier", b"bytes", ("",), (None,), ("x"*129,), ("display:agent5.disable_tls_cert",), ("bad id",), ("CONFIG6.verify_tls",), ("config6.verify_tls\n",), ("a.b\x00",), ("a." + "b"*99,), ("a." + "b"*98 + "!",)])
def test_invalid_ids_rejected(invalid):
    with pytest.raises((TypeError, ValueError)):
        related_groups(invalid)


def test_bounded_and_empty():
    assert related_groups(()) == ()
    with pytest.raises(ValueError):
        related_groups("rule." + str(i) for i in range(4097))


def test_immutable_result_keeps_original_identities_and_positions():
    values = ["config6.verify_tls", "agent5.disable_tls_cert"]
    groups = related_groups(values)
    values.append("new.rule")
    assert groups[0].rule_ids == ("config6.verify_tls", "agent5.disable_tls_cert")
    assert groups[0].source_positions == (0, 1)


def test_full_tenant_id_length_boundary_and_unknown_valid_id():
    valid = "a." + "b" * 98
    assert len(valid) == 100
    group = related_groups((valid, "custom.security_rule"))
    assert tuple(item.rule_ids for item in group) == ((valid,), ("custom.security_rule",))
    with pytest.raises(ValueError, match="invalid tenant rule ID"):
        related_groups((valid + "c",))


def test_4096_occurrences_preserve_last_position():
    values = ("config6.verify_tls",) * 4096
    group, = related_groups(iter(values))
    assert len(group.rule_ids) == 4096
    assert group.source_positions[0] == 0
    assert group.source_positions[-1] == 4095
