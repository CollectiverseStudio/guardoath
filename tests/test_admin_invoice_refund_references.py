"""Opaque invoice selection authority and exact money display boundaries."""
import copy
import json

import pytest

from guardoath.admin_invoices import AdminInvoiceHistory, InvalidInvoiceReference, InvoiceRefundReferences
from guardoath.admin_billing import AdminSubscriptionReporter
from guardoath.admin_invoice_refund import currency_exponent, RefundUnavailable
from guardoath.tenant_record import TenantRecord

ACTOR = "12345678-1234-1234-1234-123456789012"
NOW = 1795700000


def record(**changes):
    return TenantRecord(tenant_id="usr-refund", display_name="Synthetic refund account", revision=3, demo=False,
        billing_mode="stripe", payer_id="cus_refund", stripe_subscription_id="sub_refund",
        subscription_status="active", input_check_pii="block", output_check_pii="block",
        **changes)


def codec(**changes):
    return InvoiceRefundReferences(b"i" * 32,
        **{"expected_price_id": "price_plan", "expected_livemode": False, **changes})


def test_invoice_reference_hides_provider_identifiers_and_roundtrips():
    references, tenant = codec(), record()
    token = references.seal(tenant, actor=ACTOR, invoice_id="in_refund", now=NOW)
    assert token.startswith("ir1.") and "in_refund" not in token
    assert references.open(token, tenant, actor=ACTOR, now=NOW + 1) == "in_refund"
    assert references.seal(tenant, actor=ACTOR, invoice_id="in_refund", now=NOW) != token


@pytest.mark.parametrize("change", [
    {"tenant_id": "usr-other"}, {"payer_id": "cus_other"},
    {"stripe_subscription_id": "sub_other"}, {"revision": 4},
    {"demo": True}, {"billing_mode": "internal"},
])
def test_invoice_selection_is_bound_to_exact_saved_authority(change):
    references, tenant = codec(), record()
    token = references.seal(tenant, actor=ACTOR, invoice_id="in_refund", now=NOW)
    changed = tenant.model_copy(update=change)
    with pytest.raises(InvalidInvoiceReference):
        references.open(token, changed, actor=ACTOR, now=NOW)


@pytest.mark.parametrize("other", [codec(expected_livemode=True), codec(expected_price_id="price_other"),
    InvoiceRefundReferences(b"j" * 32, expected_price_id="price_plan", expected_livemode=False)])
def test_reference_rejects_another_mode_plan_or_key(other):
    tenant = record()
    token = codec().seal(tenant, actor=ACTOR, invoice_id="in_refund", now=NOW)
    with pytest.raises(InvalidInvoiceReference):
        other.open(token, tenant, actor=ACTOR, now=NOW)


@pytest.mark.parametrize("now", [NOW - 1, NOW + 900, NOW + 901, True, float("nan")])
def test_expired_future_or_invalid_reference_clock_is_rejected(now):
    tenant, references = record(), codec()
    token = references.seal(tenant, actor=ACTOR, invoice_id="in_refund", now=NOW)
    with pytest.raises(InvalidInvoiceReference):
        references.open(token, tenant, actor=ACTOR, now=now)


def test_reference_rejects_wrong_actor_tampering_and_padding():
    tenant, references = record(), codec()
    token = references.seal(tenant, actor=ACTOR, invoice_id="in_refund", now=NOW)
    with pytest.raises(InvalidInvoiceReference):
        references.open(token, tenant, actor="87654321-4321-4321-4321-210987654321", now=NOW)
    for changed in (token + "=", "ic1." + token[4:], token[:15] + ("A" if token[15] != "A" else "B") + token[16:], "in_refund"):
        with pytest.raises(InvalidInvoiceReference):
            references.open(changed, tenant, actor=ACTOR, now=NOW)


def test_history_issues_references_only_for_paid_rows_with_a_current_revision():
    from test_admin_refund_provider import invoice
    item = invoice()
    subscription = {"id": "sub_refund", "customer": "cus_refund", "object": "subscription",
        "status": "active", "livemode": False, "items": {"data": [
            {"price": {"id": "price_plan"}, "quantity": 1}], "has_more": False}}
    reporter = AdminSubscriptionReporter(lambda *_: subscription,
        expected_price_id="price_plan", expected_livemode=False)
    history = AdminInvoiceHistory(lambda *_, **__: {"object": "list", "data": [item], "has_more": False},
        reporter, cursor_key=b"i" * 32, clock=lambda: NOW)
    tenant = record()
    before = copy.deepcopy(tenant)
    page = history.read(tenant, owner_sub=ACTOR)
    token = page["invoices"][0]["invoice_reference"]
    assert page["expected_revision"] == 3
    assert history.refund_references.open(token, tenant, actor=ACTOR, now=NOW) == "in_refund"
    assert "in_refund" not in json.dumps(page) and tenant == before
    item.update(status="open", amount_paid=0, amount_remaining=1299)
    assert history.read(tenant, owner_sub=ACTOR)["invoices"][0]["invoice_reference"] is None


@pytest.mark.parametrize("currency,exponent", [("usd", 2), ("eur", 2), ("jpy", 0), ("krw", 0),
    ("huf", 2), ("twd", 2), ("isk", 2), ("ugx", 2)])
def test_refund_display_uses_stripe_charge_units(currency, exponent):
    assert currency_exponent(currency) == exponent


@pytest.mark.parametrize("currency", ["zzz", "USD", "", "bhd"])
def test_unsupported_currency_display_fails_before_refund_review(currency):
    with pytest.raises(RefundUnavailable):
        currency_exponent(currency)
