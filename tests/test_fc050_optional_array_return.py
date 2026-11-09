"""FC-050 optional return event: indexed-array semantics in the real tenant Engine.

A fourth event may not exist yet. Absence must not create a false positive, but
an existing invalid/null/out-of-order return and missing mandatory events must
still produce findings on both stages. All documents and tenants are synthetic.
"""
import json

import pytest

from fastapi.testclient import TestClient

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.models import AdvancedConversionTimeRule
from guardoath.tenant_record import TenantRecord, demo_key, key_hash
from guardoath.tenant_store import MemoryTenantStore


TENANT = "fc050-optional-array"
SECRET = "fc050-optional-array-secret-for-regression-2026"
RULE = {
    "rule_id": "optional_array_return",
    "kind": "lifecycle_chronology",
    "fields": {
        "order_at": "/events/0/at",
        "shipment_at": "/events/1/at",
        "delivery_at": "/events/2/at",
        "return_at": "/events/3/at",
    },
    "stages": ["input", "output"],
}
ORDER = "2026-11-06T09:00:00Z"
SHIPMENT = "2026-11-06T10:00:00Z"
DELIVERY = "2026-11-06T11:00:00Z"
AFTER = "2026-11-06T12:00:00Z"
BEFORE = "2026-11-06T08:00:00Z"


def _events(*values):
    return {"events": [{"at": value} for value in values]}


def _client():
    token = demo_key(TENANT, SECRET)
    record = TenantRecord(
        tenant_id=TENANT,
        display_name="Synthetic optional array return acceptance",
        demo=True,
        monthly_allowance=30,
        overage="block",
        key_hashes=(key_hash(token),),
        input_check_text_security="flag",
        output_check_text_security="flag",
    )
    store = MemoryTenantStore((record,))
    api = TestClient(create_app(
        engine=demo_engine(), credentials={},
        tenants=TenantService(store, default_checkers(), SECRET),
    ))
    return api, store, {"Authorization": "Bearer " + token}


def _check(api, headers, stage, document):
    response = api.post("/v1/checks/" + stage, headers=headers, json={
        "application_id": TENANT,
        "content_format": "json",
        "content": json.dumps(document),
    })
    assert response.status_code == 200, response.text[:200]
    body = response.json()
    assert body["evaluation_status"] == "complete"
    return body


def test_optional_array_return_is_real_tenant_setting_and_both_stage_engine_contract():
    # Refuse invalid rule shape at the authoritative tenant-model boundary.
    validated = AdvancedConversionTimeRule.model_validate(RULE)
    assert validated.fields["return_at"] == "/events/3/at"

    api, store, headers = _client()
    baseline = store.get(TENANT)
    saved = api.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": baseline.revision,
        "changes": {
            "advanced_conversion_time": [RULE],
            "input_check_cross_field": "block",
            "output_check_cross_field": "flag",
        },
    })
    assert saved.status_code == 200, saved.text[:250]
    persisted = store.get(TENANT)
    assert persisted.revision == baseline.revision + 1
    assert persisted.advanced_conversion_time == (validated,)
    public = api.get("/v1/applications/current", headers=headers)
    assert public.status_code == 200
    assert public.json()["tenant"]["advanced_conversion_time"][0]["rule_id"] == RULE["rule_id"]
    assert "business.optional_array_return" in {
        row["rule_id"] for row in public.json()["tenant"]["record_rules"]
    }

    clean_before_return = _events(ORDER, SHIPMENT, DELIVERY)
    clean_after_return = _events(ORDER, SHIPMENT, DELIVERY, AFTER)
    invalid_reverse = _events(ORDER, SHIPMENT, DELIVERY, BEFORE)
    invalid_null = _events(ORDER, SHIPMENT, DELIVERY, None)
    # Once an actual return event exists, its timestamp is mandatory.
    # Treating the absent /events/3/at key as an optional absent *event*
    # would silently accept malformed lifecycle records.
    present_return_missing_time = {
        "events": [{"at": ORDER}, {"at": SHIPMENT}, {"at": DELIVERY}, {}]
    }
    missing_mandatory = _events(ORDER, SHIPMENT)
    for stage, action in (("input", "block"), ("output", "flag")):
        for clean in (clean_before_return, clean_after_return):
            result = _check(api, headers, stage, clean)
            assert not any(f["rule_id"] == "business.optional_array_return" for f in result["findings"])
            assert result["effective_action"] == "allow"
        for bad in (invalid_reverse, invalid_null, present_return_missing_time, missing_mandatory):
            result = _check(api, headers, stage, bad)
            assert {f["rule_id"] for f in result["findings"]} == {"business.optional_array_return"}
            assert result["effective_action"] == action
            assert all("2026-11-06" not in f["explanation"] for f in result["findings"])

    # Same tenant revision must reject concurrent stale updates, not erase the rule.
    rejected = api.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": baseline.revision,
        # Change a valid action so stale-revision admission is tested independently
        # of the legitimate 422 safeguard against removing the only active rule.
        "changes": {"input_check_cross_field": "flag"},
    })
    assert rejected.status_code == 409
    assert store.get(TENANT).advanced_conversion_time == (validated,)



