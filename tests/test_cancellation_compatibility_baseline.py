"""Source activation writes signed history that the disabled reader preserves."""
import pytest

from admin_cancellation_support import ACTOR, TENANT, NOW, KEY, request, review, service
from admin_test_support import AdminTestDynamo
from guardoath import state_contracts
from guardoath.admin_cancellation_state import CONTRACT, CancellationStateUnavailable, DynamoCancellationState
from guardoath.admin_cancellation_provider import CancellationProviderUnavailable
from guardoath.admin_subscription_cancellation import AdminSubscriptionCancellation, CancellationUnavailable
from guardoath.subscription_reconciliation import StripeBinding
from test_admin_cancellation_api import client, HEADERS, PREFIX


@pytest.fixture
def financial_reader_phase(monkeypatch):
    """Model the already-deployed reader without an application override."""
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_SUBSCRIPTION_STATE", False)
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_CANCELLATION_STATE", False)
    monkeypatch.setattr(state_contracts, "ENABLE_NEW_REFUND_STATE", False)


def test_explicit_reader_phase_cannot_be_enabled_through_environment(monkeypatch, financial_reader_phase):
    assert state_contracts.ENABLE_NEW_CANCELLATION_STATE is False
    for key in ("ENABLE_NEW_CANCELLATION_STATE","GUARDOATH_ENABLE_NEW_CANCELLATION_STATE",
                "GUARDOATH_ADMIN_TEST_WRITER_PHASE","GUARDOATH_CANCELLATION_WRITER_ENABLED"):
        monkeypatch.setenv(key,"1")
    assert state_contracts.new_cancellation_state_enabled() is False
    assert state_contracts.release_metadata("a"*40)["introduced_state_contracts"] == [
        "catalog-publication-v1", "catalog-publication-lifecycle-v1"]


def test_source_default_cancellation_remains_enabled_despite_environment(monkeypatch):
    for key in ("ENABLE_NEW_CANCELLATION_STATE", "GUARDOATH_ENABLE_NEW_CANCELLATION_STATE",
                "GUARDOATH_ADMIN_TEST_WRITER_PHASE", "GUARDOATH_CANCELLATION_WRITER_ENABLED"):
        monkeypatch.setenv(key, "0")
    assert state_contracts.ENABLE_NEW_CANCELLATION_STATE is True
    assert state_contracts.new_cancellation_state_enabled() is True
    assert state_contracts.release_metadata("a" * 40)["introduced_state_contracts"] == [
        "subscription-cap-provenance-v1", CONTRACT,
        "catalog-publication-v1", "catalog-publication-lifecycle-v1", "stripe-invoice-refund-v1"]


@pytest.mark.parametrize("caps,cancellation", [(False,False),(True,False),(False,True),(True,True)])
def test_writer_contract_introductions_are_independent(monkeypatch,caps,cancellation):
    monkeypatch.setattr(state_contracts,"ENABLE_NEW_SUBSCRIPTION_STATE",caps)
    monkeypatch.setattr(state_contracts,"ENABLE_NEW_CANCELLATION_STATE",cancellation)
    metadata = state_contracts.release_metadata("a"*40)
    assert metadata["supported_state_contracts"] == ["subscription-cap-provenance-v1",CONTRACT,
        "catalog-publication-v1", "catalog-publication-lifecycle-v1", "stripe-invoice-refund-v1", "subscription-cap-restoration-v1"]
    assert metadata["introduced_state_contracts"] == (
        (["subscription-cap-provenance-v1"] if caps else []) + ([CONTRACT] if cancellation else [])
        + ["catalog-publication-v1", "catalog-publication-lifecycle-v1", "stripe-invoice-refund-v1"])


@pytest.mark.parametrize("invalid", [None,0,1,"true"])
def test_writer_source_gate_requires_actual_boolean(monkeypatch,invalid):
    monkeypatch.setattr(state_contracts,"ENABLE_NEW_CANCELLATION_STATE",invalid)
    with pytest.raises(ValueError): state_contracts.new_cancellation_state_enabled()
    with pytest.raises(ValueError): state_contracts.release_metadata("a"*40)


