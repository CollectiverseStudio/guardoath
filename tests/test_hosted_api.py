"""Hosted mode: tenant keys, one record per tenant, demo users demo1-demo10 and their keys."""
import pytest
from fastapi.testclient import TestClient

pytest.importorskip("tldextract")
pytest.importorskip("phonenumbers")

from guardoath.app import create_app
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.tenant_record import TenantRecord, demo_key, demo_records, new_key, usage_month
from guardoath.tenant_store import MemoryTenantStore

SECRET = "hosted-test-demo-secret-0123456789abcdef"
CHECKERS = default_checkers(enable_pii=True)
CLEAN = "Can you explain your return policy?"
EMAIL = "Please email jane@example.com about my order."


def service(records=None, secret=SECRET):
    return TenantService(MemoryTenantStore(demo_records(key_secret=secret) if records is None else records), CHECKERS, secret)


def client_for(tenants, **kwargs):
    return TestClient(create_app(engine=_engine(), credentials=kwargs.pop("credentials", {}), tenants=tenants), **kwargs)


def _engine():
    from guardoath.app import demo_engine
    return demo_engine()


def auth(token):
    return {"Authorization": "Bearer " + token}


def check(client, token, tenant_id, content, stage="input", version="tenant"):
    return client.post("/v1/checks/" + stage, headers=auth(token), json={
        "application_id": tenant_id, "policy_version": version, "content": content})


@pytest.fixture
def tenants():
    return service()


@pytest.fixture
def client(tenants):
    return client_for(tenants)


def used(tenants, tenant_id):
    return tenants.store.get(tenant_id).used(usage_month())


def test_demo_users_are_listed_without_a_key(client):
    response = client.get("/v1/demo-users")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    users = response.json()["demo_users"]
    assert [u["tenant_id"] for u in users] == [f"demo{n}" for n in range(1, 11)]
    assert users[0] == {"tenant_id": "demo1", "display_name": "Demo user 1"}


def test_choosing_a_demo_user_gives_its_api_key(client, tenants):
    response = client.post("/v1/demo-users/demo3/key", json={})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body == {"tenant_id": "demo3", "api_key": demo_key("demo3", SECRET)}
    assert body["api_key"].startswith("gok_demo3_")
    # Every visitor who picks demo3 gets the same key, and the record holds only its hash.
    assert client.post("/v1/demo-users/demo3/key", json={}).json() == body
    assert body["api_key"] not in tenants.store.get("demo3").model_dump_json()
    assert client.post("/v1/demo-users/demo4/key", json={}).json()["api_key"] != body["api_key"]


@pytest.mark.parametrize("tenant_id", ["demo11", "acme", "Demo1", "demo_1", "x"])
def test_unknown_or_invalid_demo_users_get_no_key(client, tenant_id):
    assert client.post(f"/v1/demo-users/{tenant_id}/key", json={}).status_code in (404, 422)


def test_customer_tenants_are_not_demo_users():
    _, digest = new_key("acme")
    customer = TenantRecord(tenant_id="acme", display_name="Acme", key_hashes=(digest,), check_secrets="flag")
    tenants = service(records=demo_records(key_secret=SECRET) + (customer,))
    client = client_for(tenants)
    assert "acme" not in [u["tenant_id"] for u in client.get("/v1/demo-users").json()["demo_users"]]
    assert client.post("/v1/demo-users/acme/key", json={}).status_code == 404


def test_demo_users_are_hidden_without_a_demo_secret():
    client = client_for(TenantService(MemoryTenantStore(demo_records()), CHECKERS, None))
    assert client.get("/v1/demo-users").status_code == 404
    assert client.post("/v1/demo-users/demo1/key", json={}).status_code == 404


def test_demo_key_request_needs_a_json_body(client):
    assert client.post("/v1/demo-users/demo1/key").status_code == 415


def test_a_demo_key_connects_to_its_own_record(client):
    token = client.post("/v1/demo-users/demo2/key", json={}).json()["api_key"]
    response = client.get("/v1/applications/current", headers=auth(token))
    assert response.status_code == 200
    data = response.json()
    assert data["application_id"] == "demo2"
    assert [p["version"] for p in data["policies"]] == ["tenant"]
    assert [c["id"] for c in data["policies"][0]["checks"]] == ["pii", "encoded_pii", "secrets", "text_security"]
    tenant = data["tenant"]
    assert tenant["tenant_id"] == "demo2" and tenant["demo"] is True and tenant["used"] == 0
    assert tenant["monthly_allowance"] == 2000 and tenant["overage"] == "block" and tenant["month"] == usage_month()
    assert tenant["checks"]["pii"] == "block" and tenant["checks"]["ledger"] is None and len(tenant["checks"]) == 16
    assert token not in response.text