def test_optional_nested_return_object_requires_timestamp_when_object_is_present():
    """A missing optional event and a recorded event missing its timestamp differ."""
    from guardoath.advanced_conversion_time import conversion_time_findings

    nested_rule = AdvancedConversionTimeRule.model_validate({
        "rule_id": "nested_return_event",
        "kind": "lifecycle_chronology",
        "fields": {
            "order_at": "/times/order",
            "shipment_at": "/times/shipment",
            "delivery_at": "/times/delivery",
            "return_at": "/times/return/at",
        },
        "stages": ["input", "output"],
    })
    base = {"times": {"order": ORDER, "shipment": SHIPMENT, "delivery": DELIVERY}}
    present = {"times": {**base["times"], "return": {"at": AFTER}}}
    invalid = {"times": {**base["times"], "return": {}}}
    for stage in ("input", "output"):
        assert conversion_time_findings(base, (nested_rule,), stage) == ()
        assert conversion_time_findings(present, (nested_rule,), stage) == ()
        findings = conversion_time_findings(invalid, (nested_rule,), stage)
        assert [f.rule_id for f in findings] == ["business.nested_return_event"]
        assert "2026-11-06" not in findings[0].explanation


def test_optional_root_return_field_can_be_absent_but_not_null():
    from guardoath.advanced_conversion_time import conversion_time_findings

    root_rule = AdvancedConversionTimeRule.model_validate({
        "rule_id": "root_return_event",
        "kind": "lifecycle_chronology",
        "fields": {
            "order_at": "/times/order",
            "shipment_at": "/times/shipment",
            "delivery_at": "/times/delivery",
            "return_at": "/return_at",
        },
        "stages": ["input", "output"],
    })
    base = {"times": {"order": ORDER, "shipment": SHIPMENT, "delivery": DELIVERY}}
    for stage in ("input", "output"):
        assert conversion_time_findings(base, (root_rule,), stage) == ()
        invalid = {**base, "return_at": None}
        assert [f.rule_id for f in conversion_time_findings(invalid, (root_rule,), stage)] == [
            "business.root_return_event"
        ]


def test_same_object_optional_return_timestamp_remains_omittable():
    """An optional scalar alongside mandatory timestamps is not a return object."""
    from guardoath.advanced_conversion_time import conversion_time_findings

    rule = AdvancedConversionTimeRule.model_validate({
        "rule_id": "same_object_optional_return",
        "kind": "lifecycle_chronology",
        "fields": {
            "order_at": "/life/order_at",
            "shipment_at": "/life/shipment_at",
            "delivery_at": "/life/delivery_at",
            "return_at": "/life/return_at",
        },
        "stages": ["input", "output"],
    })
    life = {"order_at": ORDER, "shipment_at": SHIPMENT, "delivery_at": DELIVERY}
    for stage in ("input", "output"):
        assert conversion_time_findings({"life": life}, (rule,), stage) == ()
        assert conversion_time_findings({"life": {**life, "return_at": AFTER}}, (rule,), stage) == ()
        found = conversion_time_findings({"life": {**life, "return_at": None}}, (rule,), stage)
        assert [f.rule_id for f in found] == ["business.same_object_optional_return"]


