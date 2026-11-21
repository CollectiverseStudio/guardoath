import json
from dataclasses import dataclass
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from guardoath.admin_console import VerifiedAdmin, create_admin_router
from guardoath.admin_directory import DirectoryUnavailable, DynamoAdminDirectory, InvalidCursor

KEY = b"sample-test-signing-key-32-bytes-long-not-production"
OWNER = "12345678-1234-1234-1234-123456789012"
OTHER_OWNER = "87654321-4321-4321-4321-210987654321"


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
    one = d.page(owner_sub=OWNER, limit=1)
    assert [t["tenant_id"] for t in one["tenants"]] == ["alpha"]
    two = d.page(owner_sub=OWNER, limit=1, cursor=one["next_cursor"])
    assert [t["tenant_id"] for t in two["tenants"]] == ["beta"]
    assert len(fake.calls) == 2 and all(c["Limit"] == 1 for c in fake.calls)
    assert one["total"] is None
    assert one["tenants"][0]["api_key_count"] == 1
    assert "HIDDEN_SECRET" not in str(one)
    assert "cus_SECRET" not in str(two)


def test_cursor_tampering_filter_change_and_expiry_fail_closed():
    d = directory()
    token = d.page(owner_sub=OWNER, limit=1)["next_cursor"]
    for candidate, term in [(token + "x", ""), (token, "alpha"), ("garbage", "")]:
        with pytest.raises(InvalidCursor): d.page(owner_sub=OWNER, limit=1, search=term, cursor=candidate)
    expired = directory(clock=lambda: 2000)
    with pytest.raises(InvalidCursor): expired.page(owner_sub=OWNER, limit=1, cursor=token)


def test_scan_error_and_invalid_results_fail_closed():
    class Broken:
        def scan(self, **kw): raise RuntimeError("private provider information")
    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda _: VerifiedAdmin(OWNER, frozenset({"owner"}), True),
        admin_directory=directory(Broken())))
    r = TestClient(app).get("/v1/admin/tenants", headers={"Authorization": "Bearer valid"})
    assert r.status_code == 503 and "private provider" not in r.text


def test_admin_endpoint_auth_redaction_and_cursor_validation():
    fake = Client()
    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda t: VerifiedAdmin(OWNER, frozenset({"owner"}), True) if t == "owner" else None,
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
    response = directory(WithUsage()).page(owner_sub=OWNER, limit=5)
    assert response["tenants"][0]["usage_month_count"] == 17
    assert response["tenants"][0]["usage_month"] == current
    assert "persisted tenant" in response["tenants"][0]["subscription_source"]
    assert directory(Client()).page(owner_sub=OWNER, limit=1)["tenants"][0]["usage_month_count"] is None
    assert response["total"] is None  # bounded scan is not global total


def test_invalid_usage_record_rejected_not_silently_zeroed():
    from datetime import datetime, timezone
    current = datetime.now(timezone.utc).strftime("%Y-%m")
    class Malformed:
        def scan(self, **kw):
            row=item("alpha")
            row["usage"]={"M":{current:{"N":"not-a-number"}}}
            return {"Items":[row]}
    with pytest.raises(DirectoryUnavailable):directory(Malformed()).page(owner_sub=OWNER, limit=10)

def test_missing_or_malformed_legacy_key_metadata_is_unknown_not_zero():
    class Missing:
        def scan(self, **kw):
            row = item("alpha")
            doc = json.loads(row["record"]["S"])
            doc.pop("key_hashes", None)
            row["record"]["S"] = json.dumps(doc)
            return {"Items": [row]}
    assert directory(Missing()).page(owner_sub=OWNER, limit=5)["tenants"][0]["api_key_count"] is None
    class Malformed:
        def scan(self, **kw):
            row = item("alpha")
            doc = json.loads(row["record"]["S"])
            doc["key_hashes"] = "plaintext-not-a-list"
            row["record"]["S"] = json.dumps(doc)
            return {"Items": [row]}
    assert directory(Malformed()).page(owner_sub=OWNER, limit=5)["tenants"][0]["api_key_count"] is None


