"""Synthetic provider boundaries for one full, independently verified refund."""
import copy
import io
import json
import urllib.parse
import urllib.request

import pytest

from guardoath.admin_refund_provider import RefundProviderUnavailable, StripeInvoiceRefund
from guardoath.subscription_reconciliation import StripeBinding


NOW = 1795700000
ACTION = "a" * 64
BINDING = StripeBinding("usr-refund", "cus_refund", "sub_refund")
CONFIG = {"api_key": "rk_test_synthetic_refund_0123456789", "expected_account_id": "acct_owner",
          "expected_price_id": "price_plan", "expected_livemode": False}
SNAPSHOT = {"account_id": "acct_owner", "livemode": False, "customer_id": "cus_refund",
            "subscription_id": "sub_refund", "invoice_id": "in_refund", "charge_id": "ch_refund",
            "payment_intent_id": "pi_refund", "amount_minor": 1299, "currency": "usd",
            "expected_price_id": "price_plan"}


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


class Boundary:
    def __init__(self):
        self.account = {"object": "account", "id": "acct_owner"}
        self.invoice, self.payment_intent, self.charge = invoice(), payment_intent(), charge()
        self.refund = refund()
        self.refunds = []
        self.has_more = False
        self.calls = []
        self.lose_reply = False

    def __call__(self, request, *, timeout):
        path = urllib.parse.urlsplit(request.full_url)
        assert path.scheme == "https" and path.netloc == "api.stripe.com"
        assert 0 < timeout <= 1
        self.calls.append(request)
        if request.method == "POST":
            assert path.path == "/v1/refunds" and not path.query
            self.refunds = [copy.deepcopy(self.refund)]
            if self.refund["status"] == "succeeded":
                self.charge.update(amount_refunded=1299, refunded=True)
            if self.lose_reply:
                raise TimeoutError("synthetic private transport detail")
            value = self.refund
        elif path.path == "/v1/account":
            value = self.account
        elif path.path == "/v1/invoices/in_refund":
            value = self.invoice
        elif path.path == "/v1/payment_intents/pi_refund":
            value = self.payment_intent
        elif path.path == "/v1/charges/ch_refund":
            value = self.charge
        elif path.path == "/v1/refunds":
            assert urllib.parse.parse_qs(path.query) == {"charge": ["ch_refund"], "limit": ["2"]}
            value = {"object": "list", "has_more": self.has_more, "data": self.refunds}
        elif path.path == "/v1/refunds/re_refund":
            value = self.refund
        else:
            raise AssertionError("Unexpected provider request")
        return Response(json.dumps(value).encode())


@pytest.fixture
def enabled(monkeypatch):
    import guardoath.state_contracts as contracts
    monkeypatch.setattr(contracts, "new_refund_state_enabled", lambda: True, raising=False)


def test_exact_paid_invoice_graph_and_configuration_projection():
    boundary = Boundary()
    provider = StripeInvoiceRefund(**CONFIG, opener=boundary)
    assert provider.inspect(BINDING, "in_refund", now=NOW) == SNAPSHOT
    assert [urllib.parse.urlsplit(c.full_url).path for c in boundary.calls] == [
        "/v1/account", "/v1/invoices/in_refund", "/v1/payment_intents/pi_refund",
        "/v1/charges/ch_refund", "/v1/refunds"]
    assert all(c.method == "GET" and c.data is None for c in boundary.calls)
    assert provider.expected_account_id == "acct_owner"
    assert provider.expected_price_id == "price_plan"
    assert provider.expected_livemode is False
    assert all(c.get_header("Stripe-version") == "2025-02-24.acacia" for c in boundary.calls)
    assert all(c.get_header("Stripe-account") is None for c in boundary.calls)