def test_nested_array_return_event_requires_complete_timestamp_through_tenant_api():
    """A recorded fourth event may not hide a missing deeper array timestamp.

    This exercises the saved rule, same shared Engine, both tenant stages and
    the real hosted API. An unrecorded fourth event remains legitimate.
    """
    nested = {
        **RULE,
        "rule_id": "nested_array_return",
        "fields": {**RULE["fields"], "return_at": "/events/3/details/0/at"},
    }
    api, store, headers = _client()
    initial = store.get(TENANT)
    saved = api.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": initial.revision,
        "changes": {
            "advanced_conversion_time": [nested],
            "input_check_cross_field": "block",
            "output_check_cross_field": "flag",
        },
    })
    assert saved.status_code == 200, saved.text[:200]
    persisted = store.get(TENANT)
    assert persisted.revision == initial.revision + 1
    assert persisted.advanced_conversion_time[0].fields["return_at"] == nested["fields"]["return_at"]
    listing = api.get("/v1/applications/current", headers=headers)
    assert listing.status_code == 200
    assert "business.nested_array_return" in {
        row["rule_id"] for row in listing.json()["tenant"]["record_rules"]
    }

    first_three = [{"at": ORDER}, {"at": SHIPMENT}, {"at": DELIVERY}]
    good = [
        {"events": first_three},
        {"events": first_three + [{"details": [{"at": AFTER}]}]},
    ]
    bad = [
        # The fourth event exists. Only the nested timestamp is missing.
        {"events": first_three + [{}]},
        {"events": first_three + [{"details": []}]},
        {"events": first_three + [{"details": [{}]}]},
        {"events": first_three + [{"details": [{"at": None}]}]},
        {"events": first_three + [{"details": [{"at": BEFORE}]}]},
        # The missing third *mandatory* event is never optional.
        {"events": first_three[:2]},
    ]
    for stage, action in (("input", "block"), ("output", "flag")):
        for document in good:
            response = _check(api, headers, stage, document)
            assert response["effective_action"] == "allow"
            assert not any(
                item["rule_id"] == "business.nested_array_return"
                for item in response["findings"]
            )
        for document in bad:
            response = _check(api, headers, stage, document)
            assert response["effective_action"] == action
            assert {item["rule_id"] for item in response["findings"]} == {
                "business.nested_array_return"
            }
            assert all(
                ORDER not in item["explanation"] and BEFORE not in item["explanation"]
                for item in response["findings"]
            )


