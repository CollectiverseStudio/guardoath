"""FC conversion/time dashboard: tenant save/reload/Engine and hidden FX references."""
import json
from fastapi.testclient import TestClient

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.record_rules import CONVERSION_TIME_EXAMPLES
from guardoath.tenant_record import TenantRecord, demo_key, key_hash
from guardoath.tenant_store import MemoryTenantStore

SECRET = "conversion-dashboard-synthetic-secret-2026-123456"


DEFINITIONS = [
    {"rule_id": "mass_limit", "kind": "unit_load_kg_cap",
     "fields": {"load": "/mass/load", "load_unit": "/mass/load_unit",
                "max_kg": "/mass/max_kg"}, "stages": ["input", "output"]},
    {"rule_id": "cold_chain", "kind": "cold_chain_excursion",
     "fields": {"excursion_start": "/cold/excursion_start",
                "excursion_end": "/cold/excursion_end",
                "allowed_minutes": "/cold/allowed_minutes"}, "stages": ["input", "output"]},
    {"rule_id": "lifecycle", "kind": "lifecycle_chronology",
     "fields": {"order_at": "/life/order_at", "shipment_at": "/life/shipment_at",
                "delivery_at": "/life/delivery_at", "return_at": "/life/return_at"},
     "stages": ["input", "output"]},
]


def setup(protected=False):
    headers, records = {}, []
    for name in ("conversion-alpha", "conversion-beta"):
        token = demo_key(name, SECRET)
        headers[name] = {"Authorization": "Bearer " + token}
        fx = ()
        if protected and name == "conversion-alpha":
            fx = ({
                "rule_id": "private_fx", "kind": "currency_conversion_cap",
                "fields": {"payment_amount": "/payment", "payment_currency": "/payment_ccy",
                           "invoice_amount": "/invoice", "invoice_currency": "/invoice_ccy"},
                "source_currency": "EUR", "destination_currency": "USD",
                "trusted_rate_numerator": 6, "trusted_rate_denominator": 5,
                "rate_reference": "SENSITIVE-FX-REFERENCE",
                "rate_asof": "2026-11-01T09:00:00Z",
                "stages": ["input", "output"],
            },)
        records.append(TenantRecord(
            tenant_id=name, display_name=name, demo=True, overage="block",
            monthly_allowance=100, key_hashes=(key_hash(token),),
            input_check_text_security="flag", output_check_text_security="flag",
            advanced_conversion_time=fx))
    store = MemoryTenantStore(tuple(records))
    client = TestClient(create_app(engine=demo_engine(), credentials={},
        tenants=TenantService(store, default_checkers(), SECRET)))
    return client, store, headers


def summary(client, headers):
    response = client.get("/v1/applications/current", headers=headers)
    assert response.status_code == 200
    return response.json()["tenant"]