def test_demo_checks_block_an_email_and_pass_a_clean_question(client, tenants):
    token = demo_key("demo1", SECRET)
    for stage in ("input", "output"):
        clean = check(client, token, "demo1", CLEAN, stage)
        assert clean.status_code == 200 and clean.json()["effective_action"] == "allow"
        held = check(client, token, "demo1", EMAIL, stage)
        assert held.status_code == 200 and held.json()["effective_action"] == "block"
        assert "pii.email_address" in {f["rule_id"] for f in held.json()["findings"]}
    assert used(tenants, "demo1") == 4


def test_only_checks_with_a_result_are_counted(client, tenants):
    token = demo_key("demo1", SECRET)
    assert check(client, token, "demo2", CLEAN).status_code == 403
    assert check(client, token, "demo1", CLEAN, version="conversation-v1").status_code == 404
    assert client.post("/v1/checks/input", headers=auth(token), json={"application_id": "demo1"}).status_code == 422
    assert used(tenants, "demo1") == 0


@pytest.mark.parametrize("token", ["gok_demo1_" + "x" * 43, "gok_demo1_short", "gok_nobody_" + "a" * 43,
                                   "gok_", "not-a-key"])
def test_wrong_tenant_keys_are_refused(client, token):
    assert client.get("/v1/applications/current", headers=auth(token)).status_code == 401
    assert check(client, token, "demo1", CLEAN).status_code == 401


def test_a_key_made_with_another_secret_is_refused(client):
    assert check(client, demo_key("demo1", "another-secret-" + "z" * 32), "demo1", CLEAN).status_code == 401


def test_disabled_tenants_are_refused():
    records = list(demo_records(key_secret=SECRET))
    records[0] = TenantRecord.model_validate({**records[0].model_dump(), "status": "disabled"})
    client = client_for(service(records=tuple(records)))
    assert check(client, demo_key("demo1", SECRET), "demo1", CLEAN).status_code == 401
    assert "demo1" not in [u["tenant_id"] for u in client.get("/v1/demo-users").json()["demo_users"]]


def test_demo_users_stop_at_their_monthly_limit():
    records = list(demo_records(key_secret=SECRET))
    records[0] = TenantRecord.model_validate({**records[0].model_dump(), "monthly_allowance": 2})
    tenants = service(records=tuple(records))
    client = client_for(tenants)
    token = demo_key("demo1", SECRET)
    assert [check(client, token, "demo1", CLEAN).status_code for _ in range(3)] == [200, 200, 429]
    refused = check(client, token, "demo1", CLEAN)
    assert refused.json() == {"detail": "Monthly check limit reached"}
    assert used(tenants, "demo1") == 2
    assert check(client, demo_key("demo2", SECRET), "demo2", CLEAN).status_code == 200


def test_customers_are_counted_past_their_allowance():
    token, digest = new_key("acme")
    customer = TenantRecord(tenant_id="acme", display_name="Acme", key_hashes=(digest,), monthly_allowance=1,
                            check_secrets="flag")
    tenants = service(records=(customer,))
    client = client_for(tenants)
    assert [check(client, token, "acme", CLEAN).status_code for _ in range(3)] == [200, 200, 200]
    assert used(tenants, "acme") == 3


def test_check_changes_apply_to_the_next_request(client, tenants):
    token = demo_key("demo1", SECRET)
    assert check(client, token, "demo1", EMAIL).json()["effective_action"] == "block"
    record = tenants.store.get("demo1")
    tenants.store.update_checks("demo1", {"check_pii": "flag"}, record.revision)
    assert check(client, token, "demo1", EMAIL).json()["effective_action"] == "flag"
    record = tenants.store.get("demo1")
    tenants.store.update_checks("demo1", {"check_pii": None, "check_encoded_pii": None}, record.revision)
    result = check(client, token, "demo1", EMAIL).json()
    assert result["effective_action"] == "allow" and {c["checker_id"] for c in result["checker_results"]} == {"secrets", "text_security"}


def test_a_failed_check_is_not_counted(tenants):
    class Broken:
        async def check(self, request, stage):
            raise RuntimeError("synthetic failure")

        def describe_policies(self, application_id):
            return []

    tenants.engine_for = lambda record: Broken()
    client = client_for(tenants, raise_server_exceptions=False)
    assert check(client, demo_key("demo1", SECRET), "demo1", CLEAN).status_code == 500
    assert used(tenants, "demo1") == 0


