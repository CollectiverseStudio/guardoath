"""Fresh real owner app for reader-first cap restoration and future writes."""
import json
import sys

from fastapi.testclient import TestClient

import admin_browser_app as mounted
from admin_test_support import AdminTestDynamo, ADMIN_BROWSER_AUDIT_KEY
from guardoath.admin_audit import AdminAuditReader
from guardoath.admin_cap_restore import AdminRequestCapRestoration
from guardoath.admin_cap_restore_state import DynamoCapRestoreState
from guardoath import state_contracts

TENANT = "usr-admin-alpha"
TENANT_PATH = "/v1/admin/tenants/" + TENANT
PREFIX = TENANT_PATH + "/allowance-cap/restore"
CONTRACT = "subscription-cap-restoration-v1"


def tenant_rows(database):
    return {name:database.get_item(TableName="tenant_table", Key={"tenant_id":{"S":name}},
        ConsistentRead=True)["Item"] for name in (TENANT, "usr-admin-beta")}


def main(cap_restore_phase, scenario):
    assert (cap_restore_phase, scenario) in (
        ("source", "reader"), ("disabled", "reader"),
        ("enabled", "applied"), ("enabled", "indeterminate"))
    writer = cap_restore_phase == "enabled"
    assert mounted.phase == "source" and mounted.cap_restore_phase == cap_restore_phase
    assert state_contracts.new_cap_restoration_state_enabled() is writer
    client = TestClient(mounted.app, base_url=mounted.ORIGIN)
    login = client.post("/v1/admin/auth/exchange", json={"code":"owner", "code_verifier":"v" * 43})
    assert login.status_code == 200, login.text
    headers = {"X-GuardOath-Admin-Session":login.json()["access_token"], "Origin":mounted.ORIGIN}
    read_receipts, mutation_receipts = [], []

    def accepted(response, *, protected_read=True):
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        result = response.json()
        if protected_read:
            read_receipts.append(result["read_audit_event_id"])
        assert all(private not in response.text for private in ("cus_alpha", "sub_alpha", "price_starter"))
        return result

    flags = accepted(client.get("/v1/admin/features", headers=headers))
    assert flags["billing_cap_restore_preview"] and flags["billing_cap_restore_receipts"]
    assert flags["billing_cap_restore_submit"] is writer
    assert flags["billing_configuration"]["cap_restore_writer_enabled"] is writer
    reconciled = accepted(client.post("/v1/admin/subscriptions/" + TENANT + "/reconcile",
        headers=headers, json={"expected_revision":7, "confirmation":"RECONCILE " + TENANT,
            "reason":"Establish the mounted synthetic subscription",
            "idempotency_key":"cap-restore-mount-reconcile-123456789"}))
    mutation_receipts.append(reconciled["audit_event_id"])
    capped = accepted(client.post(TENANT_PATH + "/allowance-cap", headers=headers,
        json={"expected_revision":8, "new_limit":4000, "confirmation":TENANT,
            "reason":"Keep the saved local synthetic request cap",
            "idempotency_key":"cap-restore-mount-cap-123456789"}), protected_read=False)
    mutation_receipts.append(capped["audit_event_id"])
    suspended = accepted(client.post(TENANT_PATH + "/status", headers=headers,
        json={"expected_revision":9, "status":"disabled", "confirmation":TENANT,
            "reason":"Preserve suspended access during cap restoration",
            "idempotency_key":"cap-restore-mount-suspend-123456789"}), protected_read=False)
    mutation_receipts.append(suspended["audit_event_id"])
    before = tenant_rows(mounted.db)
    revision = int(before[TENANT]["revision"]["N"])
    assert revision == 10
    preview_body = {"expected_revision":revision}
    write_body = {"expected_revision":revision, "preview_reference":"cr1." + "a" * 80,
        "confirmation":"RESTORE " + TENANT + " TO 10000 REQUESTS PER MONTH",
        "reason":"Restore the reviewed saved local request cap",
        "idempotency_key":"mounted-cap-restoration-123456789"}
    reference = {"idempotency_key":write_body["idempotency_key"]}
    operations = [("/preview", preview_body), ("/receipt", reference)]
    if writer:
        operations.append(("", write_body))
    denied = {}
    for code in ("customer", "no-mfa", "stale-mfa", "forged"):
        counts = (len(mounted.cap_restoration_reads), len(mounted.db.audit_items()), mounted.db.transactions)
        login = client.post("/v1/admin/auth/exchange", json={"code":code, "code_verifier":"v" * 43})
        assert login.status_code in (401, 403)
        rejected = {**headers, "X-GuardOath-Admin-Session":mounted.signed_token(code)}
        statuses = [client.post(PREFIX + suffix, headers=rejected, json=body).status_code
            for suffix, body in operations]
        assert all(status in (401, 403) for status in statuses), (code, statuses)
        assert counts == (len(mounted.cap_restoration_reads), len(mounted.db.audit_items()), mounted.db.transactions)
        denied[code] = statuses
    for suffix, body in operations:
        for origin in (None, "https://untrusted.example.invalid"):
            rejected = {key:value for key,value in headers.items() if key != "Origin"}
            if origin is not None:
                rejected["Origin"] = origin
            assert client.post(PREFIX + suffix, headers=rejected, json=body).status_code == 403
    mounted.db.fail_audit = True
    counts = (len(mounted.cap_restoration_reads), mounted.db.transactions)
    for suffix, body in operations:
        assert client.post(PREFIX + suffix, headers=headers, json=body).status_code == 503
    assert counts == (len(mounted.cap_restoration_reads), mounted.db.transactions)
    mounted.db.fail_audit = False
    preview = accepted(client.post(PREFIX + "/preview", headers=headers, json=preview_body))["restoration"]
    assert preview["status"] == "reviewed" and preview["operation"] == CONTRACT
    assert preview["allowance_before"] == preview["operator_cap_before"] == 4000
    assert preview["allowance_after"] == preview["plan_allowance"] == 10000
    assert preview["tenant_status"] == "disabled" and preview["operator_cap_after"] is None
    assert preview["livemode"] is False and preview["restore_available"] is writer
    assert preview["tenant_mutated"] is False and tenant_rows(mounted.db) == before
    write_body.update(preview_reference=preview["preview_reference"], confirmation=preview["confirmation"])
    transactions_before = mounted.db.transactions
    restores = []
    later_cap_preserved = False
    final_status = "reader"
    if not writer:
        assert client.post(PREFIX, headers=headers, json=write_body).status_code == 404
        missing = accepted(client.post(PREFIX + "/receipt", headers=headers, json=reference))["restoration"]
        assert missing["status"] == "not_found" and missing["tenant_mutated"] is False
        assert mounted.db.transactions == transactions_before and tenant_rows(mounted.db) == before
    else:
        if scenario == "indeterminate":
            # A real committed transaction loses its response while signed
            # history is temporarily unreadable. The API must not report failure
            # as non-application or issue a replacement transaction.
            original_get = mounted.db.get_item
            def lose_history_after_commit(**kwargs):
                if kwargs["TableName"] == "audit_table" and mounted.db.transactions > transactions_before:
                    raise TimeoutError("Synthetic committed history read outage")
                return original_get(**kwargs)
            mounted.db.get_item = lose_history_after_commit
            mounted.db.lose_transaction_response = True
            lost = client.post(PREFIX, headers=headers, json=write_body)
            assert lost.status_code == 503 and mounted.db.transactions == transactions_before + 1
            assert "Synthetic" not in lost.text
            assert client.post(PREFIX + "/receipt", headers=headers, json=reference).status_code == 503
            mounted.db.get_item = original_get
            mounted.db.lose_transaction_response = False
            result = accepted(client.post(PREFIX + "/receipt", headers=headers, json=reference))["restoration"]
        else:
            result = accepted(client.post(PREFIX, headers=headers, json=write_body))["restoration"]
        assert result["status"] == "applied" and result["integrity_status"] == "signed_verified"
        assert result["prior_revision"] == 10 and result["revision"] == 11
        assert result["tenant_status"] == "disabled" and result["overage"] == "block"
        mutation_receipts.append(result["audit_event_id"])
        after = tenant_rows(mounted.db)
        original = json.loads(before[TENANT]["record"]["S"])
        restored = json.loads(after[TENANT]["record"]["S"])
        assert restored["monthly_allowance"] == 10000 and "admin_monthly_allowance_cap" not in restored
        changed = {"monthly_allowance", "admin_monthly_allowance_cap", "updated_at"}
        assert {key:value for key,value in restored.items() if key not in changed} == {
            key:value for key,value in original.items() if key not in changed}
        assert after[TENANT]["usage"] == before[TENANT]["usage"] and after["usr-admin-beta"] == before["usr-admin-beta"]
        assert len(mounted.cap_restoration_reads) == 2 and mounted.db.transactions == transactions_before + 1
        count = len(mounted.cap_restoration_reads)
        historical = accepted(client.post(PREFIX + "/receipt", headers=headers, json=reference))["restoration"]
        assert historical == {**result, "replayed":True}
        later_cap = accepted(client.post(TENANT_PATH + "/allowance-cap", headers=headers,
            json={"expected_revision":11, "new_limit":2000, "confirmation":TENANT,
                "reason":"Retain a later stricter local request cap",
                "idempotency_key":"cap-restore-mount-later-cap-123456789"}), protected_read=False)
        mutation_receipts.append(later_cap["audit_event_id"])
        later = tenant_rows(mounted.db)
        transactions = mounted.db.transactions
        for suffix, body in (("", write_body), ("/receipt", reference)):
            replay = accepted(client.post(PREFIX + suffix, headers=headers, json=body))["restoration"]
            assert replay == historical
        assert tenant_rows(mounted.db) == later and mounted.db.transactions == transactions
        assert json.loads(later[TENANT]["record"]["S"])["monthly_allowance"] == 2000
        assert len(mounted.cap_restoration_reads) == count
        later_cap_preserved = True
        independent = AdminTestDynamo(mounted.path)
        try:
            # Reopen the durable receipt with no provider/plan, disabled source,
            # no clock, and no ability to read the current tenant.
            state_contracts.ENABLE_NEW_CAP_RESTORATION_STATE = False
            old_get = independent.get_item
            def history_only(**kwargs):
                assert kwargs["TableName"] == "audit_table"
                return old_get(**kwargs)
            independent.get_item = history_only
            read_only = AdminRequestCapRestoration(state=DynamoCapRestoreState(
                independent, "audit_table", ADMIN_BROWSER_AUDIT_KEY), tenant_table="tenant_table",
                preview_key=mounted.CURSOR_KEY, clock=lambda: (_ for _ in ()).throw(AssertionError("No clock read")))
            assert read_only.receipt(actor=mounted.OWNER, tenant_id=TENANT, **reference) == historical
            assert read_only.apply(actor=mounted.OWNER, tenant_id=TENANT, **write_body) == historical
        finally:
            independent.close()
        restores = [row for row in mounted.db.audit_items() if row.get("operation", {}).get("S") == "tenant.allowance_restore"]
        assert len(restores) == 1
        final_status = "applied"
    reader = AdminAuditReader(mounted.db, "audit_table", verify_key=ADMIN_BROWSER_AUDIT_KEY)
    assert all(reader.read(event)["integrity_status"] == "signed_verified" for event in read_receipts + mutation_receipts)
    if restores:
        receipt = reader.read(restores[0]["event_id"]["S"])
        assert receipt["change"]["before"] == 4000 and receipt["change"]["after"] == 10000
        assert receipt["change"]["cap_removed"] is True and receipt["change"]["tenant_status"] == "disabled"
    assert not mounted.refund_boundary.posts and not mounted.cancellation_boundary.posts
    print(json.dumps({"phase":mounted.phase, "cap_restore_phase":cap_restore_phase,
        "scenario":scenario, "writer":writer, "status":final_status, "denied":denied,
        "provider_reads":len(mounted.cap_restoration_reads), "provider_posts":0,
        "restore_events":len(restores), "later_cap_preserved":later_cap_preserved,
        "signed_read_receipts":len(read_receipts), "preserved_suspension_keys_rules_usage":True}))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
