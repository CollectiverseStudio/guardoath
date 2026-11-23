from dataclasses import dataclass
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError
from guardoath.admin_console import (
    VerifiedAdmin, create_admin_router, MAX_MUTABLE_REVISION,
    TenantRuleChange, AllowanceCap, RevokeTenantKeys,
    ReconcileUnpaidSubscription, StatusChange,
)


@dataclass
class Record:
    tenant_id: str = "alpha"
    display_name: str = "Example Tenant"
    status: str = "active"
    demo: bool = False
    subscription_status: str = "inactive"
    monthly_allowance: int = 10000
    overage: str = "block"
    revision: int = 2
    updated_at: str = "2026-11-16T00:00:00Z"
    key_hashes: tuple = ("SUPER_SECRET_HASH_SHOULD_NOT_LEAK",)
    payer_id: str = "cus_PRIVATE"


class Store:
    def tenant_ids(self): return ("alpha",)
    def get(self, name): return Record() if name == "alpha" else None


def client(verifier=None, store=None):
    app = FastAPI()
    app.include_router(create_admin_router(verify_cognito_admin=verifier, tenant_store=store))
    return TestClient(app)


def auth(token="owner"):
    return {"Authorization": "Bearer " + token}


def verified(token):
    return VerifiedAdmin("admin-1", frozenset({"owner"}), True) if token == "owner" else None


def test_no_verifier_denies_admin_even_with_bearer_token():
    c = client()
    assert c.get("/admin").status_code == 401
    assert c.get("/admin", headers=auth()).status_code == 503
    assert c.get("/v1/admin/tenants", headers=auth()).status_code == 503


def test_customer_key_and_unverified_token_cannot_access():
    c = client(verified, Store())
    for url in ("/admin", "/v1/admin/tenants", "/v1/admin/tenants/alpha", "/v1/admin/overview"):
        assert c.get(url).status_code == 401
        assert c.get(url, headers=auth("app-key")).status_code == 401
        assert c.get(url, headers={"X-GuardOath-Key": "owner"}).status_code == 401


def test_admin_role_and_mfa_required_even_for_verified_principal():
    for principal in [VerifiedAdmin("someone", frozenset({"customer"}), True),
                      VerifiedAdmin("someone", frozenset({"owner"}), False)]:
        c = client(lambda _: principal, Store())
        assert c.get("/admin", headers=auth()).status_code == 403
        assert c.get("/v1/admin/tenants", headers=auth()).status_code == 403


def test_verifier_failure_fails_closed():
    def unavailable(token): raise RuntimeError("provider failure")
    c = client(unavailable, Store())
    r = c.get("/v1/admin/tenants", headers=auth())
    assert r.status_code == 503
    assert "provider failure" not in r.text


def test_owner_read_only_tenant_directory_masks_keys_and_billing_bindings():
    c = client(verified, Store())
    page = c.get("/admin", headers=auth())
    assert page.status_code == 200
    assert "no-store" in page.headers["cache-control"]
    assert "GuardOath Administration" in page.text
    r = c.get("/v1/admin/tenants", headers=auth())
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["tenants"][0]["tenant_id"] == "alpha"
    assert r.json()["tenants"][0]["api_key_count"] == 1
    assert r.json()["tenants"][0]["overage_policy"] == "block"
    assert c.get("/v1/admin/tenants/alpha", headers=auth()).json()["tenant"]["api_key_count"] == 1
    for sensitive in ("SUPER_SECRET", "cus_PRIVATE", "key_hashes", "payer_id"):
        assert sensitive not in r.text
    assert c.get("/v1/admin/tenants/alpha", headers=auth()).status_code == 200
    assert c.get("/v1/admin/tenants/beta", headers=auth()).status_code == 404


def test_pagination_bounds_and_unknown_costs():
    c = client(verified, Store())
    assert c.get("/v1/admin/tenants?limit=51", headers=auth()).status_code == 422
    assert c.get("/v1/admin/tenants?offset=-1", headers=auth()).status_code == 422
    r = c.get("/v1/admin/overview", headers=auth()).json()
    assert r["metrics"]["revenue_usd"]["value"] is None
    assert r["metrics"]["aws_cost_usd"]["status"] == "unavailable"


