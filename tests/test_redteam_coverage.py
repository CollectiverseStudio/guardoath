"""Red-team coverage evidence must not overstate lexical defenses."""
from tools.redteam_coverage import ADVANCED_GAPS, REQUIRED_FAMILIES, audit


def test_redteam_audit_reconciles_real_rule_catalog_and_documents_gaps():
    result = audit()
    assert result["catalog_consistent"]
    assert not result["malformed_rule_ids"]
    assert result["lexical_rules"] >= 36
    assert set(result["families"]) == set(REQUIRED_FAMILIES)
    assert all(family["present"] for family in result["families"].values())
    assert result["semantic_effectiveness_verified"] is False
    assert result["external_provider_required"] is False
    assert len(result["unverified_advanced_capabilities"]) == len(ADVANCED_GAPS)
    assert "cross-turn semantic intent across nonadjacent messages" in result["unverified_advanced_capabilities"]


def test_redteam_audit_is_deterministic_and_json_serializable():
    import json
    first = json.dumps(audit(), sort_keys=True)
    assert first == json.dumps(audit(), sort_keys=True)
    assert "lexical_rule_ids" in first
    assert "semantic_effectiveness_verified" in first