def test_directory_cursor_owner_filter_size_binding_and_no_extra_scan():
    fake = Client()
    adapter = directory(fake)
    token = adapter.page(owner_sub=OWNER, limit=1, search="a")["next_cursor"]
    assert token and OWNER not in token
    for subject, size, term in [(OTHER_OWNER, 1, "a"), (OWNER, 2, "a"), (OWNER, 1, "b")]:
        with pytest.raises(InvalidCursor):
            adapter.page(owner_sub=subject, limit=size, search=term, cursor=token)
    assert len(fake.calls) == 1
    assert adapter.page(owner_sub=OWNER, limit=1, search="a", cursor=token)["tenants"][0]["tenant_id"] == "beta"
    assert len(fake.calls) == 2


def test_legacy_empty_expired_forged_cursors_fail_before_scan():
    fake = Client()
    adapter = directory(fake)
    token = adapter.page(owner_sub=OWNER, limit=1)["next_cursor"]
    old = adapter._sign({"v": 1, "table": "tenants", "search": "",
                         "key": {"tenant_id": {"S": "alpha"}}, "exp": 1900})
    for invalid in (old, token + "tampered", ""):
        with pytest.raises(InvalidCursor):
            adapter.page(owner_sub=OWNER, limit=1, cursor=invalid)
    with pytest.raises(InvalidCursor):
        directory(fake, clock=lambda: 2000).page(owner_sub=OWNER, limit=1, cursor=token)
    assert len(fake.calls) == 1


@pytest.mark.parametrize("subject", ["", "owner@example.com", "x"*81, True, None])
def test_invalid_directory_owner_subject_never_scans(subject):
    fake = Client()
    with pytest.raises(ValueError):
        directory(fake).page(owner_sub=subject, limit=1)
    assert fake.calls == []


def test_missing_or_malformed_legacy_allowance_projects_unknown():
    class Legacy:
        def __init__(self, raw): self.raw = raw
        def scan(self, **kwargs):
            row = item("alpha")
            document = json.loads(row["record"]["S"])
            if self.raw is None: document.pop("monthly_allowance")
            else: document["monthly_allowance"] = self.raw
            row["record"]["S"] = json.dumps(document)
            return {"Items": [row]}
    for raw in (None, "invalid", -1, True):
        response = directory(Legacy(raw)).page(owner_sub=OWNER, limit=1)
        assert response["tenants"][0]["monthly_allowance"] is None
    assert directory(Client()).page(owner_sub=OWNER, limit=1)["tenants"][0]["monthly_allowance"] == 10000


def test_tenant_http_cursor_replay_by_another_owner_is_refused():
    fake = Client()
    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda token: VerifiedAdmin(
            OWNER if token == "owner1" else OTHER_OWNER, frozenset({"owner"}), True),
        admin_directory=directory(fake)))
    client = TestClient(app)
    response = client.get("/v1/admin/tenants?limit=1", headers={"Authorization": "Bearer owner1"})
    assert response.status_code == 200
    replay = client.get("/v1/admin/tenants", params={"limit": 1, "cursor": response.json()["next_cursor"]},
                        headers={"Authorization": "Bearer owner2"})
    assert replay.status_code == 422
    assert len(fake.calls) == 1


@pytest.mark.parametrize("field,value", [
    ("demo", "false"), ("demo", 0), ("demo", None), ("demo", []),
    ("status", "pending"), ("status", None), ("status", ["active"]),
    ("subscription_status", "paid"), ("subscription_status", True),
    ("subscription_status", []), ("display_name", {"raw": "secret"}),
    ("display_name", ""), ("updated_at", {"bad": "date"}),
])
def test_directory_refuses_ambiguous_account_identity_and_subscription(field, value):
    class Corrupt:
        def scan(self, **kw):
            row = item("alpha")
            doc = json.loads(row["record"]["S"])
            doc[field] = value
            row["record"]["S"] = json.dumps(doc)
            return {"Items": [row]}
    with pytest.raises(DirectoryUnavailable):
        directory(Corrupt()).page(owner_sub=OWNER, limit=1)


