"""Synthetic boundaries for actual owner invoice-refund service/API acceptance."""
import copy
import io
import json
import urllib.parse

from admin_test_support import AdminTestDynamo, ADMIN_BROWSER_AUDIT_KEY
from guardoath.admin_invoice_refund import AdminInvoiceRefund
from guardoath.admin_invoices import InvoiceRefundReferences
from guardoath.admin_refund_provider import StripeInvoiceRefund
from guardoath.admin_refund_state import DynamoRefundState
from guardoath.dynamodb_store import from_item, to_item
from guardoath.tenant_record import TenantRecord

ACTOR = "12345678-1234-1234-1234-123456789012"
OTHER = "87654321-4321-4321-4321-210987654321"
TENANT = "usr-refund"
NOW = 1795700000
KEY = ADMIN_BROWSER_AUDIT_KEY
ACTION = "a" * 64


def tenant(tenant_id=TENANT):
    return TenantRecord(tenant_id=tenant_id, display_name="Synthetic refund account",
        revision=3, demo=False, billing_mode="stripe", payer_id="cus_refund",
        stripe_subscription_id="sub_refund", subscription_status="active",
        monthly_allowance=4000, overage="block", key_hashes=("b" * 64,),
        input_check_pii="block", output_check_pii="block",
        input_rule_actions={"pii.email_address":"off"},
        output_rule_actions={"pii.email_address":"off"}, usage={"2026-11":231})


def invoice():
    return {"object": "invoice", "id": "in_refund", "customer": "cus_refund",
        "subscription": "sub_refund", "livemode": False, "status": "paid", "paid": True,
        "paid_out_of_band": False, "collection_method": "charge_automatically",
        "billing_reason": "subscription_cycle", "amount_paid": 1299, "amount_due": 1299,
        "total": 1299, "amount_remaining": 0, "starting_balance": 0, "ending_balance": 0,
        "pre_payment_credit_notes_amount": 0, "post_payment_credit_notes_amount": 0,
        "created": NOW - 200, "status_transitions": {"finalized_at": NOW - 190,
            "paid_at": NOW - 100, "voided_at": None, "marked_uncollectible_at": None},
        "application": None, "application_fee_amount": None, "on_behalf_of": None,
        "transfer_data": None, "currency": "usd", "charge": "ch_refund",
        "payment_intent": "pi_refund", "lines": {"object": "list", "has_more": False,
            "data": [{"object": "line_item", "id": "il_refund", "invoice": "in_refund",
                "subscription": "sub_refund", "livemode": False, "type": "subscription",
                "proration": False, "quantity": 1, "amount": 1299, "currency": "usd",
                "price": {"object": "price", "id": "price_plan", "livemode": False,
                          "type": "recurring", "currency": "usd"},
                "pretax_credit_amounts": []}]}}


def payment_intent():
    return {"object": "payment_intent", "id": "pi_refund", "customer": "cus_refund",
        "invoice": "in_refund", "latest_charge": "ch_refund", "livemode": False,
        "status": "succeeded", "currency": "usd", "amount": 1299, "amount_received": 1299,
        "amount_capturable": 0, "created": NOW - 190, "capture_method": "automatic",
        "canceled_at": None, "next_action": None, "processing": None, "review": None,
        "application": None, "application_fee_amount": None, "on_behalf_of": None,
        "transfer_data": None, "transfer_group": None}


def charge():
    return {"object": "charge", "id": "ch_refund", "customer": "cus_refund",
        "invoice": "in_refund", "payment_intent": "pi_refund", "livemode": False,
        "status": "succeeded", "paid": True, "captured": True, "disputed": False,
        "currency": "usd", "amount": 1299, "amount_captured": 1299, "amount_refunded": 0,
        "refunded": False, "created": NOW - 100, "balance_transaction": "txn_refund",
        "failure_code": None, "failure_message": None, "failure_balance_transaction": None,
        "review": None, "application": None, "application_fee": None,
        "application_fee_amount": None, "on_behalf_of": None, "transfer_data": None,
        "source_transfer": None, "transfer_group": None}


def refund():
    # Acacia Refund has no livemode field; account/mode proof comes from its charge.
    return {"object": "refund", "id": "re_refund", "charge": "ch_refund",
        "payment_intent": "pi_refund", "amount": 1299, "currency": "usd", "status": "succeeded",
        "created": NOW, "metadata": {"guardoath_action": ACTION}, "reason": None,
        "source_transfer_reversal": None, "transfer_reversal": None}


class Response(io.BytesIO):
    status = 200