def test_exact_one_post_and_independent_charge_refund_confirmation(enabled):
    boundary = Boundary()
    provider = StripeInvoiceRefund(**CONFIG, opener=boundary)
    snapshot = provider.inspect(BINDING, "in_refund", now=NOW)
    written = provider.dispatch(snapshot, action_id=ACTION,
        idempotency_key="guardoath-ir1-" + ACTION, now=NOW)
    verified = provider.verify(snapshot, action_id=ACTION, refund_id=written["refund_id"], now=NOW)
    assert written == verified == {**SNAPSHOT, "action_id": ACTION, "refund_id": "re_refund",
                                   "status": "succeeded", "refund_created": NOW}
    posts = [call for call in boundary.calls if call.method == "POST"]
    assert len(posts) == 1
    assert urllib.parse.parse_qs(posts[0].data.decode()) == {
        "charge": ["ch_refund"], "amount": ["1299"], "metadata[guardoath_action]": [ACTION]}
    assert posts[0].get_header("Idempotency-key") == "guardoath-ir1-" + ACTION
    assert posts[0].get_header("Stripe-version") == "2025-02-24.acacia"
    assert posts[0].get_header("Stripe-account") is None
    assert [urllib.parse.urlsplit(c.full_url).path for c in boundary.calls[-3:]] == [
        "/v1/account", "/v1/refunds/re_refund", "/v1/charges/ch_refund"]


def test_lost_post_reply_recovers_by_action_bound_gets_without_resending(enabled):
    boundary = Boundary()
    boundary.lose_reply = True
    provider = StripeInvoiceRefund(**CONFIG, opener=boundary)
    with pytest.raises(RefundProviderUnavailable) as error:
        provider.dispatch(SNAPSHOT, action_id=ACTION,
                          idempotency_key="guardoath-ir1-" + ACTION, now=NOW)
    assert str(error.value) == "" and error.value.__suppress_context__ is True
    assert provider.verify(SNAPSHOT, action_id=ACTION, now=NOW)["status"] == "succeeded"
    assert len([c for c in boundary.calls if c.method == "POST"]) == 1
    assert [urllib.parse.urlsplit(c.full_url).path for c in boundary.calls[1:]] == [
        "/v1/account", "/v1/refunds", "/v1/refunds/re_refund", "/v1/charges/ch_refund"]


def test_plan_change_keeps_new_inspection_dispatch_and_response_validation_current(enabled):
    boundary = Boundary()
    provider = StripeInvoiceRefund(**{**CONFIG, "expected_price_id": "price_next"}, opener=boundary)
    with pytest.raises(RefundProviderUnavailable):
        provider.inspect(BINDING, "in_refund", now=NOW)
    assert [urllib.parse.urlsplit(c.full_url).path for c in boundary.calls] == [
        "/v1/account", "/v1/invoices/in_refund"]
    boundary.calls.clear()
    with pytest.raises(RefundProviderUnavailable):
        provider.dispatch(SNAPSHOT, action_id=ACTION,
                          idempotency_key="guardoath-ir1-" + ACTION, now=NOW)
    with pytest.raises(RefundProviderUnavailable):
        provider.validate_refund(boundary.refund, SNAPSHOT, action_id=ACTION, now=NOW)
    assert boundary.calls == []


@pytest.mark.parametrize("refund_id", [None, "re_refund"])
def test_historical_price_survives_direct_refund_read_and_read_only_recovery(refund_id):
    boundary = Boundary()
    boundary.charge.update(amount_refunded=1299, refunded=True)
    boundary.refunds = [copy.deepcopy(boundary.refund)]
    provider = StripeInvoiceRefund(**{**CONFIG, "expected_price_id": "price_next"}, opener=boundary)
    proof = provider.verify(SNAPSHOT, action_id=ACTION, refund_id=refund_id, now=NOW)
    assert proof == {**SNAPSHOT, "action_id": ACTION, "refund_id": "re_refund",
                     "status": "succeeded", "refund_created": NOW}
    expected_paths = ["/v1/account"]
    if refund_id is None:
        expected_paths.append("/v1/refunds")
    expected_paths.extend(["/v1/refunds/re_refund", "/v1/charges/ch_refund"])
    assert [urllib.parse.urlsplit(c.full_url).path for c in boundary.calls] == expected_paths
    assert all(c.method == "GET" and c.data is None for c in boundary.calls)
    assert provider.expected_price_id == "price_next"