@pytest.mark.parametrize("change", [
    "missing_demo", "wrong_tenant", "missing_revision", "negative_revision",
    "float_revision", "huge_revision", "duplicate_id", "invalid_usage_map",
    "invalid_historical_usage", "malformed_checkpoint",
])
def test_directory_rejects_corrupt_provider_pages_before_returning_any_tenants(change):
    class Corrupt:
        def scan(self, **kw):
            row = item("alpha")
            doc = json.loads(row["record"]["S"])
            if change == "missing_demo":
                doc.pop("demo")
            elif change == "wrong_tenant":
                doc["tenant_id"] = "beta"
            elif change == "missing_revision":
                row.pop("revision")
            elif change == "negative_revision":
                row["revision"] = {"N": "-1"}
            elif change == "float_revision":
                row["revision"] = {"N": "1.5"}
            elif change == "huge_revision":
                row["revision"] = {"N": "9" * 100}
            elif change == "duplicate_id":
                return {"Items": [row, row]}
            elif change == "invalid_usage_map":
                row["usage"] = {"M": "bad"}
            elif change == "invalid_historical_usage":
                row["usage"] = {"M": {"2026-13": {"N": "1"}}}
            elif change == "malformed_checkpoint":
                return {"Items": [row], "LastEvaluatedKey": {"tenant_id": {"S": "../bad"}}}
            row["record"]["S"] = json.dumps(doc)
            return {"Items": [row]}
    with pytest.raises(DirectoryUnavailable):
        directory(Corrupt()).page(owner_sub=OWNER, limit=2)


def test_admin_directory_corruption_is_503_not_client_422_and_never_leaks_provider_data():
    class Corrupt:
        def scan(self, **kw):
            row = item("alpha")
            doc = json.loads(row["record"]["S"])
            doc["demo"] = "false"
            doc["payer_id"] = "cus_PRIVATE_PROVIDED"
            row["record"]["S"] = json.dumps(doc)
            return {"Items": [row]}
    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda t: VerifiedAdmin(OWNER, frozenset({"owner"}), True)
        if t == "owner" else None, admin_directory=directory(Corrupt())))
    client = TestClient(app)
    assert client.get("/v1/admin/tenants").status_code == 401
    response = client.get("/v1/admin/tenants", headers={"X-GuardOath-Admin-Session": "owner"})
    assert response.status_code == 503
    assert "cus_PRIVATE_PROVIDED" not in response.text
    assert "tenant directory" in response.text.lower()


def test_valid_legacy_unknown_billing_stays_unknown_and_real_demo_boolean_is_preserved():
    class Legacy:
        def scan(self, **kw):
            row = item("alpha")
            doc = json.loads(row["record"]["S"])
            doc["demo"] = True
            doc.pop("subscription_status")
            row["record"]["S"] = json.dumps(doc)
            return {"Items": [row]}
    projected = directory(Legacy()).page(owner_sub=OWNER, limit=2)["tenants"][0]
    assert projected["demo"] is True
    assert projected["subscription_status"] is None
    assert projected["status"] == "active"
    assert projected["api_key_count"] == 1


def test_filter_cannot_hide_malformed_provider_account_within_one_scanned_page():
    class Corrupt:
        def scan(self, **kw):
            row = item("beta")
            doc = json.loads(row["record"]["S"])
            doc["demo"] = "wrong-type"
            row["record"]["S"] = json.dumps(doc)
            return {"Items": [row]}
    with pytest.raises(DirectoryUnavailable):
        directory(Corrupt()).page(owner_sub=OWNER, limit=1, search="alpha")
