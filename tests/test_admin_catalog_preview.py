"""Owner metadata proposal preflight never changes the published registry."""
from copy import deepcopy

import pytest

from guardoath.admin_catalog_preview import (
    CatalogPreviewConflict, CatalogPreviewUnavailable, preview_rule_description,
)

BASE = {"registry_version": "3", "domains": [
    {"domain_id": "privacy", "name": "Privacy", "status": "active", "controls": [
        {"control_id": "pii", "name": "PII", "stages": ["input", "output"],
         "rules": [{"rule_id": "pii.email", "title": "Email rule",
                    "use_case": "Protect personal email addresses"}]}
    ]}
]}


def args():
    return dict(expected_registry_version="3", proposed_registry_version="4",
                category_id="privacy", check_id="pii", rule_id="pii.email",
                title="Better email rule", use_case="Protect personal email addresses")


def test_preview_only_and_no_source_mutation():
    saved = deepcopy(BASE)
    out = preview_rule_description(lambda: BASE, **args())
    assert out["status"] == "preview_only" and out["published"] is False
    assert out["persisted"] is False and out["tenant_policies_changed"] is False
    assert out["detector_behavior_changed"] is False
    assert out["registry_version"] == "3" and out["candidate_registry_version"] == "4"
    assert out["changes"] == {"title": {"before": "Email rule", "after": "Better email rule"}}
    assert BASE == saved


@pytest.mark.parametrize("change, expected_exception", [
    ({"expected_registry_version": "2"}, CatalogPreviewConflict),
    ({"proposed_registry_version": "3"}, CatalogPreviewConflict),
    ({"proposed_registry_version": "5"}, CatalogPreviewConflict),
    ({"proposed_registry_version": "04"}, ValueError),
    ({"rule_id": "no.such.rule"}, CatalogPreviewConflict),
    ({"category_id": "security"}, CatalogPreviewConflict),
    ({"check_id": "other"}, CatalogPreviewConflict),
    ({"title": "Email rule"}, ValueError),
    ({"title": "\rOverwrite"}, ValueError),
    ({"title": "x" * 121}, ValueError),
    ({"rule_id": "../../file"}, ValueError),
    ({"use_case": "\x00bad"}, ValueError),
])
def test_reject_stale_nondistinct_malformed_and_unknown(change, expected_exception):
    proposed = args()
    proposed.update(change)
    with pytest.raises(expected_exception):
        preview_rule_description(lambda: BASE, **proposed)


def test_duplicate_published_target_cannot_be_previewed():
    duplicated = deepcopy(BASE)
    rules = duplicated["domains"][0]["controls"][0]["rules"]
    rules.append(deepcopy(rules[0]))
    with pytest.raises(CatalogPreviewConflict):
        preview_rule_description(lambda: duplicated, **args())


def test_unavailable_provider_and_malformed_version_fail_closed():
    def failed():
        raise RuntimeError("internal provider details")
    with pytest.raises(CatalogPreviewUnavailable):
        preview_rule_description(failed, **args())
    data = deepcopy(BASE)
    data["registry_version"] = "unknown"
    with pytest.raises(CatalogPreviewUnavailable):
        preview_rule_description(lambda: data, **args())


def test_two_field_preview_trims_but_does_not_write():
    proposed = args()
    proposed["title"] = " New description "
    proposed["use_case"] = " Updated safe case "
    out = preview_rule_description(lambda: BASE, **proposed)
    assert out["changed_fields"] == ["title", "use_case"]
    assert out["changes"]["title"]["after"] == "New description"