def test_no_admin_write_routes():
    c = client(verified, Store())
    assert c.post("/v1/admin/tenants/alpha", headers=auth()).status_code == 405
    assert c.delete("/v1/admin/tenants/alpha", headers=auth()).status_code == 405


def sample_catalog():
    return {
        "registry_version": "3", "domains": [
            {"domain_id": "privacy", "name": "Privacy", "status": "active", "controls": [
                {"control_id": "pii", "name": "PII", "severity": "high", "stages": ["input", "output"],
                 "rules": [{"rule_id": "pii.email", "title": "Email address", "use_case": "Protect email addresses",
                            "example": "do not leak these example contents"}],
                 "detector": {"implementation": ["private-path"]}},
            ]},
            {"domain_id": "security_events", "name": "Security events", "status": "planned", "controls": []},
        ]}


def test_admin_catalog_is_read_only_from_actual_provider_projection():
    from guardoath.admin_console import create_admin_router
    app = FastAPI()
    app.include_router(create_admin_router(verify_cognito_admin=verified, catalog_provider=sample_catalog))
    c = TestClient(app)
    assert c.get("/v1/admin/catalog").status_code == 401
    assert c.get("/v1/admin/catalog", headers=auth("app-key")).status_code == 401
    r = c.get("/v1/admin/catalog", headers=auth())
    assert r.status_code == 200
    assert r.json()["registry_version"] == "3"
    assert r.json()["total_categories"] == 2
    assert r.json()["total_checks"] == 1
    assert r.json()["total_rules"] == 1
    assert "private-path" not in r.text
    assert "do not leak" not in r.text
    r = c.get("/v1/admin/catalog?category_id=privacy", headers=auth())
    assert r.json()["total_categories"] == 1
    assert r.json()["categories"][0]["checks"][0]["rules"][0]["rule_id"] == "pii.email"
    assert c.get("/v1/admin/catalog?category_id=../secrets", headers=auth()).status_code == 422
    assert c.post("/v1/admin/catalog", headers=auth()).status_code == 405


def test_admin_catalog_provider_failure_denies_data():
    def broken(): raise RuntimeError("secret internal provider failure")
    app = FastAPI()
    app.include_router(create_admin_router(verify_cognito_admin=verified, catalog_provider=broken))
    response = TestClient(app).get("/v1/admin/catalog", headers=auth())
    assert response.status_code == 503
    assert "secret internal" not in response.text


def test_owner_only_cognito_user_directory_route_denial_and_sanitized_status():
    class Directory:
        def page(self, **kwargs):
            if kwargs["query"] == "fault":
                raise RuntimeError("upstream private details")
            if kwargs["query"] == "throttle":
                from guardoath.admin_users import CognitoLookupRateLimited
                raise CognitoLookupRateLimited("provider-private quota details")
            if kwargs["cursor"] == "bad":
                from guardoath.admin_users import InvalidUserCursor
                raise InvalidUserCursor("private cursor")
            return {"status": "ok", "source": "Cognito",
                    "users": [{"username": "sub-1", "email_hint": "u***@example.org",
                               "status": "CONFIRMED"}], "total": None, "next_cursor": None}
    app = FastAPI()
    app.include_router(create_admin_router(verify_cognito_admin=verified, admin_user_directory=Directory()))
    c = TestClient(app)
    assert c.get("/v1/admin/users").status_code == 401
    assert c.get("/v1/admin/users", headers=auth("customer-key")).status_code == 401
    ok = c.get("/v1/admin/users?query=ab&limit=3", headers=auth())
    assert ok.status_code == 200 and ok.json()["users"][0]["email_hint"] == "u***@example.org"
    assert ok.json()["total"] is None
    # Identity scope comes exclusively from the authenticated verifier, never the query.
    # This stub receives the principal subject and should see no untrusted override.
    assert c.get("/v1/admin/users?owner_sub=someone-else", headers=auth()).status_code == 200
    limited = c.get("/v1/admin/users?query=throttle", headers=auth())
    assert limited.status_code == 429
    assert "provider-private" not in limited.text
    assert c.get("/v1/admin/users?limit=51", headers=auth()).status_code == 422
    assert c.get("/v1/admin/users?cursor=bad", headers=auth()).status_code == 422
    fail = c.get("/v1/admin/users?query=fault", headers=auth())
    assert fail.status_code == 503 and "private details" not in fail.text