def test_optional_event_boundary_is_not_hidden_by_a_close_mandatory_sibling():
    """A recorded return cannot disappear when another required path shares its subtree.

    Using only the deepest mandatory common prefix would select
    /life/return/details as the optional node. If details was absent it
    would wrongly accept the return even though /life/return exists.
    """
    rule = {
        **RULE,
        "rule_id": "shared_return_parent_boundary",
        "fields": {
            "order_at": "/life/order",
            "shipment_at": "/life/shipment",
            "delivery_at": "/life/return/received",
            "return_at": "/life/return/details/0/at",
        },
    }
    api, store, headers = _client()
    baseline = store.get(TENANT)
    saved = api.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": baseline.revision,
        "changes": {
            "advanced_conversion_time": [rule],
            "input_check_cross_field": "block",
            "output_check_cross_field": "flag",
        },
    })
    assert saved.status_code == 200, saved.text[:200]
    actual = store.get(TENANT)
    assert actual.revision == baseline.revision + 1
    assert actual.advanced_conversion_time[0].fields == rule["fields"]
    listing = api.get("/v1/applications/current", headers=headers)
    assert listing.status_code == 200
    assert "business.shared_return_parent_boundary" in {
        row["rule_id"] for row in listing.json()["tenant"]["record_rules"]
    }

    base = {"life": {"order": ORDER, "shipment": SHIPMENT,
                     "return": {"received": DELIVERY}}}
    valid = {"life": {"order": ORDER, "shipment": SHIPMENT,
                      "return": {"received": DELIVERY,
                                 "details": [{"at": AFTER}]}}}
    invalid = [
        base,
        {"life": {"order": ORDER, "shipment": SHIPMENT,
                  "return": {"received": DELIVERY, "details": []}}},
        {"life": {"order": ORDER, "shipment": SHIPMENT,
                  "return": {"received": DELIVERY, "details": [{}]}}},
    ]
    for stage, action in (("input", "block"), ("output", "flag")):
        good = _check(api, headers, stage, valid)
        assert good["effective_action"] == "allow"
        assert not any(f["rule_id"] == "business.shared_return_parent_boundary"
                       for f in good["findings"])
        for document in invalid:
            result = _check(api, headers, stage, document)
            assert result["effective_action"] == action
            assert {f["rule_id"] for f in result["findings"]} == {
                "business.shared_return_parent_boundary"
            }
            assert all(ORDER not in f["explanation"] and DELIVERY not in f["explanation"]
                       for f in result["findings"])


def test_fc050_dashboard_samples_execute_as_advertised_with_shared_return_parent():
    """The customer-visible look-alike must pass the saved tenant rule.

    A mandatory delivery timestamp inside the return record makes a generated
    three-field example malformed, even though an entirely unrecorded return
    remains optional for normal event-array configurations.
    """
    rule = {
        **RULE,
        "rule_id": "sample_shared_return_record",
        "fields": {
            "order_at": "/life/order",
            "shipment_at": "/life/shipment",
            "delivery_at": "/life/return/received",
            "return_at": "/life/return/details/0/at",
        },
    }
    api, store, headers = _client()
    baseline = store.get(TENANT)
    saved = api.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": baseline.revision,
        "changes": {
            "advanced_conversion_time": [rule],
            "input_check_cross_field": "block",
            "output_check_cross_field": "flag",
        },
    })
    assert saved.status_code == 200, saved.text[:250]
    listing = api.get("/v1/applications/current", headers=headers)
    assert listing.status_code == 200
    sample = next(
        item for item in listing.json()["tenant"]["record_rules"]
        if item["rule_id"] == "business.sample_shared_return_record"
    )
    clean = json.loads(sample["look_alike"])
    violating = json.loads(sample["example"])
    assert clean["life"]["return"]["details"][0]["at"] == AFTER.replace("12:00", "13:00")
    assert violating["life"]["return"]["details"][0]["at"] == clean["life"]["return"]["details"][0]["at"]
    for stage, action in (("input", "block"), ("output", "flag")):
        clean_response = _check(api, headers, stage, clean)
        assert not any(
            found["rule_id"] == "business.sample_shared_return_record"
            for found in clean_response["findings"]
        )
        assert clean_response["effective_action"] == "allow"
        invalid_response = _check(api, headers, stage, violating)
        assert {found["rule_id"] for found in invalid_response["findings"]} == {
            "business.sample_shared_return_record"
        }
        assert invalid_response["effective_action"] == action
        assert all(
            ORDER not in found["explanation"] and AFTER not in found["explanation"]
            for found in invalid_response["findings"]
        )
    assert store.get(TENANT).advanced_conversion_time[0].rule_id == rule["rule_id"]



