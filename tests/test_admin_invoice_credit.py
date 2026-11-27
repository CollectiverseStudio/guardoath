from dataclasses import dataclass, replace
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from admin_credit_support import CreditBoundary
from admin_test_support import AdminTestDynamo
from guardoath.admin_audit import AdminSensitiveReadAuditor
from guardoath.admin_console import VerifiedAdmin, create_admin_router
from guardoath.admin_invoice_credit import AdminInvoiceCredit, StripeInvoiceCredits
from test_admin_invoices import Tenant, history, invoice

BASE = "/v1/admin/subscriptions/usr-invoices/invoice-credit"
HEADERS = {"X-GuardOath-Admin-Session": "owner", "Origin": "https://guardoath.com"}


@dataclass
class Record(Tenant):
    revision: int = 7


def setup(tmp_path, writer=True):
    record = Record()

    class Store:
        def get(self, tenant):
            return replace(record) if tenant == record.tenant_id else None

    boundary = CreditBoundary()
    h = history(
        fetch=lambda *args, **kw: {
            "object": "list",
            "has_more": False,
            "data": [
                invoice(
                    **{k: v for k, v in boundary.invoice.items() if k != "id"},
                    amount_due=2900,
                    amount_paid=0,
                )
            ],
        }
    )
    client = StripeInvoiceCredits(
        "sk_test_" + "r" * 15, "rk_test_" + "w" * 15 if writer else "", boundary.account, opener=boundary
    )
    key = b"k" * 32
    db = AdminTestDynamo(tmp_path / "credits.sqlite3")
    api = FastAPI()
    api.include_router(
        create_admin_router(
            tenant_store=Store(),
            billing_invoices=h,
            read_auditor=AdminSensitiveReadAuditor(db, "audit_table", signing_key=key),
            verify_cognito_admin=lambda token: VerifiedAdmin(
                "test-owner-subject" if token == "owner" else "other-owner-subject",
                frozenset({"owner" if token in ("owner", "other") else "customer"}),
                token != "no-mfa",
            ),
            billing_credit=AdminInvoiceCredit(client, h, key),
        )
    )
    browser = TestClient(api)
    rows = browser.get(BASE.replace("invoice-credit", "invoices"), headers=HEADERS).json()["history"][
        "invoices"
    ]
    return browser, boundary, record, rows[0]["credit_reference"]


def review(browser, selection, amount=1000):
    return browser.post(
        BASE + "/preview",
        headers=HEADERS,
        json={"amount_minor": amount, "reason": "order_change", "selection": selection},
    )


@pytest.mark.parametrize("amount,remaining", [(1000, 1900), (2900, 0)])
def test_review_issue_and_recover_without_second_write(tmp_path, amount, remaining):
    browser, b, record, selection = setup(tmp_path)
    p = review(browser, selection, amount).json()["credit"]
    assert p["before_minor"] == 2900 and p["after_minor"] == remaining and not b.posts
    body = {"reference": p["reference"], "confirmation": p["confirmation"]}
    r = browser.post(BASE, headers=HEADERS, json=body)
    assert r.status_code == 200 and r.json()["credit"]["status"] == "issued"
    assert b.invoice["amount_remaining"] == remaining and record == Record()
    assert browser.post(BASE, headers=HEADERS, json=body).status_code == 409
    h = browser.post(BASE + "/receipt", headers=HEADERS, json={"reference": p["reference"]})
    assert h.json()["credit"] == r.json()["credit"] and len(b.posts) == 1
    assert (
        browser.post(
            BASE + "/receipt",
            headers={**HEADERS, "X-GuardOath-Admin-Session": "other"},
            json={"reference": p["reference"]},
        ).status_code
        == 409
    )


def test_unknown_response_read_only_recovery(tmp_path):
    browser, b, _, s = setup(tmp_path)
    p = review(browser, s).json()["credit"]
    b.lose_response = True
    assert (
        browser.post(
            BASE, headers=HEADERS, json={k: p[k] for k in ("reference", "confirmation")}
        ).status_code
        == 503
    )
    assert (
        browser.post(BASE + "/receipt", headers=HEADERS, json={"reference": p["reference"]}).json()[
            "credit"
        ]["number"]
        == "TEST-CN-1"
    )
    assert len(b.posts) == 1


@pytest.mark.parametrize(
    "changed",
    [
        "binding",
        "paid",
        "balance",
        "revision",
        "account",
        "confirmation",
        "expired",
        "tenant",
        "postpayment",
    ],
)
def test_changed_review_or_invalid_evidence_fails_closed(tmp_path, changed, monkeypatch):
    browser, b, record, s = setup(tmp_path)
    p = review(browser, s).json()["credit"]
    if changed == "binding":
        b.invoice["subscription"] = "sub_other"
    if changed == "paid":
        b.invoice["status"] = "paid"
    if changed == "balance":
        b.invoice["amount_remaining"] = 2100
    if changed == "revision":
        record.revision += 1
    if changed == "account":
        b.account = "acct_other"
    if changed == "confirmation":
        p["confirmation"] = "CREDIT 1 USD MINOR UNITS"
    if changed == "expired":
        import time

        future = time.time() + 181
        monkeypatch.setattr(time, "time", lambda: future)
    if changed == "postpayment":
        from guardoath.admin_invoice_credit import StripeInvoiceCredits

        fn = StripeInvoiceCredits.issue
        monkeypatch.setattr(
            StripeInvoiceCredits,
            "issue",
            lambda self, review: {**fn(self, review), "type": "post_payment"},
        )
    url = BASE.replace("usr-invoices", "usr-other") if changed == "tenant" else BASE
    assert browser.post(
        url, headers=HEADERS, json={k: p[k] for k in ("reference", "confirmation")}
    ).status_code in (404, 409, 503)
    assert len(b.posts) == int(changed == "postpayment")


def test_input_and_permissions(tmp_path):
    browser, b, _, s = setup(tmp_path, False)
    for amount in (0, -1, 2901, True, 1.25):
        assert review(browser, s, amount).status_code in (422, 409)
    p = review(browser, s).json()["credit"]
    assert (
        browser.post(
            BASE, headers=HEADERS, json={k: p[k] for k in ("reference", "confirmation")}
        ).status_code
        == 404
    )
    for headers in (
        {"Origin": "https://guardoath.com"},
        {**HEADERS, "Origin": "https://other.example"},
        {**HEADERS, "X-GuardOath-Admin-Session": "customer"},
        {**HEADERS, "X-GuardOath-Admin-Session": "no-mfa"},
    ):
        assert browser.post(
            BASE + "/preview",
            headers=headers,
            json={"amount_minor": 10, "reason": "duplicate", "selection": s},
        ).status_code in (401, 403)
    assert not b.posts