def test_cognito_user_directory_not_configured_is_not_false_zero():
    c = client(verified, Store())
    assert c.get("/v1/admin/users", headers=auth()).status_code == 503

def test_owner_only_admin_audit_receipt_endpoint_and_errors():
    from guardoath.admin_audit import AdminAuditReader
    e = "a" * 64
    class DB:
        def __init__(self, exists=True):
            self.calls = []
            self.exists = exists
        def get_item(self, **kwargs):
            self.calls.append(kwargs)
            if not self.exists:
                return {}
            return {"Item": {
                "event_id": {"S": e}, "tenant_id": {"S": "usr-good"},
                "actor_sub": {"S": "12345678-1234-1234-1234-123456789012"},
                "operation": {"S": "tenant.status"}, "when_utc": {"S": "2026-11-20T04:00:00Z"},
                "reason": {"S": "Owner reviewed this change"}, "prior_revision": {"N": "6"},
                "new_revision": {"N": "7"}, "old_status": {"S": "active"},
                "new_status": {"S": "disabled"}, "key_hashes": {"S": "must-not-leak"}}}
    d = DB()
    app = FastAPI()
    app.include_router(create_admin_router(verify_cognito_admin=verified,
                                           audit_reader=AdminAuditReader(d, "admin-audit")))
    c = TestClient(app)
    url = "/v1/admin/audit/" + e
    assert c.get(url).status_code == 401
    assert c.get(url, headers={"X-GuardOath-Key": "owner"}).status_code == 401
    assert c.get(url, headers=auth("customer")).status_code == 401
    assert d.calls == []
    ok = c.get(url, headers=auth())
    assert ok.status_code == 200 and ok.json()["audit"]["new_revision"] == 7
    assert ok.json()["audit"]["change"] == {"field": "status", "before": "active", "after": "disabled"}
    assert "must-not-leak" not in ok.text
    assert len(d.calls) == 1 and d.calls[0]["ConsistentRead"] is True
    assert c.get("/v1/admin/audit/" + "A" * 64, headers=auth()).status_code == 422
    assert len(d.calls) == 1
    missing = FastAPI()
    missing.include_router(create_admin_router(verify_cognito_admin=verified,
                                audit_reader=AdminAuditReader(DB(False), "admin-audit")))
    assert TestClient(missing).get(url, headers=auth()).status_code == 404
    assert client(verified).get(url, headers=auth()).status_code == 503

def test_exact_cognito_inspection_requires_owner_mfa_origin_and_redacts_private_attributes():
    from guardoath.admin_users import CognitoUserDirectory
    from datetime import datetime, timezone
    calls = []
    class Provider:
        def list_users(self, **kwargs):
            calls.append(kwargs)
            return {"Users":[{"Username":"unmasked-cognito-internal",
                "UserStatus":"UNCONFIRMED","Enabled":False,
                "UserLastModifiedDate":datetime(2026,11,20,tzinfo=timezone.utc),
                "Attributes":[{"Name":"email","Value":"someone@example.com"},
                              {"Name":"custom:secret","Value":"no-export"}]}]}
    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda token: VerifiedAdmin(
            "12345678-1234-1234-1234-123456789012",frozenset({"owner"}),True)
        if token == "owner" else None,
        admin_user_directory=CognitoUserDirectory(Provider(),"us-east-1_Example",b"a"*32)))
    client = TestClient(app)
    url = "/v1/admin/users/lookup"
    body = {"email":"someone@example.com"}
    origin = {"Origin":"https://guardoath.com"}
    assert client.post(url,json=body,headers=origin).status_code == 401
    assert client.post(url,json=body,headers={**origin,**auth("customer")}).status_code == 401
    assert client.post(url,json=body,headers=auth()).status_code == 403
    assert client.post(url,json=body,headers={**auth(), "Origin":"https://evil.example"}).status_code == 403
    assert calls == []
    ok = client.post(url,json=body,headers={**auth(),**origin})
    assert ok.status_code == 200 and ok.json()["user"]["status"] == "UNCONFIRMED"
    assert ok.json()["user"]["enabled"] is False
    assert "someone@example.com" not in ok.text
    assert "unmasked-cognito-internal" not in ok.text
    assert "no-export" not in ok.text
    assert ok.headers["cache-control"] == "no-store"
    assert len(calls) == 1 and calls[0]["Limit"] == 2
    assert client.post(url,json={"email":'x"@evil.example'},headers={**auth(),**origin}).status_code == 422
    assert client.post(url,json={**body,"owner_sub":"spoof"},headers={**auth(),**origin}).status_code == 422
    assert len(calls) == 1