def test_lost_post_reply_can_recover_after_price_change_without_a_second_post(enabled):
    boundary = Boundary()
    boundary.lose_reply = True
    original = StripeInvoiceRefund(**CONFIG, opener=boundary)
    with pytest.raises(RefundProviderUnavailable):
        original.dispatch(SNAPSHOT, action_id=ACTION,
                          idempotency_key="guardoath-ir1-" + ACTION, now=NOW)
    current = StripeInvoiceRefund(**{**CONFIG, "expected_price_id": "price_next"}, opener=boundary)
    proof = current.verify(SNAPSHOT, action_id=ACTION, now=NOW)
    assert proof["status"] == "succeeded" and proof["expected_price_id"] == "price_plan"
    assert [c.method for c in boundary.calls] == ["POST", "GET", "GET", "GET", "GET"]
    with pytest.raises(RefundProviderUnavailable):
        current.dispatch(SNAPSHOT, action_id=ACTION,
                         idempotency_key="guardoath-ir1-" + ACTION, now=NOW)
    assert len(boundary.calls) == 5


@pytest.mark.parametrize("refund_id", [None, "re_refund"])
@pytest.mark.parametrize("change", [{"expected_account_id": "acct_other"},
    {"expected_livemode": True, "api_key": "rk_live_synthetic_refund_0123456789"}])
def test_historical_price_never_relaxes_configured_account_or_mode(change, refund_id):
    boundary = Boundary()
    provider = StripeInvoiceRefund(**{**CONFIG, "expected_price_id": "price_next", **change},
                                   opener=boundary)
    with pytest.raises(RefundProviderUnavailable):
        provider.verify(SNAPSHOT, action_id=ACTION, refund_id=refund_id, now=NOW)
    assert boundary.calls == []


@pytest.mark.parametrize("price", [None, True, "", "price_", "prod_other", "price_bad/route",
                                  "price_" + "x" * 97])
def test_historical_price_must_be_a_canonical_price_id_before_any_read(price):
    boundary = Boundary()
    provider = StripeInvoiceRefund(**{**CONFIG, "expected_price_id": "price_next"}, opener=boundary)
    with pytest.raises(RefundProviderUnavailable):
        provider.verify({**SNAPSHOT, "expected_price_id": price}, action_id=ACTION, now=NOW)
    assert boundary.calls == []


@pytest.mark.parametrize("refund_id", [None, "re_refund"])
@pytest.mark.parametrize("resource,field,value", [
    ("account", "id", "acct_other"), ("charge", "customer", "cus_other"),
    ("charge", "invoice", "in_other"), ("charge", "livemode", True),
    ("charge", "amount_refunded", 0), ("refund", "charge", "ch_other"),
    ("refund", "payment_intent", "pi_other"), ("refund", "amount", 1200),
    ("refund", "currency", "eur"), ("refund", "metadata", {"guardoath_action": "b" * 64}),
])
def test_historical_price_retains_exact_independent_payment_proof(resource, field, value, refund_id):
    boundary = Boundary()
    boundary.charge.update(amount_refunded=1299, refunded=True)
    getattr(boundary, resource)[field] = value
    boundary.refunds = [copy.deepcopy(boundary.refund)]
    provider = StripeInvoiceRefund(**{**CONFIG, "expected_price_id": "price_next"}, opener=boundary)
    with pytest.raises(RefundProviderUnavailable):
        provider.verify(SNAPSHOT, action_id=ACTION, refund_id=refund_id, now=NOW)
    assert boundary.calls and all(c.method == "GET" and c.data is None for c in boundary.calls)


