import json
from dataclasses import dataclass
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from guardoath.admin_console import VerifiedAdmin, create_admin_router
from guardoath.admin_directory import DynamoAdminDirectory, InvalidCursor

KEY = b"sample-test-signing-key-32-bytes-long-not-production"


def item(tid):
    return {"tenant_id": {"S": tid}, "revision": {"N": "3"},
            "record": {"S": json.dumps({"tenant_id": tid, "display_name": "Tenant " + tid,
                                    "status": "active", "demo": False, "subscription_status": "inactive",
                                    "monthly_allowance": 10000, "updated_at": "2026-11-16T00:00:00Z",
                                    "key_hashes": ["HIDDEN_SECRET"], "payer_id": "cus_SECRET"})}}


class Client:
    def __init__(self): self.calls = []
    def scan(self, **kwargs):
        self.calls.append(kwargs)
        keys = ["alpha", "beta", "gamma"]
        start = kwargs.get("ExclusiveStartKey", {}).get("tenant_id", {}).get("S")
        offset = keys.index(start) + 1 if start else 0
        page = keys[offset:offset + kwargs["Limit"]]
        result = {"Items": [item(k) for k in page]}
        if offset + len(page) < len(keys):
            result["LastEvaluatedKey"] = {"tenant_id": {"S": page[-1]}}
        return result


def directory(client=None, clock=lambda: 1000):
    return DynamoAdminDirectory(client or Client(), "tenants", KEY, clock)


def test_bounded_cursor_pagination_avoids_full_scan():
    fake = Client()
    d = directory(fake)
    one = d.page(limit=1)
    assert [t["tenant_id"] for t in one["tenants"]] == ["alpha"]
    two = d.page(limit=1, cursor=one["next_cursor"])
    assert [t["tenant_id"] for t in two["tenants"]] == ["beta"]
    assert len(fake.calls) == 2 and all(c["Limit"] == 1 for c in fake.calls)
    assert one["total"] is None
    assert "HIDDEN_SECRET" not in str(one)
    assert "cus_SECRET" not in str(two)


def test_cursor_tampering_filter_change_and_expiry_fail_closed():
    d = directory()
    token = d.page(limit=1)["next_cursor"]
    for candidate, term in [(token + "x", ""), (token, "alpha"), ("garbage", "")]:
        with pytest.raises(InvalidCursor): d.page(limit=1, search=term, cursor=candidate)
    expired = directory(clock=lambda: 2000)
    with pytest.raises(InvalidCursor): expired.page(limit=1, cursor=token)


def test_scan_error_and_invalid_results_fail_closed():
    class Broken:
        def scan(self, **kw): raise RuntimeError("private provider information")
    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda _: VerifiedAdmin("admin", frozenset({"owner"}), True),
        admin_directory=directory(Broken())))
    r = TestClient(app).get("/v1/admin/tenants", headers={"Authorization": "Bearer valid"})
    assert r.status_code == 503 and "private provider" not in r.text


def test_admin_endpoint_auth_redaction_and_cursor_validation():
    fake = Client()
    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda t: VerifiedAdmin("admin", frozenset({"owner"}), True) if t == "owner" else None,
        admin_directory=directory(fake)))
    client = TestClient(app)
    assert client.get("/v1/admin/tenants").status_code == 401
    first = client.get("/v1/admin/tenants?limit=1", headers={"Authorization": "Bearer owner"})
    assert first.status_code == 200
    assert first.json()["next_cursor"]
    assert "HIDDEN_SECRET" not in first.text
    assert client.get("/v1/admin/tenants?cursor=wrong", headers={"Authorization": "Bearer owner"}).status_code == 422
    assert client.get("/v1/admin/tenants?limit=51", headers={"Authorization": "Bearer owner"}).status_code == 422


def test_usage_is_real_month_counter_and_missing_usage_is_unknown():
    from datetime import datetime, timezone
    current = datetime.now(timezone.utc).strftime("%Y-%m")
    class WithUsage:
        def scan(self, **kw):
            row = item("alpha")
            row["usage"] = {"M": {current: {"N": "17"}}}
            return {"Items": [row]}
    response = directory(WithUsage()).page(limit=5)
    assert response["tenants"][0]["usage_month_count"] == 17
    assert response["tenants"][0]["usage_month"] == current
    assert "persisted tenant" in response["tenants"][0]["subscription_source"]
    assert directory(Client()).page(limit=1)["tenants"][0]["usage_month_count"] is None
    assert response["total"] is None  # bounded scan is not global total


def test_invalid_usage_record_rejected_not_silently_zeroed():
    from datetime import datetime, timezone
    current = datetime.now(timezone.utc).strftime("%Y-%m")
    class Malformed:
        def scan(self, **kw):
            row=item("alpha")
            row["usage"]={"M":{current:{"N":"not-a-number"}}}
            return {"Items":[row]}
    with pytest.raises(ValueError):directory(Malformed()).page(limit=10)