def test_exact_cognito_inspection_unavailable_is_503_without_leaking_error():
    from guardoath.admin_users import CognitoDirectoryUnavailable
    class Broken:
        def lookup(self, **kw): raise CognitoDirectoryUnavailable("secret provider message")
    app = FastAPI()
    app.include_router(create_admin_router(verify_cognito_admin=verified, admin_user_directory=Broken()))
    result = TestClient(app).post("/v1/admin/users/lookup",json={"email":"a@example.com"},
        headers={**auth(), "Origin":"https://guardoath.com"})
    assert result.status_code == 503 and "secret provider message" not in result.text
    unavailable = client(verified)
    assert unavailable.post("/v1/admin/users/lookup",json={"email":"a@example.com"},
        headers={**auth(), "Origin":"https://guardoath.com"}).status_code == 503

def test_exact_owner_lookup_returns_429_when_rate_limited_before_provider_read():
    from guardoath.admin_users import CognitoUserDirectory
    class Provider:
        def __init__(self): self.calls=0
        def list_users(self, **kwargs):
            self.calls += 1
            return {"Users":[]}
    provider = Provider()
    service = CognitoUserDirectory(provider,"us-east-1_Example",b"a"*32,clock=lambda: 100)
    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda token: VerifiedAdmin(
            "12345678-1234-1234-1234-123456789012",frozenset({"owner"}),True)
        if token == "owner" else None,
        admin_user_directory=service))
    client = TestClient(app)
    args = {"json":{"email":"someone@example.com"},
            "headers":{**auth(),"Origin":"https://guardoath.com"}}
    for _ in range(20):
        assert client.post("/v1/admin/users/lookup", **args).status_code == 200
    refused = client.post("/v1/admin/users/lookup", **args)
    assert refused.status_code == 429
    assert provider.calls == 20

def test_catalog_metadata_preview_requires_verified_owner_and_origin_and_does_not_publish():
    app = FastAPI()
    app.include_router(create_admin_router(verify_cognito_admin=verified, catalog_provider=sample_catalog))
    c = TestClient(app)
    body = {"expected_registry_version": "3", "proposed_registry_version": "4",
            "category_id": "privacy", "check_id": "pii", "rule_id": "pii.email",
            "title": "Updated email rule title", "use_case": "Protect email addresses"}
    path = "/v1/admin/catalog/preview"
    assert c.post(path, json=body).status_code == 401
    assert c.post(path, json=body, headers=auth("app-key")).status_code == 401
    assert c.post(path, json=body, headers=auth()).status_code == 403
    assert c.post(path, json=body, headers={**auth(), "Origin": "https://evil.example"}).status_code == 403
    valid_headers = {**auth(), "Origin": "https://guardoath.com"}
    result = c.post(path, json=body, headers=valid_headers)
    assert result.status_code == 200
    preview = result.json()
    assert preview["status"] == "preview_only" and preview["published"] is False
    assert preview["persisted"] is False and preview["tenant_policies_changed"] is False
    assert preview["detector_behavior_changed"] is False
    assert preview["registry_version"] == "3" and preview["candidate_registry_version"] == "4"
    assert preview["changes"]["title"]["before"] == "Email address"
    assert preview["changes"]["title"]["after"] == "Updated email rule title"
    assert "private-path" not in result.text
    unchanged = c.get("/v1/admin/catalog", headers=auth()).json()
    assert unchanged["registry_version"] == "3"
    assert unchanged["categories"][0]["checks"][0]["rules"][0]["title"] == "Email address"
    assert c.get("/v1/admin/features", headers=auth()).json()["catalog_preview"] is True
    assert c.get("/v1/admin/features", headers=auth()).json()["rule_publishing"] is False
    for alteration, expected_status in [
        ({"expected_registry_version": "2"}, 409),
        ({"proposed_registry_version": "5"}, 409),
        ({"rule_id": "pii.missing"}, 409),
        ({"category_id": "unknown"}, 409),
        ({"title": "Email address"}, 422),
        ({"title": "x" * 121}, 422),
        ({"title": "unsafe\ncontrol"}, 422),
        ({"unknown_customer_permission": True}, 422),
    ]:
        candidate = {**body, **alteration}
        assert c.post(path, json=candidate, headers=valid_headers).status_code == expected_status