class ProviderBoundary:
    """Strict HTTP boundary; mutation IDs come from the actual dispatch request."""
    def __init__(self):
        self.account = {"object":"account", "id":"acct_owner"}
        self.invoice, self.payment_intent, self.charge = invoice(), payment_intent(), charge()
        self.refund = refund()
        self.refunds, self.calls = [], []
        self.has_more = False
        self.before = lambda request: None
        self.after = lambda request, value: value
        self.lose_post_response = False

    @property
    def status(self):
        return self.refund["status"]

    @status.setter
    def status(self, value):
        self.refund["status"] = value
        self._sync_charge()

    def _sync_charge(self):
        succeeded = bool(self.refunds) and self.refund["status"] == "succeeded"
        self.charge.update(amount_refunded=self.refund["amount"] if succeeded else 0,
                           refunded=succeeded)
        if self.refunds:
            self.refunds = [copy.deepcopy(self.refund)]

    def open(self, request, *, timeout):
        path = urllib.parse.urlsplit(request.full_url)
        assert path.scheme == "https" and path.netloc == "api.stripe.com"
        assert 0 < timeout <= 1
        self.calls.append(request)
        self.before(request)
        if request.get_method() == "POST":
            assert path.path == "/v1/refunds" and not path.query
            body = urllib.parse.parse_qs(request.data.decode())
            assert set(body) == {"charge", "amount", "metadata[guardoath_action]"}
            assert body["charge"] == [self.charge["id"]]
            assert body["amount"] == [str(self.invoice["amount_paid"])]
            self.refund["metadata"]["guardoath_action"] = body["metadata[guardoath_action]"][0]
            self.refunds = [copy.deepcopy(self.refund)]
            self._sync_charge()
            if self.lose_post_response:
                raise TimeoutError("private provider response")
            value = self.refund
        elif path.path == "/v1/account":
            value = self.account
        elif path.path == "/v1/invoices/" + self.invoice["id"]:
            value = self.invoice
        elif path.path == "/v1/payment_intents/" + self.payment_intent["id"]:
            value = self.payment_intent
        elif path.path == "/v1/charges/" + self.charge["id"]:
            value = self.charge
        elif path.path == "/v1/refunds":
            assert urllib.parse.parse_qs(path.query) == {"charge":[self.charge["id"]],"limit":["2"]}
            value = {"object":"list", "has_more":self.has_more, "data":self.refunds}
        elif path.path == "/v1/refunds/" + self.refund["id"]:
            value = self.refund
        else:
            raise AssertionError("Unexpected provider request")
        return Response(json.dumps(self.after(request, copy.deepcopy(value))).encode())

    @property
    def posts(self):
        return [r for r in self.calls if r.get_method() == "POST"]


def service(db=None, *, boundary=None, key=KEY, clock=None, seed=True):
    db = db or AdminTestDynamo()
    if seed:
        db.seed("tenant_table", to_item(tenant()))
    boundary = boundary or ProviderBoundary()
    provider = StripeInvoiceRefund(api_key="rk_test_synthetic_refund_0123456789",
        expected_account_id="acct_owner", expected_price_id="price_plan", expected_livemode=False,
        opener=boundary.open)
    references = InvoiceRefundReferences(key, expected_price_id="price_plan", expected_livemode=False)
    result = AdminInvoiceRefund(state=DynamoRefundState(db, "audit_table", key),
        tenant_table="tenant_table", preview_key=key, invoice_references=references,
        provider=provider, clock=clock or (lambda:NOW))
    return result, db, boundary


def invoice_reference(actions, *, actor=ACTOR, tenant_id=TENANT):
    item = actions.state.client.get_item(TableName=actions.tenant_table,
        Key={"tenant_id":{"S":tenant_id}}, ConsistentRead=True)["Item"]
    return actions.invoice_references.seal(from_item(item), actor=actor,
        invoice_id="in_refund", now=int(actions._clock()))


def review(actions, *, actor=ACTOR, tenant_id=TENANT):
    return actions.preview(actor=actor, tenant_id=tenant_id, expected_revision=3,
        invoice_reference=invoice_reference(actions, actor=actor, tenant_id=tenant_id))


def request(preview, *, actor=ACTOR, tenant_id=TENANT, key="refund-reference-123456789"):
    return {"actor":actor, "tenant_id":tenant_id, "expected_revision":3,
        "preview_reference":preview["preview_reference"], "confirmation":preview["confirmation"],
        "reason":"Account owner requested full invoice refund", "idempotency_key":key}


def verified_refund_rows(db):
    """Reopen each new envelope using the exact source reader."""
    state = DynamoRefundState(db, "audit_table", KEY)
    values = []
    for row in db.audit_items():
        if not row.get("event_id", {}).get("S", "").startswith("financial-ir1:"):
            continue
        prefix, kind, identifier = row["event_id"]["S"].split(":", 2)
        assert prefix == "financial-ir1"
        values.append(state.verify_item(row, kind, identifier))
    return values
