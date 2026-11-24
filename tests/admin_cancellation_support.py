"""Synthetic Stripe/Dynamo boundaries for the actual cancellation implementation."""
import copy
import io
import json

from admin_test_support import AdminTestDynamo, ADMIN_BROWSER_AUDIT_KEY
from guardoath.admin_cancellation_provider import StripePeriodEndCancellation
from guardoath.admin_cancellation_state import DynamoCancellationState
from guardoath.admin_subscription_cancellation import AdminSubscriptionCancellation
from guardoath.dynamodb_store import to_item
from guardoath.tenant_record import TenantRecord

ACTOR = "12345678-1234-1234-1234-123456789012"
OTHER = "87654321-4321-4321-4321-210987654321"
TENANT = "usr-cancellation"
NOW = 1795500000
KEY = ADMIN_BROWSER_AUDIT_KEY


def tenant(tenant_id=TENANT):
    return TenantRecord(tenant_id=tenant_id, display_name="Synthetic cancellation account",
        revision=3, demo=False, billing_mode="stripe", payer_id="cus_cancel",
        stripe_subscription_id="sub_cancel", subscription_status="active",
        monthly_allowance=4000, overage="block", key_hashes=("b" * 64,),
        input_check_pii="block", output_check_pii="block",
        input_rule_actions={"pii.email_address":"off"},
        output_rule_actions={"pii.email_address":"off"}, usage={"2026-11":231})


def snapshot():
    return {"object":"subscription", "id":"sub_cancel", "customer":"cus_cancel",
        "livemode":False, "status":"active", "collection_method":"charge_automatically",
        "pause_collection":None, "schedule":None, "pending_update":None,
        "cancel_at_period_end":False, "cancel_at":None,
        "current_period_start":NOW - 86400, "current_period_end":NOW + 86400 * 10,
        "items":{"object":"list", "has_more":False, "data":[{
            "object":"subscription_item", "id":"si_cancel", "subscription":"sub_cancel",
            "quantity":1, "price":{"object":"price", "id":"price_plan", "livemode":False}}]}}


class Response(io.BytesIO):
    status = 200


class ProviderBoundary:
    def __init__(self):
        self.value = snapshot()
        self.calls = []
        self.account = {"object":"account", "id":"acct_owner"}
        self.before = lambda request: None
        self.after = lambda request, value: value
        self.lose_post_response = False

    def open(self, request, *, timeout):
        assert 0 < timeout <= 1.0
        self.calls.append(request)
        self.before(request)
        if request.full_url == "https://api.stripe.com/v1/account":
            assert request.get_method() == "GET"
            value = self.account
        else:
            assert request.full_url == "https://api.stripe.com/v1/subscriptions/" + self.value["id"]
            if request.get_method() == "POST":
                assert request.data == b"cancel_at_period_end=true"
                self.value["cancel_at_period_end"] = True
                self.value["cancel_at"] = self.value["current_period_end"]
                if self.lose_post_response:
                    raise TimeoutError("private provider response")
            value = self.value
        return Response(json.dumps(self.after(request, copy.deepcopy(value))).encode())

    @property
    def posts(self):
        return [r for r in self.calls if r.get_method() == "POST"]


def service(db=None, *, boundary=None, key=KEY, clock=None, seed=True):
    db = db or AdminTestDynamo()
    if seed:
        db.seed("tenant_table", to_item(tenant()))
    boundary = boundary or ProviderBoundary()
    provider = StripePeriodEndCancellation(api_key="rk_test_synthetic_cancel_0123456789",
        expected_account_id="acct_owner", expected_price_id="price_plan", expected_livemode=False,
        opener=boundary.open)
    result = AdminSubscriptionCancellation(state=DynamoCancellationState(db, "audit_table", key),
        tenant_table="tenant_table", preview_key=key, provider=provider, clock=clock or (lambda:NOW))
    return result, db, boundary


def review(service, *, actor=ACTOR, tenant_id=TENANT):
    return service.preview(actor=actor, tenant_id=tenant_id, expected_revision=3)


def request(preview, *, actor=ACTOR, tenant_id=TENANT, key="cancel-reference-123456789"):
    return {"actor":actor, "tenant_id":tenant_id, "expected_revision":3,
        "preview_reference":preview["preview_reference"],
        "confirmation":"SCHEDULE CANCELLATION " + tenant_id,
        "reason":"Account owner requested scheduled cancellation", "idempotency_key":key}


def verified_financial_rows(db):
    """Reopen every dedicated envelope through its actual strict reader."""
    state = DynamoCancellationState(db, "audit_table", KEY)
    values = []
    for row in db.audit_items():
        if "financial_operation" not in row:
            continue
        prefix, kind, identifier = row["event_id"]["S"].split(":")
        assert prefix == "financial-sc1"
        value = state.verify_item(row, kind, identifier)
        if kind in ("intent", "outcome"):
            intent, outcome = state.history(value["action_id"])
            assert intent is not None
        values.append(value)
    return values