def test_catalog_preview_provider_failure_stays_unavailable_and_no_bypass():
    def broken():
        raise RuntimeError("private server catalog")
    app = FastAPI()
    app.include_router(create_admin_router(verify_cognito_admin=verified, catalog_provider=broken))
    body = {"expected_registry_version": "3", "proposed_registry_version": "4",
            "category_id": "privacy", "check_id": "pii", "rule_id": "pii.email",
            "title": "Updated email title", "use_case": "Protect email addresses"}
    r = TestClient(app).post("/v1/admin/catalog/preview", json=body,
                             headers={**auth(), "Origin": "https://guardoath.com"})
    assert r.status_code == 503 and "private server" not in r.text
    assert client(verified, Store()).post("/v1/admin/catalog/preview", json=body,
            headers={**auth(), "Origin": "https://guardoath.com"}).status_code == 503


def test_one_owner_origin_policy_for_every_sensitive_admin_post():
    class Directory:
        def __init__(self): self.calls = 0
        def lookup(self, **kwargs):
            self.calls += 1
            return {"status": "not_found", "user": None}
    class Actions:
        def __init__(self): self.calls = []
        def change_status(self, **kwargs): self.calls.append("status"); return {"ok": True}
        def change_rule_action(self, **kwargs): self.calls.append("rule"); return {"ok": True}
        def cap_monthly_allowance(self, **kwargs): self.calls.append("cap"); return {"ok": True}
    directory, actions = Directory(), Actions()
    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=verified, admin_user_directory=directory,
        catalog_provider=sample_catalog, admin_mutator=actions,
        record_validator=lambda *_: object(), rule_exists=lambda *_: True))
    c = TestClient(app)
    operations = [
        ("/v1/admin/users/lookup", {"email": "someone@example.com"}),
        ("/v1/admin/catalog/preview", {
            "expected_registry_version": "3", "proposed_registry_version": "4",
            "category_id": "privacy", "check_id": "pii", "rule_id": "pii.email",
            "title": "Revised email rule title", "use_case": "Protect email addresses"}),
        ("/v1/admin/tenants/alpha/status", {
            "expected_revision": 2, "status": "disabled", "confirmation": "alpha",
            "reason": "Operator confirmed status change", "idempotency_key": "Z" * 20}),
        ("/v1/admin/tenants/alpha/rule-action", {
            "expected_revision": 2, "stage": "input", "rule_id": "pii.email",
            "action": "block", "confirmation": "alpha",
            "reason": "Operator confirmed rule change", "idempotency_key": "Z" * 20}),
        ("/v1/admin/tenants/alpha/allowance-cap", {
            "expected_revision": 2, "new_limit": 100, "confirmation": "alpha",
            "reason": "Operator confirmed cost control", "idempotency_key": "Z" * 20}),
    ]
    for endpoint, body in operations:
        for origin in (None, "https://evil.example", "https://guardoath.com.evil.example",
                       "null", "http://127.0.1:8080", "http://localhost:0",
                       "http://localhost:65536", "http://localhost:8080/"):
            headers = auth()
            if origin is not None:
                headers["Origin"] = origin
            assert c.post(endpoint, json=body, headers=headers).status_code == 403, (endpoint, origin)
        duplicate_headers = [("Authorization", "Bearer owner"), ("Origin", "https://guardoath.com"),
                             ("Origin", "https://evil.example")]
        assert c.post(endpoint, json=body, headers=duplicate_headers).status_code == 403
        # A remote application must not accept a forged localhost Origin,
        # even with a spoofed Host header. Only its production origin is valid.
        for origin in ("http://localhost:8000", "http://127.0.0.1:8080"):
            assert c.post(endpoint, json=body, headers={**auth(), "Origin": origin}).status_code == 403
        assert c.post(endpoint, json=body, headers={
            **auth(), "Host": "localhost:8000", "Origin": "http://localhost:8000",
        }).status_code == 403
        assert c.post(endpoint, json=body, headers={
            **auth(), "Origin": "https://guardoath.com",
        }).status_code == 200

    # Real local ASGI listeners can use exact same-origin loopback requests;
    # mismatched ports, schemes and hostnames cannot borrow that exception.
    for local_url in ("http://localhost:8000", "http://127.0.0.1:8080"):
        local_client = TestClient(app, base_url=local_url)
        for endpoint, body in operations:
            assert local_client.post(endpoint, json=body, headers={
                **auth(), "Origin": local_url,
            }).status_code == 200, (local_url, endpoint)
            assert local_client.post(endpoint, json=body, headers={
                **auth(), "Origin": local_url.rsplit(":", 1)[0] + ":9999",
            }).status_code == 403
            other_host = ("127.0.0.1" if "localhost" in local_url else "localhost")
            assert local_client.post(endpoint, json=body, headers={
                **auth(), "Origin": local_url.replace(
                    "localhost" if "localhost" in local_url else "127.0.0.1", other_host),
            }).status_code == 403
    assert directory.calls == 3
    assert len(actions.calls) == 9