def test_baseline_preview_is_read_only_and_direct_injected_writer_is_unmounted(financial_reader_phase):
    actions, db, boundary = service()
    browser = client(actions,db)
    features = browser.get("/v1/admin/features",headers=HEADERS).json()
    assert features["billing_cancellation_preview"] is features["billing_cancellation_receipts"] is True
    assert features["billing_cancellation_schedule"] is False
    response = browser.post(PREFIX+"/cancellation/preview",headers=HEADERS,json={"expected_revision":3})
    assert response.status_code == 200 and response.json()["cancellation"]["scheduling_available"] is False
    denied = browser.post(PREFIX+"/cancel-at-period-end",headers=HEADERS,json={})
    assert denied.status_code == 404
    with pytest.raises(CancellationUnavailable): actions.apply(**request(response.json()["cancellation"]))
    with pytest.raises(CancellationProviderUnavailable):
        actions.provider.dispatch(StripeBinding(TENANT,"cus_cancel","sub_cancel"),idempotency_key="guardoath-sc1-"+"a"*64,now=1795500000)
    assert not boundary.posts and all("financial_operation" not in row for row in db.audit_items())


@pytest.mark.parametrize("lost_response", [False, True], ids=["verified", "indeterminate"])
def test_source_default_history_reopens_after_reader_rollback_without_credentials(tmp_path, monkeypatch, lost_response):
    assert state_contracts.new_cancellation_state_enabled() is True
    path = tmp_path / "cancellation-rollback.sqlite3"
    db = AdminTestDynamo(path)
    try:
        actions, _, boundary = service(db)
        original_tenant = db.get_item(TableName="tenant_table", Key={"tenant_id":{"S":TENANT}})
        payload = request(review(actions))
        boundary.lose_post_response = lost_response
        result = actions.apply(**payload)
        assert result["status"] == ("indeterminate" if lost_response else "verified")
        assert result["integrity_status"] == "signed_verified"
        assert result["intent"]["intent_hash"] == result["outcome"]["intent_hash"]
        assert result["tenant_mutated"] is result["provider_state_refreshed"] is False
        assert len(boundary.posts) == 1
        assert db.get_item(TableName="tenant_table", Key={"tenant_id":{"S":TENANT}}) == original_tenant
        before = db.audit_items()
        count = len(boundary.calls)
        assert actions.apply(**payload) == result and len(boundary.calls) == count
        monkeypatch.setattr(state_contracts, "ENABLE_NEW_SUBSCRIPTION_STATE", False)
        monkeypatch.setattr(state_contracts, "ENABLE_NEW_CANCELLATION_STATE", False)
        reopened = AdminTestDynamo(path)
        try:
            state = DynamoCancellationState(reopened, "audit_table", KEY)
            reader = AdminSubscriptionCancellation(state=state, tenant_table="tenant_table",
                preview_key=KEY, clock=lambda: NOW)
            assert reader.provider is None
            intent, outcome = state.history(result["intent"]["action_id"])
            assert outcome["status"] == result["status"]
            control = state.read("control", intent["target_id"])
            assert control["state"] == ("BLOCKED" if lost_response else "VERIFIED")
            with client(reader, reopened) as browser:
                history = browser.post(PREFIX+"/cancellation/receipt", headers=HEADERS,
                    json={"idempotency_key":payload["idempotency_key"]})
            assert history.status_code == 200 and history.json()["cancellation"] == result
            with pytest.raises(CancellationUnavailable):
                reader.apply(**payload)
            for entry in (lambda:state.reserve({},expected_control_hash="a"*64),
                lambda:state.dispatching(control,tenant_table="tenant_table",tenant_id=TENANT,expected_revision=3,original_document="{}"),
                lambda:state.outcome(intent,"verified",intent["created_at"]), lambda:state.finish(control,verified=True)):
                with pytest.raises(CancellationStateUnavailable): entry()
            with pytest.raises(CancellationProviderUnavailable):
                actions.provider.dispatch(StripeBinding(TENANT, "cus_cancel", "sub_cancel"),
                    idempotency_key="guardoath-sc1-"+"a"*64, now=NOW)
            assert len(boundary.calls) == count
            assert [r for r in reopened.audit_items() if "financial_operation" in r] == before
            assert reopened.get_item(TableName="tenant_table", Key={"tenant_id":{"S":TENANT}}) == original_tenant
        finally:
            reopened.close()
    finally:
        db.close()
