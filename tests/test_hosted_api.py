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
    ({"GUARDOATH_TENANT_STORE": "postgres"}, "must be one of"),
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


# Profiles: the record's own checks ("tenant") plus the example profiles. A request that names no
# profile uses the record's default_profile.

from guardoath.config import load_policies
from guardoath.hosted import load_example_profiles

PROFILES = tuple(load_policies("policies/playground.json"))
OUTSIDE = {"content-safety-v1"}  # the only example profile that calls a paid outside provider


def profiled(records=None):
    return TenantService(MemoryTenantStore(demo_records(key_secret=SECRET) if records is None else records),
                         CHECKERS, SECRET, PROFILES)


@pytest.fixture
def pclient():
    tenants = profiled()
    return client_for(tenants), tenants


def unnamed(client, token, tenant_id, content, stage="input"):
    return client.post("/v1/checks/" + stage, headers=auth(token), json={"application_id": tenant_id, "content": content})


def test_the_example_profile_file_has_23_profiles_and_one_calls_an_outside_provider():
    from guardoath.hosted import calls_outside_provider
    assert len(PROFILES) == 23
    assert {p.version for p in PROFILES if calls_outside_provider(p)} == OUTSIDE


def test_a_demo_user_gets_its_record_and_every_local_example_profile(pclient):
    client, _ = pclient
    data = client.get("/v1/applications/current", headers=auth(demo_key("demo1", SECRET))).json()
    assert [p["version"] for p in data["policies"]] == ["tenant"] + [p.version for p in PROFILES if p.version not in OUTSIDE]
    assert data["tenant"]["default_profile"] == "tenant"
    assert [c["id"] for c in data["policies"][0]["checks"]] == ["pii", "encoded_pii", "secrets", "text_security"]


def test_customers_also_get_the_profile_that_calls_an_outside_provider():
    token, digest = new_key("acme")
    customer = TenantRecord(tenant_id="acme", display_name="Acme", key_hashes=(digest,), check_secrets="flag")
    client = client_for(profiled(records=(customer,)))
    versions = [p["version"] for p in client.get("/v1/applications/current", headers=auth(token)).json()["policies"]]
    assert versions == ["tenant"] + [p.version for p in PROFILES]


def test_profile_checks_are_described_with_the_action_on_each_stage(pclient):
    client, _ = pclient
    policies = {p["version"]: p for p in client.get("/v1/applications/current", headers=auth(demo_key("demo1", SECRET))).json()["policies"]}
    pii = next(c for c in policies["conversation-v1"]["checks"] if c["id"] == "pii")
    assert pii["actions"] == {"input": "flag", "output": "block"}
    assert next(c for c in policies["redaction-v1"]["checks"] if c["id"] == "pii")["actions"] == {"input": "redact", "output": "redact"}
    assert all(c["actions"] == {"input": "block", "output": "block"} for c in policies["tenant"]["checks"])


def test_a_request_without_a_profile_uses_the_default_profile(pclient):
    client, tenants = pclient
    token = demo_key("demo1", SECRET)
    result = unnamed(client, token, "demo1", EMAIL)
    assert result.status_code == 200
    assert result.json()["policy_version"] == "tenant" and result.json()["effective_action"] == "block"
    response = change(client, token, summary(client, token)["revision"], {"default_profile": "secrets-v1"})
    assert response.status_code == 200 and response.json()["default_profile"] == "secrets-v1"
    result = unnamed(client, token, "demo1", EMAIL).json()
    assert result["policy_version"] == "secrets-v1"
    assert {c["checker_id"] for c in result["checker_results"]} == {"secrets"} and result["effective_action"] == "allow"
    assert used(tenants, "demo1") == 2


def test_a_named_profile_applies_to_that_request_only(pclient):
    client, _ = pclient
    token = demo_key("demo1", SECRET)
    named = check(client, token, "demo1", EMAIL, version="pii-v1").json()
    assert named["policy_version"] == "pii-v1" and {c["checker_id"] for c in named["checker_results"]} == {"pii"}
    # pii-v1 runs in observe mode: the finding is recommended for flagging but the message is allowed.
    assert named["recommended_action"] == "flag" and named["effective_action"] == "allow"
    after = unnamed(client, token, "demo1", EMAIL).json()
    assert after["policy_version"] == "tenant"
    assert {c["checker_id"] for c in after["checker_results"]} == {"pii", "encoded_pii", "secrets", "text_security"}


@pytest.mark.parametrize("version", ["no-such-profile", "content-safety-v1"])
def test_a_profile_the_tenant_cannot_use_is_refused_and_not_counted(pclient, version):
    client, tenants = pclient
    response = check(client, demo_key("demo1", SECRET), "demo1", CLEAN, version=version)
    assert response.status_code == 404 and response.json() == {"detail": "Policy version not found"}
    assert used(tenants, "demo1") == 0


@pytest.mark.parametrize("value, message", [
    ("content-safety-v1", "default_profile: choose one of the profiles available to this tenant"),
    ("no-such-profile", "default_profile: choose one of the profiles available to this tenant"),
    ("Not A Profile", "default_profile: String should match pattern"),
])
def test_a_default_profile_must_be_one_the_tenant_can_use(pclient, value, message):
    client, tenants = pclient
    before = tenants.store.get("demo1")
    response = change(client, demo_key("demo1", SECRET), before.revision, {"default_profile": value})
    assert response.status_code == 422 and message in response.json()["detail"]
    assert tenants.store.get("demo1") == before