def test_explicit_disjoint_return_event_uses_saved_tenant_and_rejects_incomplete_records():
    """The return event is optional; its timestamp is required once recorded."""
    from pydantic import ValidationError
    import pytest

    config = {
        **RULE,
        "rule_id": "explicit_return_event",
        "fields": {
            "order_at": "/order/at",
            "shipment_at": "/shipment/at",
            "delivery_at": "/delivery/at",
            "return_at": "/returns/latest/at",
        },
        "return_event_pointer": "/returns/latest",
    }
    expected = AdvancedConversionTimeRule.model_validate(config)
    for invalid in ("/returns/latest/at", "/returns/other", "/returns/~2latest", "", "/"):
        with pytest.raises(ValidationError):
            AdvancedConversionTimeRule.model_validate({**config, "return_event_pointer": invalid})

    api, store, headers = _client()
    baseline = store.get(TENANT)
    saved = api.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": baseline.revision,
        "changes": {
            "advanced_conversion_time": [config],
            "input_check_cross_field": "block",
            "output_check_cross_field": "flag",
        },
    })
    assert saved.status_code == 200, saved.text[:250]
    persisted = store.get(TENANT)
    assert persisted.advanced_conversion_time == (expected,)
    public = api.get("/v1/applications/current", headers=headers)
    assert public.status_code == 200
    assert public.json()["tenant"]["advanced_conversion_time"][0]["return_event_pointer"] == "/returns/latest"
    assert "business.explicit_return_event" in {
        row["rule_id"] for row in public.json()["tenant"]["record_rules"]
    }

    base = {
        "order": {"at": ORDER},
        "shipment": {"at": SHIPMENT},
        "delivery": {"at": DELIVERY},
        "returns": {},  # Return branch exists, but no return event is recorded.
    }
    good = [base, {**base, "returns": {"latest": {"at": AFTER}}}]
    bad = [
        {**base, "returns": {"latest": {}}},
        {**base, "returns": {"latest": None}},
        {**base, "returns": {"latest": {"at": None}}},
        {**base, "returns": {"latest": {"at": BEFORE}}},
        {**base, "shipment": {}},
    ]
    for stage, action in (("input", "block"), ("output", "flag")):
        for document in good:
            result = _check(api, headers, stage, document)
            assert result["effective_action"] == "allow"
            assert not any(row["rule_id"] == "business.explicit_return_event"
                           for row in result["findings"])
        for document in bad:
            result = _check(api, headers, stage, document)
            assert result["effective_action"] == action
            assert {row["rule_id"] for row in result["findings"]} == {
                "business.explicit_return_event"
            }
            assert all(ORDER not in row["explanation"] and BEFORE not in row["explanation"]
                       for row in result["findings"])

    rejected = api.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": persisted.revision,
        "changes": {"advanced_conversion_time": [
            {**config, "return_event_pointer": "/returns/other"}
        ]},
    })
    assert rejected.status_code == 422
    assert store.get(TENANT).revision == persisted.revision
    assert store.get(TENANT).advanced_conversion_time == (expected,)

def test_fc050_event_boundary_must_not_contain_mandatory_timestamp_paths():
    """A required event inside an optional root makes valid returns appear incomplete."""
    base = {
        **RULE,
        "rule_id": "invalid_optional_event_root",
        "fields": {
            "order_at": "/order/at",
            "shipment_at": "/shipment/at",
            "delivery_at": "/returns/delivered/at",
            "return_at": "/returns/latest/at",
        },
        "return_event_pointer": "/returns",
    }
    # Escaped slash segments obey the same structural rules.
    for candidate in (
        base,
        {
            **base,
            "fields": {
                **base["fields"],
                "delivery_at": "/audit~1returns/delivered/at",
                "return_at": "/audit~1returns/latest/at",
            },
            "return_event_pointer": "/audit~1returns",
        },
    ):
        with pytest.raises(ValueError, match="cannot contain mandatory lifecycle timestamps"):
            AdvancedConversionTimeRule.model_validate(candidate)
        api, store, headers = _client()
        before = store.get(TENANT)
        rejected = api.post("/v1/tenants/current/checks", headers=headers, json={
            "revision": before.revision,
            "changes": {
                "advanced_conversion_time": [candidate],
                "input_check_cross_field": "block",
                "output_check_cross_field": "flag",
            },
        })
        assert rejected.status_code == 422
        assert store.get(TENANT) == before

    # A disjoint optional return event is still allowed, even when another
    # required field lives elsewhere within the same document.
    valid = {**base, "fields": {**base["fields"], "delivery_at": "/delivery/at"}}
    accepted = AdvancedConversionTimeRule.model_validate(valid)
    assert accepted.return_event_pointer == "/returns"