@pytest.mark.parametrize("field,value", [
    ("customer", "cus_other"), ("subscription", "sub_other"), ("livemode", True),
    ("livemode", 0), ("paid", 1), ("paid", False), ("status", "open"),
    ("paid_out_of_band", True), ("amount_paid", True), ("amount_paid", 0),
    ("amount_paid", 100_000_000), ("amount_due", 1300), ("total", 1300),
    ("amount_remaining", 1), ("starting_balance", -100), ("starting_balance", 100),
    ("ending_balance", -1), ("ending_balance", True),
    ("pre_payment_credit_notes_amount", 1), ("post_payment_credit_notes_amount", 1),
    ("currency", "USD"), ("charge", "ch_other"), ("payment_intent", "pi_other"),
    ("collection_method", "send_invoice"), ("billing_reason", "subscription_update"),
    ("application", "ca_other"), ("application_fee_amount", 1),
    ("on_behalf_of", "acct_other"), ("transfer_data", {}), ("created", NOW + 1),
    ("payments", {"object": "list", "data": []}), ("amount_overpaid", 1),
])
def test_unsupported_or_mismatched_invoice_cannot_reach_a_refund(field, value):
    boundary = Boundary()
    boundary.invoice[field] = value
    provider = StripeInvoiceRefund(**CONFIG, opener=boundary)
    with pytest.raises(RefundProviderUnavailable):
        provider.inspect(BINDING, "in_refund", now=NOW)
    assert all(call.method == "GET" for call in boundary.calls)


@pytest.mark.parametrize("mutation", ["two_lines", "more_lines", "price", "mode", "currency",
    "quantity", "proration", "other_invoice", "other_subscription", "invoice_item", "credits",
    "future_payment", "missing_amount", "missing_paid_at"])
def test_invoice_line_price_and_complete_payment_evidence_are_required(mutation):
    boundary = Boundary()
    line = boundary.invoice["lines"]["data"][0]
    if mutation == "two_lines": boundary.invoice["lines"]["data"].append(copy.deepcopy(line))
    elif mutation == "more_lines": boundary.invoice["lines"]["has_more"] = True
    elif mutation == "price": line["price"]["id"] = "price_other"
    elif mutation == "mode": line["price"]["livemode"] = True
    elif mutation == "currency": line["currency"] = "eur"
    elif mutation == "quantity": line["quantity"] = True
    elif mutation == "proration": line["proration"] = True
    elif mutation == "other_invoice": line["invoice"] = "in_other"
    elif mutation == "other_subscription": line["subscription"] = "sub_other"
    elif mutation == "invoice_item": line["type"] = "invoiceitem"
    elif mutation == "credits": line["pretax_credit_amounts"] = [{"type": "credit_balance_transaction", "amount": 1}]
    elif mutation == "future_payment": boundary.invoice["status_transitions"]["paid_at"] = NOW + 1
    elif mutation == "missing_amount": del boundary.invoice["amount_paid"]
    elif mutation == "missing_paid_at": del boundary.invoice["status_transitions"]["paid_at"]
    with pytest.raises(RefundProviderUnavailable):
        StripeInvoiceRefund(**CONFIG, opener=boundary).inspect(BINDING, "in_refund", now=NOW)


@pytest.mark.parametrize("field,value", [("latest_charge", "ch_other"), ("invoice", "in_other"),
    ("customer", "cus_other"), ("livemode", True), ("amount_received", 1000),
    ("amount", True), ("amount_capturable", 1), ("status", "processing"),
    ("capture_method", "manual"), ("processing", {}), ("review", "prv_review"),
    ("next_action", {}), ("transfer_data", {}), ("created", NOW + 1)])
def test_payment_intent_must_prove_the_same_single_successful_charge(field, value):
    boundary = Boundary()
    boundary.payment_intent[field] = value
    with pytest.raises(RefundProviderUnavailable):
        StripeInvoiceRefund(**CONFIG, opener=boundary).inspect(BINDING, "in_refund", now=NOW)


@pytest.mark.parametrize("field,value", [("invoice", "in_other"), ("customer", "cus_other"),
    ("payment_intent", "pi_other"), ("livemode", True), ("amount_captured", 1000),
    ("amount", True), ("amount_refunded", 1), ("refunded", True), ("disputed", True),
    ("captured", False), ("paid", False), ("status", "failed"), ("currency", "eur"),
    ("balance_transaction", None), ("failure_code", "failed"), ("review", "prv_review"),
    ("source_transfer", "tr_other"), ("application_fee", "fee_other")])