def test_tenant_detail_and_legacy_directory_reject_ambiguous_authoritative_records():
    """Admin detail must not stringify private/corrupt account data into a paid state."""
    from datetime import datetime, timezone
    import pytest

    month = datetime.now(timezone.utc).strftime("%Y-%m")
    cases = [
        ("tenant_id", "beta"), ("demo", "false"), ("demo", 0),
        ("demo", None), ("status", "pending"), ("status", None),
        ("subscription_status", "paid"), ("subscription_status", True),
        ("subscription_status", {"private": "cus_INTERNAL"}),
        ("display_name", {"private": "cus_INTERNAL"}),
        ("updated_at", {"private": "cus_INTERNAL"}),
        ("revision", True), ("revision", -1), ("revision", 10**19),
        ("usage", {month: "cus_INTERNAL"}),
        ("usage", {month: True}),
        ("usage", {"2026-13": 7}),
        ("usage", {month: {"private": "cus_INTERNAL"}}),
        ("usage", ["cus_INTERNAL"]),
    ]
    for field, malformed in cases:
        class BrokenStore(Store):
            def get(self, name):
                record = Record()
                setattr(record, field, malformed)
                return record

        c = client(verified, BrokenStore())
        for path in ("/v1/admin/tenants/alpha", "/v1/admin/tenants"):
            assert c.get(path).status_code == 401
            response = c.get(path, headers=auth())
            assert response.status_code == 503, (field, malformed, response.text)
            assert "cus_INTERNAL" not in response.text
            assert "private" not in response.text


def test_owner_saved_overage_projection_never_infers_a_hard_cap():
    for saved, expected in (("block", "block"),
                            ("allow_and_record", "allow_and_record"),
                            ("invalid", None), (None, None)):
        class Saved(Store):
            def get(self, name):
                row = Record()
                row.overage = saved
                return row
        c = client(verified, Saved())
        assert c.get("/v1/admin/tenants/alpha").status_code == 401
        for path in ("/v1/admin/tenants/alpha", "/v1/admin/tenants"):
            response = c.get(path, headers=auth())
            assert response.status_code == 200
            row = response.json().get("tenant") or response.json()["tenants"][0]
            assert row["overage_policy"] == expected
            assert "cus_PRIVATE" not in response.text


def test_detail_legacy_unknown_allowance_and_key_metadata_do_not_fabricate_zero():
    class LegacyStore(Store):
        def get(self, name):
            record = Record()
            record.monthly_allowance = "unavailable"
            record.key_hashes = {"private": "NEVER_EXPOSE"}
            record.subscription_status = None
            return record

    c = client(verified, LegacyStore())
    for path in ("/v1/admin/tenants/alpha", "/v1/admin/tenants"):
        result = c.get(path, headers=auth())
        assert result.status_code == 200, result.text
        tenant = result.json().get("tenant") or result.json()["tenants"][0]
        assert tenant["monthly_allowance"] is None
        assert tenant["api_key_count"] is None
        assert tenant["subscription_status"] is None
        assert "NEVER_EXPOSE" not in result.text