def test_reset_restores_the_default_profile(pclient):
    client, tenants = pclient
    token = demo_key("demo1", SECRET)
    change(client, token, summary(client, token)["revision"], {"default_profile": "pii-v1"})
    assert client.post("/v1/tenants/current/reset", headers=auth(token), json={}).json()["default_profile"] == "tenant"
    assert tenants.store.get("demo1").default_profile == "tenant"


def test_one_demo_users_default_profile_does_not_change_another(pclient):
    client, tenants = pclient
    token = demo_key("demo1", SECRET)
    change(client, token, summary(client, token)["revision"], {"default_profile": "pii-v1"})
    assert tenants.store.get("demo2").default_profile == "tenant"
    assert unnamed(client, demo_key("demo2", SECRET), "demo2", EMAIL).json()["policy_version"] == "tenant"


def test_local_keys_must_still_name_a_profile(pclient):
    _, tenants = pclient
    client = client_for(tenants, credentials={"local-key": "support"})
    response = client.post("/v1/checks/input", headers=auth("local-key"), json={"application_id": "support", "content": CLEAN})
    assert response.status_code == 422 and response.json() == {"detail": "Invalid check request"}


def test_a_profile_change_applies_to_the_next_check(pclient):
    client, _ = pclient
    token = demo_key("demo1", SECRET)
    first = unnamed(client, token, "demo1", EMAIL).json()["policy_version"]
    change(client, token, summary(client, token)["revision"], {"default_profile": "redaction-v1"})
    assert (first, unnamed(client, token, "demo1", EMAIL).json()["policy_version"]) == ("tenant", "redaction-v1")


def test_the_example_profiles_come_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("GUARDOATH_TENANT_STORE", "memory")
    monkeypatch.setenv("GUARDOATH_DEMO_KEY_SECRET", SECRET)
    monkeypatch.delenv("GUARDOATH_TENANT_PROFILE_FILE", raising=False)
    tenants = TenantService.from_environment(CHECKERS)
    assert len(tenants.profile_versions(tenants.store.get("demo1"))) == 1 + 22
    monkeypatch.setenv("GUARDOATH_TENANT_PROFILE_FILE", str(tmp_path / "missing.json"))
    with pytest.raises(ValueError, match="Example profile file not found"):
        TenantService.from_environment(CHECKERS)
    with pytest.raises(ValueError, match="Example profile file not found"):
        load_example_profiles(tmp_path / "missing.json")


@pytest.mark.parametrize("profiles, message", [
    (PROFILES[:1] * 2, "must be distinct"),
    ((PROFILES[0].model_copy(update={"version": "tenant"}),), "No example profile may be called tenant"),
    ((PROFILES[0].model_copy(update={"version": "Upper_Case"}),), "lower-case letters"),
])
def test_unusable_example_profiles_stop_startup(profiles, message):
    with pytest.raises(ValueError, match=message):
        TenantService(MemoryTenantStore(), CHECKERS, SECRET, profiles)


# The X-GuardOath-Key header (CloudFront with origin access control replaces Authorization)

SIGNED = "AWS4-HMAC-SHA256 Credential=EXAMPLE/20261030/us-east-1/lambda/aws4_request, SignedHeaders=host, Signature=" + "0" * 64


def keyed(token, signed=True):
    return {"X-GuardOath-Key": token, **({"Authorization": SIGNED} if signed else {})}


def test_a_demo_key_in_the_key_header_works_next_to_a_cloudfront_signature(client, tenants):
    token = demo_key("demo1", SECRET)
    assert client.get("/v1/applications/current", headers=keyed(token)).json()["application_id"] == "demo1"
    result = client.post("/v1/checks/input", headers=keyed(token), json={"application_id": "demo1", "policy_version": "tenant", "content": EMAIL})
    assert result.status_code == 200 and result.json()["effective_action"] == "block"
    assert used(tenants, "demo1") == 1


def test_a_local_key_in_the_key_header_works(tenants):
    client = client_for(tenants, credentials={"local-key": "demo"})
    assert client.get("/v1/applications/current", headers=keyed("local-key")).json()["application_id"] == "demo"


def test_the_key_header_wins_over_a_bearer_token(client):
    headers = {"X-GuardOath-Key": "gok_demo1_" + "x" * 43, "Authorization": "Bearer " + demo_key("demo1", SECRET)}
    assert client.get("/v1/applications/current", headers=headers).status_code == 401


@pytest.mark.parametrize("headers", [{}, {"Authorization": SIGNED}, {"X-GuardOath-Key": ""}, {"X-GuardOath-Key": "not-a-key"}])
def test_no_usable_key_is_refused(client, headers):
    response = client.get("/v1/applications/current", headers=headers)
    assert response.status_code == 401 and response.headers["www-authenticate"] == "Bearer"


def test_check_changes_accept_the_key_header(client):
    token = demo_key("demo1", SECRET)
    revision = summary(client, token)["revision"]
    response = client.post("/v1/tenants/current/checks", headers=keyed(token), json={"revision": revision, "changes": {"check_pii": "flag"}})
    assert response.status_code == 200 and response.json()["checks"]["pii"] == "flag"


def test_validation_packs_on_a_tenant_record_are_refused_not_a_server_error(client, tenants):
    response = client.post("/v1/checks/input", headers=auth(demo_key("demo1", SECRET)),
                           json={"application_id": "demo1", "content": CLEAN, "validation_codes": ["PII"]})
    assert response.status_code == 422 and response.json() == {"detail": "Invalid or unavailable validation pack selection"}
    assert used(tenants, "demo1") == 0
