"""Display grouping must never substitute policy-controlled lexical finding IDs."""
import pytest
from guardoath.lexical_finding_consolidation import ALIASES
from guardoath.lexical_related_groups import related_groups


@pytest.mark.parametrize("alias,canonical", sorted(ALIASES.items()))
@pytest.mark.parametrize("reverse", [False, True])
def test_pair_preserves_both_ids(alias, canonical, reverse):
    original = (canonical, alias) if reverse else (alias, canonical)
    result = related_groups(original)
    assert len(result) == 1
    assert result[0].concept_id == canonical
    assert result[0].rule_ids == original


def test_distinct_risks_and_duplicates():
    original = ("workflow.cross_tenant", "config6.verify_tls",
                "agent5.disable_tls_cert", "data.sql_disable_rls")
    results = related_groups(original)
    assert tuple(group.concept_id for group in results) == (
        "workflow.cross_tenant", "agent5.disable_tls_cert", "data.sql_disable_rls")
    assert tuple(rule for group in results for rule in group.rule_ids) == original
    assert related_groups(("config6.verify_tls", "config6.verify_tls",
        "agent5.disable_tls_cert"))[0].rule_ids == (
            "config6.verify_tls", "agent5.disable_tls_cert")


@pytest.mark.parametrize("invalid", ["identifier", b"bytes", ("",), (None,), ("x"*129,)])
def test_invalid_ids_rejected(invalid):
    with pytest.raises((TypeError, ValueError)):
        related_groups(invalid)


def test_bounded_and_empty():
    assert related_groups(()) == ()
    with pytest.raises(ValueError):
        related_groups("rule." + str(i) for i in range(4097))


def test_immutable_result_keeps_original_identities():
    values = ["config6.verify_tls", "agent5.disable_tls_cert"]
    groups = related_groups(values)
    values.append("new.rule")
    assert groups[0].rule_ids == ("config6.verify_tls", "agent5.disable_tls_cert")
