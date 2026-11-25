"""Fresh-process owner publication acceptance through the real mounted app.

Only the existing harness's external Cognito/DynamoDB/Stripe transports and
packaged source marker are synthetic. No route, verifier or service is mocked.
"""
import json

from fastapi.testclient import TestClient

import admin_browser_app as mounted
from admin_test_support import AdminTestDynamo, ADMIN_BROWSER_AUDIT_KEY
from guardoath.admin_audit import AdminAuditReader
from guardoath.admin_catalog_drafts import AdminCatalogDrafts
from guardoath.admin_catalog_publication_state import DynamoCatalogPublicationState, document_hash


PREFIX = "/v1/admin/catalog/publications"


def tenant_rows(database):
    return {name: database.get_item(TableName="tenant_table",
        Key={"tenant_id": {"S": name}}, ConsistentRead=True)["Item"]
        for name in ("usr-admin-alpha", "usr-admin-beta")}


def main():
    before = tenant_rows(mounted.db)
    client = TestClient(mounted.app, base_url=mounted.ORIGIN)
    exchanged = client.post("/v1/admin/auth/exchange",
        json={"code": "owner", "code_verifier": "v" * 43})
    assert exchanged.status_code == 200, exchanged.text
    headers = {"X-GuardOath-Admin-Session": exchanged.json()["access_token"],
               "Origin": mounted.ORIGIN}
    audits = []

    def accepted(response):
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        value = response.json()
        audits.append(value["read_audit_event_id"])
        return value

    catalog = accepted(client.get("/v1/admin/catalog", headers=headers))
    category, check, rule = next((category, check, rule)
        for category in catalog["categories"] for check in category["checks"]
        for rule in check["rules"] if rule["rule_id"] == "pii.email_address")
    draft = accepted(client.post("/v1/admin/catalog/drafts", headers=headers, json={
        "expected_registry_version": catalog["registry_version"],
        "proposed_registry_version": str(int(catalog["registry_version"]) + 1),
        "category_id": category["category_id"], "check_id": check["check_id"],
        "rule_id": rule["rule_id"], "title": "Mounted owner email description",
        "use_case": rule["use_case"], "reason": "Review the actual mounted owner catalog",
        "idempotency_key": "mounted-catalog-draft-001"}))
    inspected = accepted(client.get("/v1/admin/catalog/drafts/" + draft["draft_id"], headers=headers))
    assert inspected["stale"] is False
    review = accepted(client.post("/v1/admin/catalog/drafts/" + draft["draft_id"] + "/review",
        headers=headers, json={"confirmation": "REVIEW " + draft["draft_id"][-8:],
            "reason": "Approve the exact mounted descriptive change",
            "idempotency_key": "mounted-catalog-review-001"}))
    manifest = accepted(client.get("/v1/admin/catalog/reviews/" + review["review_id"] + "/manifest",
        headers=headers))
    features = accepted(client.get("/v1/admin/features", headers=headers))
    writer = mounted.phase == "1"
    assert features["catalog_publication_preview"] is True
    assert features["catalog_publication_history"] is True
    assert features["catalog_publication_requests"] is writer
    assert features["rule_publishing"] is False

    reads = []
    get_item = mounted.db.get_item
    def recorded_read(**kwargs):
        reads.append(kwargs)
        return get_item(**kwargs)
    mounted.db.get_item = recorded_read
    source_reads = []
    def packaged_source():
        source_reads.append(True)
        return "a" * 40
    mounted.catalog_publications.source_provider = packaged_source
    preview_body = {"review_id": review["review_id"], "operation": "publish_metadata"}
    preview = accepted(client.post(PREFIX + "/preview", headers=headers, json=preview_body))
    assert preview["writer_enabled"] is writer and preview["control_available"] is True
    assert preview["candidate_metadata_sha256"] == manifest["candidate_metadata_sha256"]
    assert mounted.catalog_publications.state.read_control() is None
    body = {**preview_body, "expected_source_sha": preview["base_source_sha"],
        "expected_control_hash": preview["expected_control_hash"],
        "confirmation": preview["confirmation"], "reason": "Prepare the independently reviewed source candidate",
        "action_reference": "mounted-publication-action-001"}
    operations = [("POST", PREFIX + "/preview", preview_body),
        ("POST", PREFIX + "/recover", {"action_reference": body["action_reference"]}),
        ("GET", PREFIX + "/" + "1" * 64, None)]
    if writer:
        operations.append(("POST", PREFIX, body))

    def send(method, path, payload, request_headers):
        return client.request(method, path, headers=request_headers,
            **({"json": payload} if payload is not None else {}))

    denied = {}
    for code in ("customer", "no-mfa", "stale-mfa", "forged"):
        counts = (len(reads), len(source_reads), len(mounted.db.audit_items()), mounted.db.transactions)
        login = client.post("/v1/admin/auth/exchange", json={"code": code, "code_verifier": "v" * 43})
        assert login.status_code in (401, 403)
        rejected_headers = {**headers, "X-GuardOath-Admin-Session": mounted.signed_token(code)}
        statuses = [send(method, path, payload, rejected_headers).status_code
                    for method, path, payload in operations]
        assert all(status in (401, 403) for status in statuses), (code, statuses)
        assert counts == (len(reads), len(source_reads), len(mounted.db.audit_items()), mounted.db.transactions)
        denied[code] = statuses

    for method, path, payload in operations:
        if method == "POST":
            counts = (len(reads), len(source_reads), len(mounted.db.audit_items()), mounted.db.transactions)
            assert send(method, path, payload, {**headers, "Origin": "https://untrusted.example.invalid"}).status_code == 403
            assert counts == (len(reads), len(source_reads), len(mounted.db.audit_items()), mounted.db.transactions)
    mounted.db.fail_audit = True
    counts = (len(reads), len(source_reads), mounted.db.transactions)
    for method, path, payload in operations:
        assert send(method, path, payload, headers).status_code == 503
        assert counts == (len(reads), len(source_reads), mounted.db.transactions)
    mounted.db.fail_audit = False

    receipt = None
    if writer:
        receipt = accepted(client.post(PREFIX, headers=headers, json=body))
        assert receipt["status"] == "requested" and receipt["source_preparation"] is None
        assert receipt["action_reference"] == body["action_reference"]
        assert receipt["review_id"] == review["review_id"] and receipt["draft_id"] == draft["draft_id"]
        assert mounted.db.transactions == 1
        def unavailable_source():
            raise AssertionError("Historical recovery must not depend on the current artifact")
        mounted.catalog_publications.source_provider = unavailable_source
        prior_source_reads = len(source_reads)
        replay = accepted(client.post(PREFIX, headers=headers, json=body))
        recovered = accepted(client.post(PREFIX + "/recover", headers=headers,
            json={"action_reference": body["action_reference"]}))
        history = accepted(client.get(PREFIX + "/" + receipt["request_id"], headers=headers))
        assert len(source_reads) == prior_source_reads
        for value in (receipt, replay, recovered, history):
            assert value["integrity_verified"] is True and value["request_id"] == receipt["request_id"]
            assert value["intent_sha256"] == receipt["intent_sha256"]
            assert all(value[name] is False for name in ("published", "remote_published", "ci_verified",
                "deployed", "tenant_policies_changed", "detector_behavior_changed"))
        assert client.post(PREFIX, headers=headers, json={**body,
            "reason": "Different text with the same retained action"}).status_code == 409
        assert mounted.db.transactions == 1
    else:
        assert client.post(PREFIX, headers=headers, json=body).status_code == 404
        assert client.post(PREFIX + "/recover", headers=headers,
            json={"action_reference": body["action_reference"]}).status_code == 404
        assert client.get(PREFIX + "/" + "1" * 64, headers=headers).status_code == 404
        assert mounted.db.transactions == 0

    reopened = AdminTestDynamo(mounted.path)
    try:
        state = DynamoCatalogPublicationState(reopened, "audit_table", ADMIN_BROWSER_AUDIT_KEY)
        drafts = AdminCatalogDrafts(reopened, "audit_table", ADMIN_BROWSER_AUDIT_KEY)
        audit_reader = AdminAuditReader(reopened, "audit_table", verify_key=ADMIN_BROWSER_AUDIT_KEY)
        assert all(audit_reader.read(event)["integrity_status"] == "signed_verified" for event in audits)
        assert tenant_rows(reopened) == before
        control = state.read_control()
        if writer:
            intent, prepared = state.history(receipt["request_id"])
            assert control["state"] == "REQUESTED" and prepared is None
            assert document_hash(intent) == receipt["intent_sha256"]
            assert document_hash(drafts.read_review(mounted.OWNER, review["review_id"])) == intent["review_sha256"]
            assert document_hash(drafts.read(mounted.OWNER, draft["draft_id"])) == intent["draft_sha256"]
        else:
            assert control is None
    finally:
        reopened.close()
    fresh = accepted(client.get("/v1/admin/catalog", headers=headers))
    strip_audit = lambda value: {key: item for key, item in value.items() if key != "read_audit_event_id"}
    assert strip_audit(fresh) == strip_audit(catalog)
    print(json.dumps({"phase": mounted.phase, "denied": denied,
        "signed_read_receipts": len(audits) - 1, "intent_documents": int(writer),
        "transactions": mounted.db.transactions, "tenants_unchanged": True,
        "catalog_unchanged": True, "remote_published": False, "ci_verified": False, "deployed": False}))
    mounted.db.close()


if __name__ == "__main__":
    main()