def test_unsettled_disputed_refunded_or_unbound_charge_is_rejected(field, value):
    boundary = Boundary()
    boundary.charge[field] = value
    with pytest.raises(RefundProviderUnavailable):
        StripeInvoiceRefund(**CONFIG, opener=boundary).inspect(BINDING, "in_refund", now=NOW)


@pytest.mark.parametrize("status", ["pending", "requires_action", "failed", "canceled", "succeeded"])
def test_any_preexisting_refund_blocks_a_new_review(status):
    boundary = Boundary()
    prior = refund()
    prior["status"] = status
    boundary.refunds = [prior]
    with pytest.raises(RefundProviderUnavailable):
        StripeInvoiceRefund(**CONFIG, opener=boundary).inspect(BINDING, "in_refund", now=NOW)


@pytest.mark.parametrize("status", ["pending", "requires_action", "failed", "canceled"])
def test_known_non_success_is_observed_without_claiming_completion(status, enabled):
    boundary = Boundary()
    boundary.refund["status"] = status
    provider = StripeInvoiceRefund(**CONFIG, opener=boundary)
    dispatched = provider.dispatch(SNAPSHOT, action_id=ACTION,
        idempotency_key="guardoath-ir1-" + ACTION, now=NOW)
    assert dispatched["status"] == status
    assert provider.verify(SNAPSHOT, action_id=ACTION, refund_id="re_refund", now=NOW)["status"] == status
    assert boundary.charge["amount_refunded"] == 0


def test_provider_pending_then_succeeded_can_be_read_without_a_second_post(enabled):
    boundary = Boundary()
    boundary.refund["status"] = "pending"
    provider = StripeInvoiceRefund(**CONFIG, opener=boundary)
    provider.dispatch(SNAPSHOT, action_id=ACTION, idempotency_key="guardoath-ir1-" + ACTION, now=NOW)
    assert provider.verify(SNAPSHOT, action_id=ACTION, refund_id="re_refund", now=NOW)["status"] == "pending"
    boundary.refund["status"] = "succeeded"
    boundary.charge.update(amount_refunded=1299, refunded=True)
    assert provider.verify(SNAPSHOT, action_id=ACTION, refund_id="re_refund", now=NOW)["status"] == "succeeded"
    assert sum(call.method == "POST" for call in boundary.calls) == 1


@pytest.mark.parametrize("mutation", ["missing", "multiple", "has_more", "other_action", "not_full",
                                     "wrong_charge", "wrong_pi", "unknown_status", "future", "other_mode"])
def test_lost_response_recovery_refuses_ambiguous_or_foreign_refunds(mutation):
    boundary = Boundary()
    boundary.charge.update(amount_refunded=1299, refunded=True)
    boundary.refunds = [boundary.refund]
    if mutation == "missing": boundary.refunds = []
    elif mutation == "multiple": boundary.refunds.append(copy.deepcopy(boundary.refund))
    elif mutation == "has_more": boundary.has_more = True
    elif mutation == "other_action": boundary.refund["metadata"]["guardoath_action"] = "b" * 64
    elif mutation == "not_full": boundary.refund["amount"] = 1200
    elif mutation == "wrong_charge": boundary.refund["charge"] = "ch_other"
    elif mutation == "wrong_pi": boundary.refund["payment_intent"] = "pi_other"
    elif mutation == "unknown_status": boundary.refund["status"] = "unknown"
    elif mutation == "future": boundary.refund["created"] = NOW + 60
    elif mutation == "other_mode": boundary.charge["livemode"] = True
    with pytest.raises(RefundProviderUnavailable):
        StripeInvoiceRefund(**CONFIG, opener=boundary).verify(SNAPSHOT, action_id=ACTION, now=NOW)
    assert all(call.method == "GET" for call in boundary.calls)


def test_succeeded_refund_alone_does_not_establish_money_returned():
    boundary = Boundary()
    with pytest.raises(RefundProviderUnavailable):
        StripeInvoiceRefund(**CONFIG, opener=boundary).verify(
            SNAPSHOT, action_id=ACTION, refund_id="re_refund", now=NOW)
    assert boundary.charge["refunded"] is False