@pytest.mark.parametrize("name,fields,expected_absent", [
    (
        "indexed_array",
        RULE["fields"],
        True,
    ),
    (
        "disjoint_return_branch",
        {
            "order_at": "/order/at",
            "shipment_at": "/shipment/at",
            "delivery_at": "/delivery/at",
            "return_at": "/returns/latest/at",
        },
        True,
    ),
    (
        "recorded_return_contains_delivery",
        {
            "order_at": "/life/order",
            "shipment_at": "/life/shipment",
            "delivery_at": "/life/return/received",
            "return_at": "/life/return/details/0/at",
        },
        False,
    ),
])
def test_customer_no_return_sample_only_when_real_engine_accepts_it(name, fields, expected_absent):
    """Dashboard examples must not turn a malformed recorded return into a fake benign case.

    Check the authenticated tenant listing AND both actual Engine stages; an
    implementation that merely deletes the return_at leaf would fail for the
    shared-delivery layout. The customer example has synthetic data only.
    """
    rule = {
        **RULE,
        "rule_id": "sample_" + name,
        "fields": fields,
    }
    api, store, headers = _client()
    before = store.get(TENANT)
    saved = api.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": before.revision,
        "changes": {
            "advanced_conversion_time": [rule],
            "input_check_cross_field": "block",
            "output_check_cross_field": "flag",
        },
    })
    assert saved.status_code == 200, saved.text[:200]
    response = api.get("/v1/applications/current", headers=headers)
    assert response.status_code == 200
    metadata = next(
        item for item in response.json()["tenant"]["record_rules"]
        if item["rule_id"] == "business." + rule["rule_id"]
    )
    assert (metadata["no_return_example"] is not None) is expected_absent
    for stage in ("input", "output"):
        # The ordinary full-history look-alike always passes.
        passing = _check(api, headers, stage, json.loads(metadata["look_alike"]))
        assert passing["effective_action"] == "allow"
        assert not any(row["rule_id"] == metadata["rule_id"] for row in passing["findings"])
        if expected_absent:
            without_return = json.loads(metadata["no_return_example"])
            checked = _check(api, headers, stage, without_return)
            assert checked["evaluation_status"] == "complete"
            assert checked["effective_action"] == "allow"
            assert not any(row["rule_id"] == metadata["rule_id"] for row in checked["findings"])
            assert "return_at" not in json.dumps(without_return)
        else:
            assert metadata["no_return_example"] is None
    assert store.get(TENANT).advanced_conversion_time[0].rule_id == rule["rule_id"]


def test_no_return_customer_sample_respects_strict_json_depth_admission():
    """The per-rule chronology alone can pass JSON that the shared checker rejects.

    The dashboard must never advertise such a sample as a clean check; this
    regression preserves the shipped 32-container JSON limit rather than
    increasing it or bypassing the parser.
    """
    from types import SimpleNamespace
    from guardoath.record_rules import record_rules

    parent = "/" + "/".join(["n"] * 33)
    fields = {
        "order_at": parent + "/order_at",
        "shipment_at": parent + "/shipment_at",
        "delivery_at": parent + "/delivery_at",
        "return_at": parent + "/return_at",
    }
    rule = AdvancedConversionTimeRule.model_validate({
        **RULE, "rule_id": "strict_json_sample_depth", "fields": fields,
    })
    metadata = record_rules(SimpleNamespace(
        comparisons=(), required_sections=(), advanced_conversion_time=(rule,),
    ))["business.strict_json_sample_depth"]
    assert metadata["no_return_example"] is None
