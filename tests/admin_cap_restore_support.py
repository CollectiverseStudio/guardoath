"""Synthetic boundaries for the actual owner request-cap restoration service."""
import copy

from admin_test_support import AdminTestDynamo, ADMIN_BROWSER_AUDIT_KEY
from guardoath.admin_cap_restore import AdminRequestCapRestoration
from guardoath.admin_cap_restore_state import DynamoCapRestoreState
from guardoath.dynamodb_store import to_item
from guardoath.subscription_reconciliation import StripeBinding
from guardoath.tenant_record import TenantRecord

ACTOR = "12345678-1234-1234-1234-123456789012"
OTHER = "87654321-4321-4321-4321-210987654321"
TENANT = "usr-cap-restore"
NOW = 1795700000
KEY = ADMIN_BROWSER_AUDIT_KEY


def tenant(tenant_id=TENANT, *, status="active"):
    return TenantRecord(tenant_id=tenant_id, display_name="Synthetic capped account", status=status,
        revision=3, demo=False, billing_mode="stripe", payer_id="cus_restore",
        stripe_subscription_id="sub_restore", subscription_status="active",
        monthly_allowance=4000, admin_monthly_allowance_cap=4000, stripe_plan_monthly_allowance=10000,
        overage="block", key_hashes=("b" * 64,), usage={"2026-11":231},
        input_check_pii="block", output_check_pii="block",
        input_rule_actions={"pii.email_address":"off"}, output_rule_actions={"pii.email_address":"off"})


def snapshot():
    return {"object":"subscription", "id":"sub_restore", "customer":"cus_restore",
        "livemode":False, "status":"active", "collection_method":"charge_automatically",
        "pause_collection":None, "schedule":None, "pending_update":None,
        "current_period_start":NOW - 3600, "current_period_end":NOW + 86400,
        "cancel_at_period_end":False, "cancel_at":None,
        "items":{"object":"list", "has_more":False, "data":[{
            "object":"subscription_item", "id":"si_restore", "subscription":"sub_restore", "quantity":1,
            "price":{"object":"price", "id":"price_plan", "livemode":False}}]}}


class ProviderBoundary:
    def __init__(self):
        self.value = snapshot()
        self.calls = []
        self.before = lambda binding:None
        self.after = lambda binding,value:value

    def fetch_snapshot(self, binding, *, timeout):
        assert isinstance(binding, StripeBinding)
        assert 0 < timeout <= 8
        assert binding.customer_id == self.value["customer"]
        assert binding.subscription_id == self.value["id"]
        self.calls.append({"binding":binding, "timeout":timeout})
        self.before(binding)
        return self.after(binding, copy.deepcopy(self.value))


def service(db=None, *, boundary=None, key=KEY, clock=None, seed=True, status="active"):
    db = db or AdminTestDynamo()
    if seed:
        db.seed("tenant_table", to_item(tenant(status=status)))
    boundary = boundary or ProviderBoundary()
    actions = AdminRequestCapRestoration(state=DynamoCapRestoreState(db, "audit_table", key),
        tenant_table="tenant_table", preview_key=key, fetch_snapshot=boundary.fetch_snapshot,
        expected_price_id="price_plan", expected_livemode=False, plan_monthly_allowance=10000,
        clock=clock or (lambda:NOW))
    return actions, db, boundary


def review(actions, *, actor=ACTOR, tenant_id=TENANT):
    return actions.preview(actor=actor, tenant_id=tenant_id, expected_revision=3)


def request(preview, *, actor=ACTOR, tenant_id=TENANT, key="cap-restore-reference-123456789"):
    return {"actor":actor, "tenant_id":tenant_id, "expected_revision":preview["expected_revision"],
        "preview_reference":preview["preview_reference"], "confirmation":preview["confirmation"],
        "reason":"Restore the reviewed configured request allowance", "idempotency_key":key}