def test_new_refund_timestamp_can_cross_second_boundary_during_post(enabled, monkeypatch):
    boundary = Boundary()
    boundary.refund["created"] = NOW + 1
    provider = StripeInvoiceRefund(**CONFIG, opener=boundary)
    ticks = iter([10.0, 10.8])
    monkeypatch.setattr("guardoath.admin_refund_provider.time.monotonic", lambda: next(ticks))
    result = provider.dispatch(SNAPSHOT, action_id=ACTION,
                               idempotency_key="guardoath-ir1-" + ACTION, now=NOW)
    assert result["refund_created"] == NOW + 1
    assert len(boundary.calls) == 1


def test_refund_timestamp_far_beyond_elapsed_post_remains_unknown(enabled, monkeypatch):
    boundary = Boundary()
    boundary.refund["created"] = NOW + 60
    provider = StripeInvoiceRefund(**CONFIG, opener=boundary)
    ticks = iter([10.0, 10.8])
    monkeypatch.setattr("guardoath.admin_refund_provider.time.monotonic", lambda: next(ticks))
    with pytest.raises(RefundProviderUnavailable):
        provider.dispatch(SNAPSHOT, action_id=ACTION,
                          idempotency_key="guardoath-ir1-" + ACTION, now=NOW)
    assert len(boundary.calls) == 1


@pytest.mark.parametrize("field,value", [("id", "re_other"), ("metadata", {}),
    ("amount", True), ("currency", "eur"), ("source_transfer_reversal", "trr_other"),
    ("transfer_reversal", "trr_other"), ("reason", "fraudulent"), ("livemode", True),
    ("failure_reason", "unknown"), ("next_action", {})])
def test_independent_refund_read_must_match_exact_intent(field, value):
    boundary = Boundary()
    boundary.refund[field] = value
    boundary.charge.update(amount_refunded=1299, refunded=True)
    with pytest.raises(RefundProviderUnavailable):
        StripeInvoiceRefund(**CONFIG, opener=boundary).verify(
            SNAPSHOT, action_id=ACTION, refund_id="re_refund", now=NOW)


@pytest.mark.parametrize("key", ["pk_test_01234567890123", "rk_live_01234567890123",
    "sk_live_01234567890123", "rk_test_short", "rk_test_01234567890123\n", "rk_test_bad key"])
def test_refund_key_must_be_explicit_and_match_mode(key):
    with pytest.raises(ValueError): StripeInvoiceRefund(**{**CONFIG, "api_key": key})


@pytest.mark.parametrize("change", [{"expected_livemode": 0}, {"expected_account_id": "acct_bad/path"},
    {"expected_price_id": "price_bad?query"}, {"timeout": True}, {"timeout": 0},
    {"timeout": float("nan")}, {"timeout": 1.1}, {"opener": "untrusted"}])
def test_invalid_configuration_refuses_before_transport(change):
    with pytest.raises(ValueError): StripeInvoiceRefund(**{**CONFIG, **change})


@pytest.mark.parametrize("raw", [b'{"object":"account","object":"account","id":"acct_owner"}',
    b'{"value":NaN}', b'{"value":1e999}', b'[]', b'null', b'\xff', b'{', b' ' * 262145])
def test_strict_bounded_provider_json_and_sanitized_failure(raw):
    calls = []
    def opened(request, **_kwargs): calls.append(request); return Response(raw)
    with pytest.raises(RefundProviderUnavailable) as error:
        StripeInvoiceRefund(**CONFIG, opener=opened).inspect(BINDING, "in_refund", now=NOW)
    assert str(error.value) == "" and len(calls) == 1


@pytest.mark.parametrize("status", [201, 301, 302, 307, 400, 401, 403, 429, 500])
def test_provider_http_failure_is_not_retried(status):
    calls = []
    def opened(request, **_kwargs):
        calls.append(request)
        response = Response(b'{"object":"account","id":"acct_owner"}')
        response.status = status
        return response
    with pytest.raises(RefundProviderUnavailable):
        StripeInvoiceRefund(**CONFIG, opener=opened).inspect(BINDING, "in_refund", now=NOW)
    assert len(calls) == 1


