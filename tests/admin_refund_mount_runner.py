"""Fresh-process invoice refund acceptance through the real mounted owner app."""
import json
import sys

from fastapi.testclient import TestClient

import admin_browser_app as mounted
from admin_refund_support import verified_refund_rows
from admin_test_support import AdminTestDynamo, ADMIN_BROWSER_AUDIT_KEY
from guardoath.admin_audit import AdminAuditReader
from guardoath.admin_invoice_refund import AdminInvoiceRefund
from guardoath.admin_refund_state import DynamoRefundState
from guardoath import state_contracts

TENANT = "usr-admin-alpha"
PREFIX = "/v1/admin/subscriptions/" + TENANT


def tenant_rows(database):
    return {name:database.get_item(TableName="tenant_table", Key={"tenant_id":{"S":name}},
        ConsistentRead=True)["Item"] for name in (TENANT, "usr-admin-beta")}


def main(scenario):
    assert scenario in ("source", "disabled", "pending", "indeterminate")
    writer = scenario in ("pending", "indeterminate")
    assert state_contracts.new_refund_state_enabled() is writer
    assert mounted.refund_phase == ("enabled" if writer else scenario)
    client = TestClient(mounted.app, base_url=mounted.ORIGIN)
    login = client.post("/v1/admin/auth/exchange", json={"code":"owner", "code_verifier":"v" * 43})
    assert login.status_code == 200, login.text
    headers = {"X-GuardOath-Admin-Session":login.json()["access_token"], "Origin":mounted.ORIGIN}
    read_receipts = []

    def accepted(response):
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        result = response.json()
        read_receipts.append(result["read_audit_event_id"])
        return result

    flags = accepted(client.get("/v1/admin/features", headers=headers))
    assert flags["billing_refund_preview"] and flags["billing_refund_receipts"]
    assert flags["billing_refund_submit"] is writer and flags["billing_refund_refresh"] is writer
    assert flags["billing_configuration"]["refund_provider"] is True
    assert flags["billing_configuration"]["refund_writer_enabled"] is writer
    reconciled = client.post(PREFIX + "/reconcile", headers=headers, json={"expected_revision":7,
        "confirmation":"RECONCILE " + TENANT, "reason":"Establish the mounted synthetic subscription",
        "idempotency_key":"refund-mount-reconcile-123456789"})
    assert reconciled.status_code == 200, reconciled.text
    before = tenant_rows(mounted.db)
    revision = int(before[TENANT]["revision"]["N"])
    history = accepted(client.get(PREFIX + "/invoices", headers=headers))["history"]
    assert history["expected_revision"] == revision
    invoice = history["invoices"][0]
    assert invoice["invoice_reference"].startswith("ir1.") and invoice["amount_paid_minor"] == 700
    preview_body = {"expected_revision":revision, "invoice_reference":invoice["invoice_reference"]}
    write_body = {"expected_revision":revision, "preview_reference":"rf1." + "a" * 80,
        "confirmation":"REFUND " + TENANT + " 700 USD MINOR UNITS",
        "reason":"Refund the selected synthetic invoice", "idempotency_key":"mounted-refund-action-123456789"}
    reference = {"idempotency_key":write_body["idempotency_key"]}
    operations = [("/refund/preview", preview_body), ("/refund/receipt", reference)]
    if writer:
        operations.extend([("/refund", write_body), ("/refund/refresh", reference)])
    denied = {}
    for code in ("customer", "no-mfa", "stale-mfa", "forged"):
        counts = (len(mounted.refund_boundary.calls), len(mounted.db.audit_items()), mounted.db.transactions)
        login = client.post("/v1/admin/auth/exchange", json={"code":code, "code_verifier":"v" * 43})
        assert login.status_code in (401, 403)
        rejected = {**headers, "X-GuardOath-Admin-Session":mounted.signed_token(code)}
        statuses = [client.post(PREFIX + suffix, headers=rejected, json=body).status_code
            for suffix, body in operations]
        assert all(status in (401, 403) for status in statuses)
        assert counts == (len(mounted.refund_boundary.calls), len(mounted.db.audit_items()), mounted.db.transactions)
        denied[code] = statuses
    for suffix, body in operations:
        for origin in (None, "https://untrusted.example.invalid"):
            rejected = {key:value for key,value in headers.items() if key != "Origin"}
            if origin is not None:
                rejected["Origin"] = origin
            assert client.post(PREFIX + suffix, headers=rejected, json=body).status_code == 403
    mounted.db.fail_audit = True
    count = len(mounted.refund_boundary.calls)
    for suffix, body in operations:
        assert client.post(PREFIX + suffix, headers=headers, json=body).status_code == 503
    assert len(mounted.refund_boundary.calls) == count
    mounted.db.fail_audit = False
    preview = accepted(client.post(PREFIX + "/refund/preview", headers=headers, json=preview_body))["refund"]
    assert preview["amount_minor"] == 700 and preview["currency"] == "usd"
    assert preview["currency_exponent"] == 2 and preview["livemode"] is False
    assert preview["refund_available"] is writer
    write_body.update(preview_reference=preview["preview_reference"], confirmation=preview["confirmation"])
    final_status = "disabled"
    if not writer:
        for suffix, body in (("/refund", write_body), ("/refund/refresh", reference)):
            assert client.post(PREFIX + suffix, headers=headers, json=body).status_code == 404
        assert accepted(client.post(PREFIX + "/refund/receipt", headers=headers, json=reference))["refund"]["status"] == "not_found"
        assert not verified_refund_rows(mounted.db) and not mounted.refund_boundary.posts
    else:
        if scenario == "pending":
            mounted.refund_boundary.status = "pending"
        else:
            mounted.refund_boundary.lose_post_response = True
        result = accepted(client.post(PREFIX + "/refund", headers=headers, json=write_body))["refund"]
        assert result["status"] == scenario
        assert len(mounted.refund_boundary.posts) == 1
        count = len(mounted.refund_boundary.calls)
        historical = accepted(client.post(PREFIX + "/refund/receipt", headers=headers, json=reference))["refund"]
        assert historical == result and len(mounted.refund_boundary.calls) == count
        mounted.refund_boundary.status = "succeeded"
        mounted.refund_boundary.lose_post_response = False
        result = accepted(client.post(PREFIX + "/refund/refresh", headers=headers, json=reference))["refund"]
        assert result["status"] == "verified" and result["provider_state_refreshed"] is True
        assert all(call.get_method() == "GET" for call in mounted.refund_boundary.calls[count:])
        result["provider_state_refreshed"] = False
        count = len(mounted.refund_boundary.calls)
        replay = accepted(client.post(PREFIX + "/refund", headers=headers, json=write_body))["refund"]
        assert replay == result and len(mounted.refund_boundary.calls) == count
        assert client.post(PREFIX + "/refund", headers=headers, json={**write_body,
            "idempotency_key":"another-mounted-refund-123456789"}).status_code == 409
        assert len(mounted.refund_boundary.posts) == 1
        independent = AdminTestDynamo(mounted.path)
        try:
            # Signed recovery is independently reopened with no provider/codec.
            read_only = AdminInvoiceRefund(state=DynamoRefundState(independent, "audit_table", ADMIN_BROWSER_AUDIT_KEY),
                tenant_table="tenant_table", preview_key=mounted.CURSOR_KEY)
            reopened = read_only.receipt(actor=mounted.OWNER, tenant_id=TENANT, **reference)
            assert reopened == result
            rows = verified_refund_rows(independent)
            assert len(rows) == 4 and {row["kind"] for row in rows} == {"intent", "control", "outcome", "observation"}
        finally:
            independent.close()
        final_status = "verified"
    assert tenant_rows(mounted.db) == before
    reader = AdminAuditReader(mounted.db, "audit_table", verify_key=ADMIN_BROWSER_AUDIT_KEY)
    assert all(reader.read(event)["integrity_status"] == "signed_verified" for event in read_receipts)
    print(json.dumps({"scenario":scenario, "writer":writer, "status":final_status, "denied":denied,
        "provider_posts":len(mounted.refund_boundary.posts), "tenant_preserved":True,
        "signed_refund_documents":len(verified_refund_rows(mounted.db)), "signed_read_receipts":len(read_receipts)}))


if __name__ == "__main__":
    main(sys.argv[1])