def test_local_keys_keep_working_next_to_tenant_keys(tenants):
    client = client_for(tenants, credentials={"local-key": "demo"})
    assert client.get("/v1/applications/current", headers=auth("local-key")).json()["application_id"] == "demo"
    assert client.get("/v1/applications/current", headers=auth(demo_key("demo5", SECRET))).json()["application_id"] == "demo5"


def test_tenant_records_and_a_tenant_registry_cannot_be_combined(tenants):
    from guardoath.tenants import TenantApplication, TenantRegistry
    registry = TenantRegistry((TenantApplication("t", "app", "k"),))
    with pytest.raises(ValueError, match="not both"):
        create_app(engine=_engine(), tenants=tenants, tenant_registry=registry)


def test_environment_turns_on_demo_users(monkeypatch):
    monkeypatch.setenv("GUARDOATH_TENANT_STORE", "memory")
    monkeypatch.setenv("GUARDOATH_DEMO_KEY_SECRET", SECRET)
    monkeypatch.setenv("GUARDOATH_ENABLE_PII", "1")
    monkeypatch.delenv("GUARDOATH_POLICY_FILE", raising=False)
    monkeypatch.delenv("GUARDOATH_PACK_FILE", raising=False)
    monkeypatch.delenv("GUARDOATH_API_KEY", raising=False)
    client = TestClient(create_app())
    assert len(client.get("/v1/demo-users").json()["demo_users"]) == 10
    token = client.post("/v1/demo-users/demo1/key", json={}).json()["api_key"]
    assert check(client, token, "demo1", EMAIL).json()["effective_action"] == "block"


@pytest.mark.parametrize("settings, message", [
    ({"GUARDOATH_TENANT_STORE": "dynamodb"}, "must be one of"),
    ({"GUARDOATH_TENANT_STORE": "memory", "GUARDOATH_DEMO_KEY_SECRET": "short"}, "at least 32"),
    ({"GUARDOATH_DEMO_KEY_SECRET": SECRET}, "requires GUARDOATH_TENANT_STORE"),
])
def test_invalid_tenant_settings_fail_startup(monkeypatch, settings, message):
    for name in ("GUARDOATH_TENANT_STORE", "GUARDOATH_DEMO_KEY_SECRET"):
        monkeypatch.delenv(name, raising=False)
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=message):
        TenantService.from_environment(CHECKERS)


def test_tenant_store_is_off_by_default(monkeypatch):
    for name in ("GUARDOATH_TENANT_STORE", "GUARDOATH_DEMO_KEY_SECRET"):
        monkeypatch.delenv(name, raising=False)
    assert TenantService.from_environment(CHECKERS) is None


# Changing a tenant's own check fields (dashboard toggles)


def change(client, token, revision, changes):
    return client.post("/v1/tenants/current/checks", headers=auth(token), json={"revision": revision, "changes": changes})


def summary(client, token):
    return client.get("/v1/applications/current", headers=auth(token)).json()["tenant"]


def test_a_saved_change_applies_to_the_next_check(client, tenants):
    token = demo_key("demo1", SECRET)
    before = summary(client, token)
    response = change(client, token, before["revision"], {"check_pii": "flag"})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    after = response.json()
    assert after["revision"] == before["revision"] + 1 and after["checks"]["pii"] == "flag"
    assert check(client, token, "demo1", EMAIL).json()["effective_action"] == "flag"
    # A check that was off, with its settings already on the record, can be turned on.
    assert change(client, token, after["revision"], {"check_ledger": "block"}).json()["checks"]["ledger"] == "block"
    assert "ledger" in [c["id"] for c in client.get("/v1/applications/current", headers=auth(token)).json()["policies"][0]["checks"]]


def test_turning_checks_off_stops_them_running(client):
    token = demo_key("demo1", SECRET)
    revision = summary(client, token)["revision"]
    assert change(client, token, revision, {"check_pii": None, "check_encoded_pii": None}).status_code == 200
    result = check(client, token, "demo1", EMAIL).json()
    assert result["effective_action"] == "allow"
    assert {c["checker_id"] for c in result["checker_results"]} == {"secrets", "text_security"}


def test_a_stale_revision_is_refused_and_nothing_changes(client, tenants):
    token = demo_key("demo1", SECRET)
    revision = summary(client, token)["revision"]
    assert change(client, token, revision, {"check_pii": "flag"}).status_code == 200
    stale = change(client, token, revision, {"check_pii": None})
    assert stale.status_code == 409 and stale.json() == {"detail": "The tenant record changed; reload it and try again"}
    assert tenants.store.get("demo1").check_pii == "flag"