def test_redirects_are_refused_by_default(monkeypatch):
    handlers = []
    def builder(handler):
        handlers.append(handler)
        return type("Opener", (), {"open": lambda *_a, **_k: None})()
    monkeypatch.setattr(urllib.request, "build_opener", builder)
    StripeInvoiceRefund(**CONFIG)
    assert handlers[0].redirect_request(None, None, 302, "redirect", {}, "https://foreign.invalid") is None


@pytest.mark.parametrize("deadline", [True, float("nan"), float("inf"), -1])
def test_invalid_or_expired_deadline_never_sends(deadline):
    boundary = Boundary()
    with pytest.raises(RefundProviderUnavailable):
        StripeInvoiceRefund(**CONFIG, opener=boundary).inspect(BINDING, "in_refund", now=NOW, deadline=deadline)
    assert boundary.calls == []


def test_expired_reply_does_not_establish_verification(monkeypatch):
    ticks = iter([10.0, 12.0])
    monkeypatch.setattr("guardoath.admin_refund_provider.time.monotonic", lambda: next(ticks))
    boundary = Boundary()
    with pytest.raises(RefundProviderUnavailable):
        StripeInvoiceRefund(**CONFIG, opener=boundary).inspect(BINDING, "in_refund", now=NOW, deadline=11.0)
    assert len(boundary.calls) == 1


@pytest.mark.parametrize("change", [{"amount_minor": True}, {"amount_minor": 0},
    {"amount_minor": 100_000_000}, {"account_id": "acct_other"}, {"livemode": True},
    {"expected_price_id": "price_bad/route"}, {"invoice_id": "in_bad/route"},
    {"charge_id": "ch_bad?query"}, {"payment_intent_id": "pi_other/path"},
    {"currency": "USD"}, {"unexpected": "field"}])
def test_invalid_snapshot_cannot_dispatch_or_read_provider(change, enabled):
    boundary = Boundary()
    provider = StripeInvoiceRefund(**CONFIG, opener=boundary)
    changed = {**SNAPSHOT, **change}
    with pytest.raises(RefundProviderUnavailable):
        provider.dispatch(changed, action_id=ACTION, idempotency_key="guardoath-ir1-" + ACTION, now=NOW)
    with pytest.raises(RefundProviderUnavailable):
        provider.verify(changed, action_id=ACTION, refund_id="re_refund", now=NOW)
    assert boundary.calls == []


def test_source_gate_and_action_bound_idempotency_precede_post(monkeypatch):
    import guardoath.state_contracts as contracts
    boundary = Boundary()
    provider = StripeInvoiceRefund(**CONFIG, opener=boundary)
    monkeypatch.setattr(contracts, "new_refund_state_enabled", lambda: False, raising=False)
    with pytest.raises(RefundProviderUnavailable):
        provider.dispatch(SNAPSHOT, action_id=ACTION, idempotency_key="guardoath-ir1-" + ACTION, now=NOW)
    monkeypatch.setattr(contracts, "new_refund_state_enabled", lambda: True)
    with pytest.raises(RefundProviderUnavailable):
        provider.dispatch(SNAPSHOT, action_id=ACTION, idempotency_key="guardoath-ir1-" + "b" * 64, now=NOW)
    assert boundary.calls == []


def test_charge_target_survives_secret_rotation_but_changes_for_account_and_mode():
    first = StripeInvoiceRefund(**CONFIG)
    rotated = StripeInvoiceRefund(**{**CONFIG, "api_key": "rk_test_rotated_synthetic_0123456789"})
    other = StripeInvoiceRefund(**{**CONFIG, "expected_account_id": "acct_other"})
    live = StripeInvoiceRefund(**{**CONFIG, "api_key": "rk_live_synthetic_012345678901",
                                 "expected_livemode": True})
    assert first.target_id("ch_refund") == rotated.target_id("ch_refund")
    assert len({first.target_id("ch_refund"), other.target_id("ch_refund"), live.target_id("ch_refund")}) == 3