def test_legacy_tenant_directory_rejects_duplicates_missing_and_broken_ids():
    for ids, missing in [
        (("alpha", "alpha"), False), (("alpha", "../bad"), False),
        (("alpha", "beta"), True), (("alpha", True), False),
    ]:
        class InvalidStore(Store):
            def tenant_ids(self):
                return ids
            def get(self, name):
                return None if missing and name == "beta" else Record()
        c = client(verified, InvalidStore())
        response = c.get("/v1/admin/tenants", headers=auth())
        assert response.status_code == 503, (ids, response.text)


@pytest.mark.parametrize("model,fields", [
    (StatusChange, {
        "status": "disabled", "confirmation": "alpha",
        "reason": "Operator verified account suspension",
        "idempotency_key": "revisionbounds123456"}),
    (TenantRuleChange, {
        "stage": "input", "rule_id": "pii.email", "action": "block",
        "confirmation": "alpha", "reason": "Operator verified rule action",
        "idempotency_key": "revisionbounds123456"}),
    (AllowanceCap, {
        "new_limit": 100, "confirmation": "alpha",
        "reason": "Operator verified request cap",
        "idempotency_key": "revisionbounds123456"}),
    (RevokeTenantKeys, {
        "confirmation": "alpha", "reason": "Operator verified key exposure",
        "idempotency_key": "revisionbounds123456"}),
    (ReconcileUnpaidSubscription, {
        "confirmation": "RECONCILE alpha", "reason": "Operator verified unpaid billing",
        "idempotency_key": "revisionbounds123456"}),
])
def test_all_owner_mutation_models_preserve_exact_current_and_next_revisions(model, fields):
    # A successful mutation increments the saved revision and publishes the
    # result to JavaScript and the signed owner audit receipt.
    assert model.model_validate({
        **fields, "expected_revision": MAX_MUTABLE_REVISION,
    }).expected_revision == MAX_MUTABLE_REVISION
    for unsafe in (MAX_MUTABLE_REVISION + 1, 1 << 53, True, 0, -1,
                   str(MAX_MUTABLE_REVISION)):
        with pytest.raises(ValidationError):
            model.model_validate({**fields, "expected_revision": unsafe})


def test_owner_revision_above_exact_audit_range_is_rejected_before_backend_write():
    class NoWrite:
        def __init__(self):
            self.calls = 0

        def change_status(self, **kwargs):
            self.calls += 1
            raise AssertionError("Unrepresentable revision reached tenant mutation")

    actions = NoWrite()
    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=verified, admin_mutator=actions))
    response = TestClient(app).post("/v1/admin/tenants/alpha/status", json={
        "expected_revision": MAX_MUTABLE_REVISION + 1,
        "status": "disabled", "confirmation": "alpha",
        "reason": "Operator verified account suspension",
        "idempotency_key": "revisionbounds123456",
    }, headers={**auth(), "Origin": "https://guardoath.com"})
    assert response.status_code == 422
    assert actions.calls == 0


@pytest.mark.parametrize("overflow", ["revision", "usage"])
def test_owner_tenant_detail_and_fallback_directory_refuse_rounded_source_numbers(overflow):
    """A full single-tenant read must enforce the directory's browser precision bound."""
    from datetime import datetime, timezone

    current = datetime.now(timezone.utc).strftime("%Y-%m")
    record = Record(revision=(1 << 53) - 1)
    record.usage = {current: (1 << 53) - 1}
    class SingleRecordStore:
        def tenant_ids(self): return ("alpha",)
        def get(self, tid): return record if tid == "alpha" else None

    c = client(verified, SingleRecordStore())
    detail = c.get("/v1/admin/tenants/alpha", headers=auth())
    assert detail.status_code == 200
    assert detail.json()["tenant"]["revision"] == (1 << 53) - 1
    assert detail.json()["tenant"]["usage_month_count"] == (1 << 53) - 1

    if overflow == "revision":
        record.revision = 1 << 53
    else:
        record.usage[current] = 1 << 53

    for path in ("/v1/admin/tenants/alpha", "/v1/admin/tenants"):
        refused = c.get(path, headers=auth())
        assert refused.status_code == 503
        assert str(1 << 53) not in refused.text
        assert "SUPER_SECRET" not in refused.text
    assert c.get("/v1/admin/tenants/alpha", headers=auth("customer")).status_code == 401
