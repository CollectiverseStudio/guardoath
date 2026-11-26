"""Actual refund orchestration across synthetic HTTP and durable Dynamo boundaries."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from admin_refund_support import (ACTOR, OTHER, TENANT, NOW, KEY, ProviderBoundary,
    service, review, request, invoice_reference, verified_refund_rows)
from admin_test_support import AdminTestDynamo
from guardoath import state_contracts
from guardoath.admin_invoice_refund import (
    AdminInvoiceRefund, RefundConflict, RefundUnavailable, RefundRateLimited,
)
from guardoath.admin_invoices import InvoiceRefundReferences
from guardoath.admin_refund_provider import StripeInvoiceRefund
from guardoath.admin_refund_state import DynamoRefundState


@pytest.fixture(autouse=True)
def enable_writer(monkeypatch):
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_REFUND_STATE", True)


def stored_tenant(db):
    return db.get_item(TableName="tenant_table", Key={"tenant_id":{"S":TENANT}})["Item"]


def receipt(actions, *, actor=ACTOR, key="refund-reference-123456789"):
    return actions.receipt(actor=actor, tenant_id=TENANT, idempotency_key=key)


def refresh(actions, *, actor=ACTOR, key="refund-reference-123456789"):
    return actions.refresh(actor=actor, tenant_id=TENANT, idempotency_key=key)


def test_review_dispatch_and_signed_receipt_preserve_entire_tenant():
    actions, db, provider = service()
    before = stored_tenant(db)
    preview = review(actions)
    assert preview["amount_minor"] == 1299 and preview["currency"] == "usd"
    assert preview["currency_exponent"] == 2 and preview["livemode"] is False
    assert preview["confirmation"] == "REFUND usr-refund 1299 USD MINOR UNITS"
    assert not db.audit_items() and not provider.posts
    result = actions.apply(**request(preview))
    assert result["status"] == "verified" and result["integrity_status"] == "signed_verified"
    assert result["outcome"]["provider_status"] == "succeeded"
    assert result["intent"]["before"] == 0 and result["intent"]["after"] == 1299
    assert result["tenant_mutated"] is False and result["provider_state_refreshed"] is False
    assert stored_tenant(db) == before
    assert len(provider.posts) == 1
    assert provider.calls[-1].full_url.endswith("/v1/charges/ch_refund")
    assert {v["kind"] for v in verified_refund_rows(db)} == {"control", "intent", "outcome"}
    encoded = json.dumps([preview, result])
    for private in ("acct_owner", "cus_refund", "sub_refund", "in_refund", "ch_refund",
                    "pi_refund", "price_plan", "re_refund", "rk_test_"):
        assert private not in encoded


def test_exact_replay_never_reads_provider_or_writes_again_even_after_preview_expires():
    now = [NOW]
    actions, db, provider = service(clock=lambda:now[0])
    args = request(review(actions))
    first = actions.apply(**args)
    counts = len(provider.calls), db.transactions, copy.deepcopy(db.audit_items())
    now[0] += 1000
    assert actions.apply(**args) == first
    assert (len(provider.calls), db.transactions, db.audit_items()) == counts
    with pytest.raises(RefundConflict):
        actions.apply(**{**args, "reason":"Changed purpose cannot reuse this reference"})
    assert len(provider.posts) == 1


@pytest.mark.parametrize("status,terminal", [("pending", "succeeded"),
    ("requires_action", "failed"), ("pending", "canceled")])
def test_pending_receipt_changes_only_on_explicit_get_refresh(status, terminal):
    actions, db, provider = service()
    provider.status = status
    initial = actions.apply(**request(review(actions)))
    assert initial["status"] == "pending"
    original = copy.deepcopy(db.audit_items())
    calls = len(provider.calls)
    provider.status = terminal
    assert receipt(actions) == initial and len(provider.calls) == calls
    result = refresh(actions)
    assert result["status"] == ("verified" if terminal == "succeeded" else "failed")
    assert result["provider_state_refreshed"] is True and len(provider.posts) == 1
    assert all(call.get_method() == "GET" for call in provider.calls[calls:])
    assert len(db.audit_items()) == len(original) + 1
    calls = len(provider.calls)
    final = refresh(actions)
    assert final["status"] == result["status"] and final["provider_state_refreshed"] is False
    assert len(provider.calls) == calls and len(provider.posts) == 1


def test_repeated_pending_observation_preserves_original_record_and_capacity():
    actions, db, provider = service()
    provider.status = "pending"
    first = actions.apply(**request(review(actions)))
    original = copy.deepcopy(db.audit_items())
    provider.status = "requires_action"
    result = refresh(actions)
    assert result["status"] == "pending" and result["outcome"] == first["outcome"]
    assert db.audit_items() == original
    provider.status = "succeeded"
    assert refresh(actions)["status"] == "verified" and len(provider.posts) == 1


def test_lost_post_response_is_recovered_with_gets_and_never_reissued():
    actions, db, provider = service()
    args = request(review(actions))
    provider.lose_post_response = True
    first = actions.apply(**args)
    assert first["status"] == "indeterminate" and first["outcome"]["provider_status"] is None
    assert len(provider.posts) == 1
    calls = len(provider.calls)
    assert actions.apply(**args) == first and len(provider.calls) == calls
    recovered = refresh(actions)
    assert recovered["status"] == "verified" and recovered["provider_state_refreshed"] is True
    assert len(provider.posts) == 1
    assert all(call.get_method() == "GET" for call in provider.calls[calls:])
    assert {r["kind"] for r in verified_refund_rows(db)} == {"control", "intent", "outcome", "observation"}


@pytest.mark.parametrize("corruption", ["metadata", "account", "amount", "charge", "mode", "multiple"])
def test_lost_response_recovery_rejects_unbound_or_ambiguous_provider_evidence(corruption):
    actions, db, provider = service()
    provider.lose_post_response = True
    first = actions.apply(**request(review(actions)))
    original = copy.deepcopy(db.audit_items())
    if corruption == "metadata":
        provider.refund["metadata"]["guardoath_action"] = "a" * 64
        provider.refunds = [copy.deepcopy(provider.refund)]
    elif corruption == "account":
        provider.account["id"] = "acct_other"
    elif corruption == "amount":
        provider.refund["amount"] -= 1
        provider.refunds = [copy.deepcopy(provider.refund)]
    elif corruption == "charge":
        provider.refund["charge"] = "ch_other"
        provider.refunds = [copy.deepcopy(provider.refund)]
    elif corruption == "mode":
        provider.charge["livemode"] = True
    else:
        provider.refunds.append({**provider.refund, "id":"re_other"})
    with pytest.raises(RefundUnavailable):
        refresh(actions)
    assert receipt(actions) == first and db.audit_items() == original
    assert len(provider.posts) == 1


def test_two_owner_requests_racing_for_the_same_charge_dispatch_only_once(tmp_path):
    path = tmp_path / "refund-race.sqlite3"
    first, db1, provider = service(AdminTestDynamo(path))
    second, db2, _ = service(AdminTestDynamo(path), boundary=provider, seed=False)
    args1 = request(review(first))
    args2 = request(review(second, actor=OTHER), actor=OTHER, key="other-refund-reference-12345")
    barrier = Barrier(2)
    for action in (first, second):
        original = action.state.reserve
        def reserve(*args, _original=original, **kwargs):
            barrier.wait(timeout=3)
            return _original(*args, **kwargs)
        action.state.reserve = reserve
    def apply(action, args):
        try:
            return action.apply(**args)["status"]
        except RefundConflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(apply, first, args1), executor.submit(apply, second, args2)]
        statuses = sorted(f.result(timeout=5) for f in futures)
    assert statuses == ["conflict", "verified"] and len(provider.posts) == 1
    assert len(verified_refund_rows(db1)) == 3
    db1.close()
    db2.close()


@pytest.mark.parametrize("change", ["revision", "document", "confirmation", "owner", "expiry", "provider"])
def test_stale_or_changed_review_never_claims_or_dispatches(change):
    now = [NOW]
    actions, db, provider = service(clock=lambda:now[0])
    args = request(review(actions))
    if change in {"revision", "document"}:
        item = stored_tenant(db)
        value = json.loads(item["record"]["S"])
        if change == "revision":
            value["revision"] = 4
            item["revision"] = {"N":"4"}
        else:
            value["monthly_allowance"] += 1
        item["record"]["S"] = json.dumps(value)
        db.seed("tenant_table", item)
    elif change == "confirmation":
        args["confirmation"] = "REFUND usr-refund 12 USD MINOR UNITS"
    elif change == "owner":
        args["actor"] = OTHER
    elif change == "expiry":
        now[0] += actions.PREVIEW_SECONDS
    else:
        provider.charge["disputed"] = True
    with pytest.raises((RefundConflict, RefundUnavailable)):
        actions.apply(**args)
    assert not provider.posts and not db.audit_items()


def test_exact_tenant_document_race_after_claim_blocks_dispatch_and_retains_claim():
    actions, db, provider = service()
    args = request(review(actions))
    original = db.transact_write_items
    def transact(*, TransactItems):
        if db.transactions == 1:
            item = stored_tenant(db)
            value = json.loads(item["record"]["S"])
            value["usage"]["2026-11"] += 1
            item["record"]["S"] = json.dumps(value)
            db.seed("tenant_table", item)
        return original(TransactItems=TransactItems)
    db.transact_write_items = transact
    result = actions.apply(**args)
    assert result["status"] == "indeterminate" and not provider.posts
    assert actions.apply(**args) == result and not provider.posts
    controls = [r for r in verified_refund_rows(db) if r["kind"] == "control"]
    assert len(controls) == 1 and controls[0]["dispatch_started"] is False


@pytest.mark.parametrize("lost_transaction,posts,status", [(1,0,"indeterminate"),
    (2,0,"indeterminate"), (3,1,"verified")])
def test_transaction_ack_loss_never_recreates_dispatch_authority(lost_transaction, posts, status):
    actions, db, provider = service()
    args = request(review(actions))
    original = db.transact_write_items
    def transact(*, TransactItems):
        result = original(TransactItems=TransactItems)
        if db.transactions == lost_transaction:
            raise TimeoutError("synthetic committed acknowledgement loss")
        return result
    db.transact_write_items = transact
    if lost_transaction == 1:
        with pytest.raises(RefundUnavailable):
            actions.apply(**args)
    else:
        assert actions.apply(**args)["status"] == status
    assert receipt(actions)["status"] == status and len(provider.posts) == posts
    assert actions.apply(**args)["status"] == status and len(provider.posts) == posts
    verified_refund_rows(db)


def test_post_succeeded_but_outcome_write_outage_recovers_without_second_post():
    actions, db, provider = service()
    args = request(review(actions))
    def after(call, value):
        if call.get_method() == "POST":
            db.fail_transaction = True
        return value
    provider.after = after
    result = actions.apply(**args)
    assert result["status"] == "indeterminate" and result["outcome"] is None
    assert len(provider.posts) == 1
    db.fail_transaction = False
    assert refresh(actions)["status"] == "verified" and len(provider.posts) == 1


def test_reader_reopens_signed_history_without_provider_or_writer(tmp_path, monkeypatch):
    path = tmp_path / "refund-reader.sqlite3"
    actions, db, provider = service(AdminTestDynamo(path))
    args = request(review(actions))
    expected = actions.apply(**args)
    db.close()
    reopened = AdminTestDynamo(path)
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_REFUND_STATE", False)
    reader = AdminInvoiceRefund(state=DynamoRefundState(reopened,"audit_table",KEY),
        tenant_table="tenant_table", preview_key=KEY)
    assert receipt(reader) == expected
    with pytest.raises(RefundUnavailable):
        reader.apply(**args)
    with pytest.raises(RefundUnavailable):
        refresh(reader)
    assert len(provider.posts) == 1
    reopened.close()


def test_historical_refresh_survives_current_price_and_tenant_revision_change():
    actions, db, provider = service()
    provider.status = "pending"
    args = request(review(actions))
    assert actions.apply(**args)["status"] == "pending"
    item = stored_tenant(db)
    value = json.loads(item["record"]["S"])
    value["revision"] = 4
    item.update(revision={"N":"4"}, record={"S":json.dumps(value)})
    db.seed("tenant_table", item)
    updated_provider = StripeInvoiceRefund(api_key="rk_test_synthetic_refund_0123456789",
        expected_account_id="acct_owner", expected_livemode=False,
        expected_price_id="price_newplan", opener=provider.open)
    updated = AdminInvoiceRefund(state=DynamoRefundState(db,"audit_table",KEY),
        tenant_table="tenant_table", preview_key=KEY, provider=updated_provider,
        invoice_references=InvoiceRefundReferences(KEY, expected_price_id="price_newplan",
            expected_livemode=False), clock=lambda:NOW)
    provider.status = "succeeded"
    assert refresh(updated)["status"] == "verified" and len(provider.posts) == 1
    assert stored_tenant(db) == item
    intent, outcome = updated.state.history(updated.action_id(ACTOR,TENANT,args["idempotency_key"]))
    assert intent["expected_price_id"] == outcome["proof"]["expected_price_id"] == "price_plan"


@pytest.mark.parametrize("operation", ["preview", "apply", "receipt", "refresh"])
def test_invalid_actor_rejected_before_store_or_provider_io(operation):
    actions, db, provider = service()
    args = request(review(actions))
    calls = len(provider.calls)
    def forbidden(**kwargs):
        raise AssertionError("invalid identity reached storage")
    db.get_item = forbidden
    with pytest.raises(ValueError):
        if operation == "preview":
            actions.preview(actor="invalid owner\n", tenant_id=TENANT, expected_revision=3,
                invoice_reference="ir1." + "a" * 40)
        elif operation == "apply":
            actions.apply(**{**args,"actor":"invalid owner\n"})
        elif operation == "receipt":
            receipt(actions, actor="invalid owner\n")
        else:
            refresh(actions, actor="invalid owner\n")
    assert len(provider.calls) == calls and not db.audit_items()


@pytest.mark.parametrize("budget", [0, -1, 0.5, 3.9, 9, True, float("nan")])
def test_insufficient_or_invalid_budget_cannot_claim_or_dispatch(budget):
    actions, db, provider = service()
    args = request(review(actions))
    calls = len(provider.calls)
    with pytest.raises(RefundUnavailable):
        actions.apply(**args, execution_budget_seconds=budget)
    assert len(provider.calls) == calls and not db.audit_items()


def test_preview_rate_limit_blocks_provider_reads_without_financial_claims():
    actions, db, provider = service()
    reference = invoice_reference(actions)
    for _ in range(20):
        actions.preview(actor=ACTOR, tenant_id=TENANT, expected_revision=3, invoice_reference=reference)
    calls = len(provider.calls)
    with pytest.raises(RefundRateLimited):
        actions.preview(actor=ACTOR, tenant_id=TENANT, expected_revision=3, invoice_reference=reference)
    assert len(provider.calls) == calls and not db.audit_items()


def test_missing_history_refresh_performs_no_provider_request_or_state_write():
    actions, db, provider = service()
    assert refresh(actions)["status"] == "not_found"
    assert not provider.calls and not db.audit_items()