def test_three_non_fx_types_save_reload_run_both_stages_and_remove():
    api, store, headers = setup()
    a, b = headers["conversion-alpha"], headers["conversion-beta"]
    assert summary(api, a)["advanced_conversion_time"] == []
    assert summary(api, b)["advanced_conversion_time"] == []
    assert api.get("/v1/applications/current").status_code in (401, 403)
    start = store.get("conversion-alpha")
    response = api.post("/v1/tenants/current/checks", headers=a, json={
        "revision": start.revision,
        "changes": {"advanced_conversion_time": DEFINITIONS,
                    "input_check_cross_field": "block",
                    "output_check_cross_field": "flag"},
    })
    assert response.status_code == 200, response.text[:300]
    expected = [r.model_dump(mode="json") for r in store.get("conversion-alpha").advanced_conversion_time]
    assert summary(api, a)["advanced_conversion_time"] == expected
    assert summary(api, b)["advanced_conversion_time"] == []
    for stage, action in (("input", "block"), ("output", "flag")):
        for rule in DEFINITIONS:
            good, bad = CONVERSION_TIME_EXAMPLES[rule["kind"]]
            role = rule["kind"] == "unit_load_kg_cap" and "mass" or (
                rule["kind"] == "cold_chain_excursion" and "cold" or "life")
            for item, should_find in ((bad, True), (good, False)):
                result = api.post("/v1/checks/" + stage, headers=a, json={
                    "application_id": "conversion-alpha", "content_format": "json",
                    "content": json.dumps({role: item, **{
                        ("mass" if other["kind"] == "unit_load_kg_cap" else
                         "cold" if other["kind"] == "cold_chain_excursion" else "life"):
                        CONVERSION_TIME_EXAMPLES[other["kind"]][0]
                        for other in DEFINITIONS if other["kind"] != rule["kind"]
                    }}),
                })
                assert result.status_code == 200, result.text[:300]
                matches = {f["rule_id"] for f in result.json()["findings"]}
                assert ("business." + rule["rule_id"] in matches) is should_find
                if should_find:
                    assert result.json()["effective_action"] == action
    old = store.get("conversion-alpha")
    updated = api.post("/v1/tenants/current/checks", headers=a, json={
        "revision": old.revision, "changes": {
            "advanced_conversion_time": [dict(DEFINITIONS[0], stages=["input"]), *DEFINITIONS[1:]],
        },
    })
    assert updated.status_code == 200, updated.text[:300]
    stale = api.post("/v1/tenants/current/checks", headers=a, json={
        "revision": old.revision, "changes": {
            "advanced_conversion_time": [], "input_check_cross_field": None,
            "output_check_cross_field": None},
    })
    assert stale.status_code == 409
    assert len(store.get("conversion-alpha").advanced_conversion_time) == 3
    removed = api.post("/v1/tenants/current/checks", headers=a, json={
        "revision": store.get("conversion-alpha").revision,
        "changes": {"advanced_conversion_time": [],
                    "input_check_cross_field": None, "output_check_cross_field": None},
    })
    assert removed.status_code == 200, removed.text[:300]
    assert summary(api, a)["advanced_conversion_time"] == []
    assert summary(api, b)["advanced_conversion_time"] == []


def test_fx_reference_is_opaque_and_preserved_across_unrelated_tenant_save():
    api, store, headers = setup(protected=True)
    a, b = headers["conversion-alpha"], headers["conversion-beta"]
    data = summary(api, a)
    assert data["advanced_conversion_time"] == []
    assert data["advanced_conversion_reference_status"] == [
        {"rule_id": "private_fx", "kind": "currency_conversion_cap",
         "stages": ["input", "output"], "reference_configured": True},
    ]
    assert "SENSITIVE-FX-REFERENCE" not in json.dumps(data)
    assert "SENSITIVE-FX-REFERENCE" not in api.get("/v1/applications/current", headers=a).text
    assert summary(api, b)["advanced_conversion_reference_status"] == []
    original = store.get("conversion-alpha")
    saved = api.post("/v1/tenants/current/checks", headers=a, json={
        "revision": original.revision, "changes": {"input_check_text_security": "block"},
    })
    assert saved.status_code == 200
    assert store.get("conversion-alpha").advanced_conversion_time == original.advanced_conversion_time
    assert "SENSITIVE-FX-REFERENCE" not in saved.text


def test_non_fx_rules_reject_untrusted_reference_values_and_bad_pointers():
    api, store, headers = setup()
    a = headers["conversion-alpha"]
    original = store.get("conversion-alpha")
    for invalid in (
        dict(DEFINITIONS[0], fields={"load": "/load/0", "load_unit": "/unit", "max_kg": "/max"}),
        dict(DEFINITIONS[0], source_currency="EUR"),
        dict(DEFINITIONS[0], fields={"load": "/same", "load_unit": "/same/x", "max_kg": "/max"}),
        dict(DEFINITIONS[0], kind="unknown_time_kind"),
    ):
        result = api.post("/v1/tenants/current/checks", headers=a, json={
            "revision": original.revision,
            "changes": {"advanced_conversion_time": [invalid], "input_check_cross_field": "block"},
        })
        assert result.status_code == 422
        assert store.get("conversion-alpha") == original


def test_generic_checks_api_cannot_drop_or_replace_existing_protected_fx_reference():
    api, store, headers = setup(protected=True)
    a = headers["conversion-alpha"]
    before = store.get("conversion-alpha")
    for submitted in (
        [],
        DEFINITIONS,
        [{
            "rule_id": "private_fx", "kind": "currency_conversion_cap",
            "fields": {"payment_amount": "/payment", "payment_currency": "/payment_ccy",
                       "invoice_amount": "/invoice", "invoice_currency": "/invoice_ccy"},
            "source_currency": "EUR", "destination_currency": "USD",
            "trusted_rate_numerator": 2, "trusted_rate_denominator": 1,
            "rate_reference": "UNVERIFIED_REPLACEMENT",
            "rate_asof": "2026-11-02T09:00:00Z",
            "stages": ["input", "output"],
        }],
    ):
        result = api.post("/v1/tenants/current/checks", headers=a, json={
            "revision": before.revision, "changes": {"advanced_conversion_time": submitted},
        })
        assert result.status_code == 422, result.text[:250]
        assert "SENSITIVE-FX-REFERENCE" not in result.text
        assert store.get("conversion-alpha") == before


