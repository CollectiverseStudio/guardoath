from dataclasses import dataclass, replace
from copy import deepcopy
import io
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin_test_support import AdminTestDynamo
from guardoath.admin_audit import AdminSensitiveReadAuditor
from guardoath.admin_billing import BillingUnavailable
from guardoath.admin_billing_events import AdminBillingEvents, EventCursorError, fetch_event_page
from guardoath.admin_console import VerifiedAdmin, create_admin_router
from test_admin_invoices import Tenant, history

NOW = 1795161600
OWNER = "owner-subject-1234"


@dataclass
class Record(Tenant):
    revision: int = 7


def event(key="first", **changes):
    return {
        "object": "event",
        "id": "evt_" + key,
        "created": NOW,
        "livemode": False,
        "type": "invoice.payment_failed",
        "data": {
            "object": {
                "object": "invoice",
                "id": "in_test",
                "livemode": False,
                "customer": "cus_owner123",
                "subscription": "sub_owner123",
                "customer_email": "private@example.invalid",
            }
        },
        **changes,
    }


def page(rows, more=False):
    return {"object": "list", "data": rows, "has_more": more}


def service(fetch):
    h = history()
    h.clock = lambda: NOW
    return AdminBillingEvents(h, fetch, b"b" * 32)


def test_mixed_tenant_legacy_and_parent_snapshots_and_pagination():
    first = event()
    wrong = deepcopy(first)
    wrong.update(id="evt_wrong")
    wrong["data"]["object"]["customer"] = "cus_other"
    modern = event("modern")
    obj = modern["data"]["object"]
    obj["parent"] = {
        "type": "subscription_details",
        "subscription_details": {"subscription": obj.pop("subscription")},
    }
    conflict = deepcopy(modern)
    conflict.update(id="evt_conflict")
    conflict["data"]["object"]["subscription"] = "sub_elsewhere"
    calls = []

    def fetch(*, since, until, after):
        assert since == NOW - 7 * 86400 and until == NOW
        calls.append(after)
        return page([wrong, first], True) if not after else page([conflict, modern])

    srv = service(fetch)
    recent = srv.read(Record(), OWNER, None)
    assert len(recent["events"]) == 1 and recent["events"][0]["label"] == "Invoice payment failed"
    token = recent["next_cursor"]
    assert token and not any(v in repr(recent) for v in ("private@", "cus_", "sub_owner", "evt_"))
    for actor, record in [
        (OWNER + "other", Record()),
        (OWNER, Record(revision=9)),
        (OWNER, Record(payer_id="cus_other")),
    ]:
        with pytest.raises(EventCursorError):
            srv.read(record, actor, token)
    srv.history.clock = lambda: NOW + 901
    with pytest.raises(EventCursorError):
        srv.read(Record(), OWNER, token)
    srv.history.clock = lambda: NOW
    older = srv.read(Record(), OWNER, token)
    assert older["next_cursor"] is None and len(older["events"]) == 1 and calls == [None, "evt_first"]


def test_empty_scan_can_continue_and_unbound_skips_stripe():
    fetched = []

    def fetch(**args):
        fetched.append(args["after"])
        return page([event(str(len(fetched)), account="acct_other")], True)

    srv = service(fetch)
    assert not srv.read(Record(payer_id=None), OWNER, None)["verified"] and not fetched
    result = srv.read(Record(), OWNER, None)
    assert fetched == [None, "evt_1", "evt_2"] and not result["events"] and result["next_cursor"]


@pytest.mark.parametrize(
    "bad",
    [
        page([event(livemode=True)]),
        page([], True),
        page([event(created=NOW + 1)]),
        page([event(), event()]),
        page([event(type="charge.refunded")]),
        {"object": "list", "data": [], "has_more": 1},
    ],
)
def test_invalid_provider_pages_are_not_empty_history(bad):
    with pytest.raises(BillingUnavailable):
        service(lambda **kw: bad).read(Record(), OWNER, None)


def test_transport_limits_query_and_uses_get_only():
    class Reply(io.BytesIO):
        status = 200

    def opener(req, *, timeout):
        assert req.get_method() == "GET" and urlsplit(req.full_url).netloc == "api.stripe.com"
        q = parse_qs(urlsplit(req.full_url).query)
        assert q["created[gte]"] == ["10"] and q["starting_after"] == ["evt_one"]
        assert "invoice.payment_failed" in q["types[]"] and q["limit"] == ["10"]
        return Reply(json.dumps(page([])).encode())

    assert fetch_event_page(
        "sk_test_" + "x" * 16, since=10, until=100, after="evt_one", opener=opener
    ) == page([])
    with pytest.raises(BillingUnavailable):
        fetch_event_page("sk_live_" + "x" * 16, since=10, until=100, after=None, opener=opener)


def test_mounted_auth_audit_and_binding_change(tmp_path):
    calls = []
    db = AdminTestDynamo(tmp_path / "billing-events.sqlite")
    srv = service(lambda **kw: calls.append(kw) or page([event()]))

    class Store:
        def __init__(self):
            self.n = 0

        def get(self, key):
            self.n += 1
            return replace(Record(), revision=self.n if self.n > 1 else 7)

    app = FastAPI()
    app.include_router(
        create_admin_router(
            tenant_store=Store(),
            billing_invoices=srv.history,
            billing_events=srv,
            verify_cognito_admin=lambda t: VerifiedAdmin(OWNER, frozenset({t}), True),
            read_auditor=AdminSensitiveReadAuditor(db, "audit_table", signing_key=b"a" * 32),
        )
    )
    c = TestClient(app)
    path = "/v1/admin/subscriptions/usr-invoices/events"
    assert c.get(path).status_code == 401
    assert c.get(path, headers={"X-GuardOath-Admin-Session": "customer"}).status_code == 403
    assert not calls and not db.audit_items()
    db.fail_audit = True
    assert c.get(path, headers={"X-GuardOath-Admin-Session": "owner"}).status_code == 503
    assert not calls and not db.audit_items()
    db.fail_audit = False
    res = c.get(path, headers={"X-GuardOath-Admin-Session": "owner"})
    assert res.status_code == 409 and len(calls) == 1 and len(db.audit_items()) == 1
    assert "private@" not in res.text
