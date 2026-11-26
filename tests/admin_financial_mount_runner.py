"""Fresh-process financial acceptance through the authenticated mounted app.

Only the existing fixture's external identity, database and Stripe boundaries
are synthetic. Source gates, routes, signed JWT verifier and services are real.
"""
from copy import deepcopy
import json
import sys

from fastapi.testclient import TestClient

import admin_browser_app as mounted
from admin_cancellation_support import verified_financial_rows
from admin_test_support import AdminTestDynamo, ADMIN_BROWSER_AUDIT_KEY
from guardoath.admin_audit import AdminAuditReader
from guardoath.dynamodb_store import DynamoDBTenantStore


TENANT = "usr-admin-alpha"
PREFIX = "/v1/admin/subscriptions/" + TENANT


def tenant_rows(database):
    return {name: database.get_item(TableName="tenant_table",
        Key={"tenant_id": {"S": name}}, ConsistentRead=True)["Item"]
        for name in (TENANT, "usr-admin-beta")}


def main(scenario):
    assert scenario in ("compatibility", "verified", "indeterminate")
    writer = mounted.phase == "source"
    assert writer is (scenario != "compatibility")
    before = tenant_rows(mounted.db)
    client = TestClient(mounted.app, base_url=mounted.ORIGIN)
    exchanged = client.post("/v1/admin/auth/exchange",
        json={"code": "owner", "code_verifier": "v" * 43})
    assert exchanged.status_code == 200, exchanged.text
    headers = {"X-GuardOath-Admin-Session": exchanged.json()["access_token"],
               "Origin": mounted.ORIGIN}
    read_receipts = []
    mutation_receipts = []

    def accepted(response, *, protected_read=True):
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        value = response.json()
        if protected_read:
            read_receipts.append(value["read_audit_event_id"])
        return value

    features = accepted(client.get("/v1/admin/features", headers=headers))
    assert features["billing_reconcile"] is writer
    assert features["billing_cancellation_schedule"] is writer
    assert features["billing_cancellation_preview"] is True
    assert features["billing_cancellation_receipts"] is True
    assert features["billing_configuration"] == {
        "signed_audit": True, "stripe_read": True, "plan_allowance": True,
        "cancellation_provider": True, "refund_provider": True,
        "subscription_writer_enabled": writer, "cancellation_writer_enabled": writer,
        "refund_writer_enabled": False}
    body = {"expected_revision": 7, "confirmation": "RECONCILE " + TENANT,
        "reason": "Reconcile the mounted synthetic subscription",
        "idempotency_key": "mounted-financial-reconcile-001"}
    cancellation_body = {"expected_revision": 7, "preview_reference": "sc1." + "a" * 80,
        "confirmation": "SCHEDULE CANCELLATION " + TENANT,
        "reason": "Schedule the reviewed synthetic subscription",
        "idempotency_key": "mounted-financial-cancel-001"}
    operations = [(PREFIX + "/cancellation/preview", {"expected_revision": 7}),
        (PREFIX + "/cancellation/receipt", {"idempotency_key": cancellation_body["idempotency_key"]})]
    if writer:
        operations.extend([(PREFIX + "/reconcile", body),
            (PREFIX + "/cancel-at-period-end", cancellation_body)])

    def provider_counts():
        return (len(mounted.subscription_reads), len(mounted.cancellation_boundary.calls),
            mounted.db.transactions)

    denied = {}
    for code in ("customer", "no-mfa", "stale-mfa", "forged"):
        counts, audit_count = provider_counts(), len(mounted.db.audit_items())
        login = client.post("/v1/admin/auth/exchange", json={"code": code, "code_verifier": "v" * 43})
        assert login.status_code in (401, 403)
        rejected = {**headers, "X-GuardOath-Admin-Session": mounted.signed_token(code)}
        statuses = [client.post(url, headers=rejected, json=payload).status_code
                    for url, payload in operations]
        assert all(status in (401, 403) for status in statuses), (code, statuses)
        assert provider_counts() == counts and len(mounted.db.audit_items()) == audit_count
        denied[code] = statuses
    for url, payload in operations:
        counts, audit_count = provider_counts(), len(mounted.db.audit_items())
        for origin in (None, "https://untrusted.example.invalid"):
            rejected = {key: value for key, value in headers.items() if key != "Origin"}
            if origin is not None:
                rejected["Origin"] = origin
            assert client.post(url, headers=rejected, json=payload).status_code == 403
        assert provider_counts() == counts and len(mounted.db.audit_items()) == audit_count
    mounted.db.fail_audit = True
    counts = provider_counts()
    for url, payload in operations:
        assert client.post(url, headers=headers, json=payload).status_code == 503
        assert provider_counts() == counts
    mounted.db.fail_audit = False

    if not writer:
        reviewed = accepted(client.post(PREFIX + "/cancellation/preview", headers=headers,
            json={"expected_revision": 7}))["cancellation"]
        assert reviewed["scheduling_available"] is False
        assert client.post(PREFIX + "/reconcile", headers=headers, json=body).status_code == 404
        assert client.post(PREFIX + "/cancel-at-period-end", headers=headers,
            json={**cancellation_body, "preview_reference": reviewed["preview_reference"]}).status_code == 404
        assert tenant_rows(mounted.db) == before
        assert not verified_financial_rows(mounted.db)
        assert not mounted.cancellation_boundary.posts
        assert mounted.db.transactions == 0
        result = {"status": "disabled", "provider_posts": 0, "tenant_preserved": True}
    else:
        # Exact confirmation and server-owned customer/price binding remain
        # mandatory on the source-default routes, before any financial write.
        counts = provider_counts()
        assert client.post(PREFIX + "/reconcile", headers=headers,
            json={**body, "confirmation": "RECONCILE usr-other"}).status_code == 422
        assert client.post(PREFIX + "/reconcile", headers=headers,
            json={**body, "monthly_allowance": 1000000}).status_code == 422
        assert provider_counts() == counts and tenant_rows(mounted.db) == before
        original_provider = deepcopy(mounted.cancellation_boundary.value)
        for wrong in ({"customer": "cus_other"}, {"id": "sub_other"}, {"livemode": True}):
            mounted.cancellation_boundary.value = {**deepcopy(original_provider), **wrong}
            assert client.post(PREFIX + "/reconcile", headers=headers, json=body).status_code == 503
            assert tenant_rows(mounted.db) == before and mounted.db.transactions == 0
        mounted.cancellation_boundary.value = original_provider

        # A committed response can be lost. The exact original request recovers
        # its signed receipt without another provider observation or transaction.
        mounted.db.lose_transaction_response = True
        lost = client.post(PREFIX + "/reconcile", headers=headers, json=body)
        assert lost.status_code == 503 and mounted.db.transactions == 1
        mounted.db.lose_transaction_response = False
        counts = provider_counts()
        reconciled = accepted(client.post(PREFIX + "/reconcile", headers=headers, json=body))
        assert reconciled["replayed"] is True and reconciled["revision"] == 8
        assert reconciled["monthly_allowance"] == reconciled["plan_monthly_allowance"] == 10000
        assert reconciled["subscription_status"] == "active"
        assert provider_counts() == counts
        mutation_receipts.append(reconciled["audit_event_id"])
        assert client.post(PREFIX + "/reconcile", headers=headers,
            json={**body, "reason": "A changed request cannot reuse the reference"}).status_code == 409
        assert provider_counts() == counts
        activated = mounted.store.get(TENANT)
        assert activated.stripe_subscription_id == "sub_alpha"
        assert activated.stripe_plan_monthly_allowance == 10000
        assert activated.admin_monthly_allowance_cap is None

        cap = accepted(client.post("/v1/admin/tenants/" + TENANT + "/allowance-cap", headers=headers,
            json={"expected_revision": 8, "new_limit": 4000, "confirmation": TENANT,
                "reason": "Preserve the approved synthetic request budget",
                "idempotency_key": "mounted-financial-cap-001"}), protected_read=False)
        mutation_receipts.append(cap["audit_event_id"])
        capped = accepted(client.post(PREFIX + "/reconcile", headers=headers,
            json={**body, "expected_revision": 9, "idempotency_key": "mounted-financial-reconcile-002"}))
        mutation_receipts.append(capped["audit_event_id"])
        assert capped["monthly_allowance"] == capped["admin_monthly_allowance_cap"] == 4000
        assert capped["revision"] == 10
        saved = tenant_rows(mounted.db)
        for field in ("key_hashes", "input_rule_actions", "output_rule_actions",
                      "input_attachment_csv_mode", "status", "payer_id"):
            assert json.loads(saved[TENANT]["record"]["S"])[field] == json.loads(before[TENANT]["record"]["S"])[field]
        assert saved[TENANT]["usage"] == before[TENANT]["usage"]
        assert saved["usr-admin-beta"] == before["usr-admin-beta"]

        # The provider's own account and exact saved target are independently
        # checked during review. None of these bad bindings can create an intent.
        for change in ("account", "customer", "price"):
            if change == "account":
                mounted.cancellation_boundary.account["id"] = "acct_other"
            elif change == "customer":
                mounted.cancellation_boundary.value["customer"] = "cus_other"
            else:
                mounted.cancellation_boundary.value["items"]["data"][0]["price"]["id"] = "price_other"
            assert client.post(PREFIX + "/cancellation/preview", headers=headers,
                json={"expected_revision": 10}).status_code == 503
            assert not verified_financial_rows(mounted.db) and not mounted.cancellation_boundary.posts
            mounted.cancellation_boundary.account["id"] = "acct_owner"
            mounted.cancellation_boundary.value = deepcopy(original_provider)

        reviewed = accepted(client.post(PREFIX + "/cancellation/preview", headers=headers,
            json={"expected_revision": 10}))["cancellation"]
        assert reviewed["scheduling_available"] is True and reviewed["tenant_mutated"] is False
        cancellation_body.update(expected_revision=10, preview_reference=reviewed["preview_reference"])
        counts = provider_counts()
        assert client.post(PREFIX + "/cancel-at-period-end", headers=headers,
            json={**cancellation_body, "confirmation": "SCHEDULE CANCELLATION usr-other"}).status_code == 422
        assert client.post(PREFIX + "/cancel-at-period-end", headers=headers,
            json={**cancellation_body, "subscription_id": "sub_other"}).status_code == 422
        assert provider_counts() == counts and not verified_financial_rows(mounted.db)
        mounted.cancellation_boundary.lose_post_response = scenario == "indeterminate"
        scheduled = accepted(client.post(PREFIX + "/cancel-at-period-end", headers=headers,
            json=cancellation_body))["cancellation"]
        assert scheduled["status"] == scenario and scheduled["integrity_status"] == "signed_verified"
        assert scheduled["tenant_mutated"] is scheduled["provider_state_refreshed"] is False
        assert len(mounted.cancellation_boundary.posts) == 1
        counts = provider_counts()
        replay = accepted(client.post(PREFIX + "/cancel-at-period-end", headers=headers,
            json=cancellation_body))["cancellation"]
        recovered = accepted(client.post(PREFIX + "/cancellation/receipt", headers=headers,
            json={"idempotency_key": cancellation_body["idempotency_key"]}))["cancellation"]
        assert scheduled == replay == recovered
        assert provider_counts() == counts and tenant_rows(mounted.db) == saved
        assert client.post(PREFIX + "/cancel-at-period-end", headers=headers,
            json={**cancellation_body, "reason": "Different reason cannot reuse confirmed request"}).status_code == 409
        assert provider_counts() == counts
        if scenario == "indeterminate":
            assert scheduled["outcome"] is not None and scheduled["outcome"]["status"] == "indeterminate"
            assert client.post(PREFIX + "/cancel-at-period-end", headers=headers,
                json={**cancellation_body, "idempotency_key": "mounted-conflicting-action-001"}).status_code == 409
            assert len(mounted.cancellation_boundary.posts) == 1
        result = {"status": scenario, "provider_posts": 1, "tenant_preserved": True,
            "reconciliation_recovered": True, "saved_cap": 4000}

    # Reopen disk-backed state independently from the API process's service
    # objects; verify every successful read and mutation receipt's actual MAC.
    reopened = AdminTestDynamo(mounted.path)
    try:
        reader = AdminAuditReader(reopened, "audit_table", verify_key=ADMIN_BROWSER_AUDIT_KEY)
        for event_id in read_receipts + mutation_receipts:
            assert reader.read(event_id)["integrity_status"] == "signed_verified"
        financial = verified_financial_rows(reopened)
        assert len(financial) == (3 if writer else 0)
        if writer:
            assert DynamoDBTenantStore("tenant_table", reopened).get(TENANT).admin_monthly_allowance_cap == 4000
            control = next(row for row in financial if row["kind"] == "control")
            assert control["state"] == ("VERIFIED" if scenario == "verified" else "BLOCKED")
    finally:
        reopened.close()
    print(json.dumps({**result, "phase": mounted.phase, "denied": denied,
        "read_receipts": len(read_receipts), "mutation_receipts": len(mutation_receipts),
        "financial_documents": len(financial)}))
    mounted.db.close()


if __name__ == "__main__":
    main(sys.argv[1])
