from dataclasses import dataclass
from fastapi import FastAPI
from fastapi.testclient import TestClient
from guardoath.admin_console import VerifiedAdmin, create_admin_router


@dataclass
class Record:
    tenant_id: str = "alpha"
    display_name: str = "Example Tenant"
    status: str = "active"
    demo: bool = False
    subscription_status: str = "inactive"
    monthly_allowance: int = 10000
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
    assert c.get("/v1/admin/users?limit=51", headers=auth()).status_code == 422
    assert c.get("/v1/admin/users?cursor=bad", headers=auth()).status_code == 422
    fail = c.get("/v1/admin/users?query=fault", headers=auth())
    assert fail.status_code == 503 and "private details" not in fail.text

def test_cognito_user_directory_not_configured_is_not_false_zero():
    c = client(verified, Store())
    assert c.get("/v1/admin/users", headers=auth()).status_code == 503