def test_generic_checks_api_denies_unverified_fx_provisioning_and_malformed_kind():
    api, store, headers = setup()
    a = headers["conversion-alpha"]
    before = store.get("conversion-alpha")
    for entry in (
        {
            "rule_id": "new_fx", "kind": "currency_conversion_cap",
            "fields": {"payment_amount": "/payment", "payment_currency": "/payment_ccy",
                       "invoice_amount": "/invoice", "invoice_currency": "/invoice_ccy"},
            "source_currency": "EUR", "destination_currency": "USD",
            "trusted_rate_numerator": 6, "trusted_rate_denominator": 5,
            "rate_reference": "UNVERIFIED_RATE_FROM_REQUEST",
            "rate_asof": "2026-11-01T09:00:00Z",
            "stages": ["input", "output"],
        },
        dict(DEFINITIONS[0], kind=["unit_load_kg_cap"]),
        dict(DEFINITIONS[0], kind={"bad": "kind"}),
    ):
        result = api.post("/v1/tenants/current/checks", headers=a, json={
            "revision": before.revision,
            "changes": {"advanced_conversion_time": [entry]},
        })
        assert result.status_code == 422, result.text[:250]
        assert "UNVERIFIED_RATE_FROM_REQUEST" not in result.text
        assert store.get("conversion-alpha") == before


def test_reference_management_route_is_owner_scoped_and_cannot_bypass_tenant_authentication():
    secret = "owner-only-synthetic-reference-key-0123456789"
    token = demo_key("reference-owner", secret)
    record = TenantRecord(
        tenant_id="reference-owner", display_name="Reference owner", demo=False,
        key_hashes=(key_hash(token),), input_check_text_security="flag")
    store = MemoryTenantStore((record,))
    api = TestClient(create_app(engine=demo_engine(), credentials={},
                     tenants=TenantService(store, default_checkers(), secret)))
    headers = {"Authorization": "Bearer " + token}
    # Use a structurally valid reference-management request so this test
    # exercises the owner authorization gate rather than payload validation.
    body = {"revision": record.revision, "changes": {
        "advanced_conversion_time": [],
    }}
    assert api.post("/v1/tenants/current/references", json=body).status_code in (401, 403)
    # Production evaluation-only tenant keys are not reference administrators.
    response = api.post("/v1/tenants/current/references", headers=headers, json=body)
    assert response.status_code == 403
    assert store.get("reference-owner") == record


def test_reference_route_rejects_unrelated_settings_and_keeps_generic_api_protected():
    api, store, headers = setup()
    a = headers["conversion-alpha"]
    original = store.get("conversion-alpha")
    rejected = api.post("/v1/tenants/current/references", headers=a, json={
        "revision": original.revision, "changes": {
            "input_check_text_security": "block",
            "advanced_conversion_time": [DEFINITIONS[0]],
        },
    })
    assert rejected.status_code == 422
    assert store.get("conversion-alpha") == original
    # Generic settings route still cannot create an FX reference.
    unverified_fx = {
        "rule_id": "unverified_fx", "kind": "currency_conversion_cap",
        "fields": {"payment_amount": "/payment", "payment_currency": "/payment_ccy",
                   "invoice_amount": "/invoice", "invoice_currency": "/invoice_ccy"},
        "source_currency": "EUR", "destination_currency": "USD",
        "trusted_rate_numerator": 6, "trusted_rate_denominator": 5,
        "rate_reference": "UNVERIFIED_FROM_GENERIC_API",
        "rate_asof": "2026-11-01T09:00:00Z",
        "stages": ["input"],
    }
    denied = api.post("/v1/tenants/current/checks", headers=a, json={
        "revision": original.revision,
        "changes": {"advanced_conversion_time": [unverified_fx]},
    })
    assert denied.status_code == 422
    assert "UNVERIFIED_FROM_GENERIC_API" not in denied.text
    assert store.get("conversion-alpha") == original