@pytest.mark.parametrize("changes, message", [
    ({"check_secrets": "redact"}, "Only the pii check can redact"),
    ({"check_secrets": "deny"}, "check_secrets: Input should be"),
    ({"check_content_safety": "flag"}, "Demo users cannot run checks that call a paid outside provider"),
    ({"check_pii": None, "check_encoded_pii": None, "check_secrets": None, "check_text_security": None},
     "Turn on at least one check"),
    ({"monthly_allowance": 10_000_000}, "Only check fields and check settings can be changed"),
    ({"usage": {}}, "Only check fields and check settings can be changed"),
    ({"key_hashes": []}, "Only check fields and check settings can be changed"),
    ({"ledger": None, "check_ledger": "flag"}, "The ledger check needs its settings before it can run"),
])
def test_rejected_changes_say_why_and_change_nothing(client, tenants, changes, message):
    token = demo_key("demo1", SECRET)
    before = tenants.store.get("demo1")
    response = change(client, token, before.revision, changes)
    assert response.status_code == 422 and message in response.json()["detail"]
    assert tenants.store.get("demo1") == before


def test_a_change_the_engine_rejects_is_not_stored(client, tenants):
    token = demo_key("demo1", SECRET)
    before = tenants.store.get("demo1")
    response = change(client, token, before.revision, {"check_schema": "block", "input_schema": {"$ref": "https://example.com/s.json"}})
    assert response.status_code == 422 and "Only local schema references" in response.json()["detail"]
    assert tenants.store.get("demo1") == before


def test_rejection_messages_do_not_echo_submitted_values(client):
    token = demo_key("demo1", SECRET)
    revision = summary(client, token)["revision"]
    response = change(client, token, revision, {"check_secrets": "private-submitted-value"})
    assert response.status_code == 422 and "private-submitted-value" not in response.text


def test_changes_are_not_counted_as_checks(client, tenants):
    token = demo_key("demo1", SECRET)
    change(client, token, summary(client, token)["revision"], {"check_pii": "flag"})
    assert used(tenants, "demo1") == 0


def test_reset_restores_the_demo_defaults(client, tenants):
    token = demo_key("demo1", SECRET)
    change(client, token, summary(client, token)["revision"], {"check_pii": None, "check_ledger": "flag", "mode": "observe"})
    response = client.post("/v1/tenants/current/reset", headers=auth(token), json={})
    assert response.status_code == 200
    checks = response.json()["checks"]
    assert {k for k, v in checks.items() if v} == {"pii", "encoded_pii", "secrets", "text_security"}
    assert all(v == "block" for v in checks.values() if v)
    assert tenants.store.get("demo1").mode == "enforce"
    assert check(client, token, "demo1", EMAIL).json()["effective_action"] == "block"


def test_each_demo_user_changes_only_its_own_record(client, tenants):
    token = demo_key("demo1", SECRET)
    change(client, token, summary(client, token)["revision"], {"check_pii": None})
    assert tenants.store.get("demo1").check_pii is None and tenants.store.get("demo2").check_pii == "block"


def test_customers_can_change_their_checks_but_not_reset():
    token, digest = new_key("acme")
    customer = TenantRecord(tenant_id="acme", display_name="Acme", key_hashes=(digest,), check_secrets="flag")
    client = client_for(service(records=(customer,)))
    assert change(client, token, 1, {"check_text_security": "block"}).json()["checks"]["text_security"] == "block"
    response = client.post("/v1/tenants/current/reset", headers=auth(token), json={})
    assert response.status_code == 403 and response.json() == {"detail": "Only demo users can be reset to the demo defaults"}


def test_check_changes_need_a_tenant_key(tenants):
    client = client_for(tenants, credentials={"local-key": "demo"})
    assert change(client, "local-key", 1, {"check_pii": "flag"}).status_code == 403
    assert client.post("/v1/tenants/current/reset", headers=auth("local-key"), json={}).status_code == 403
    assert change(client, "gok_demo1_" + "x" * 43, 1, {"check_pii": "flag"}).status_code == 401
    assert client.post("/v1/tenants/current/checks", json={"revision": 1, "changes": {"check_pii": "flag"}}).status_code == 401


@pytest.mark.parametrize("body", [{"changes": {"check_pii": "flag"}}, {"revision": 0, "changes": {"check_pii": "flag"}},
                                  {"revision": "1", "changes": {"check_pii": "flag"}}, {"revision": 1, "changes": {}},
                                  {"revision": 1, "changes": {"check_pii": "flag"}, "extra": True}])
def test_malformed_change_requests_are_refused(client, body):
    response = client.post("/v1/tenants/current/checks", headers=auth(demo_key("demo1", SECRET)), json=body)
    assert response.status_code == 422 and response.json() == {"detail": "Invalid check request"}
