"""The actual baseline reads future signed history but cannot introduce it."""
import pytest

from admin_cancellation_support import ACTOR, TENANT, request, review, service
from guardoath import state_contracts
from guardoath.admin_cancellation_state import CONTRACT, CancellationStateUnavailable
from guardoath.admin_cancellation_provider import CancellationProviderUnavailable
from guardoath.admin_subscription_cancellation import CancellationUnavailable
from guardoath.subscription_reconciliation import StripeBinding
from test_admin_cancellation_api import client, HEADERS, PREFIX


def test_source_baseline_cannot_be_enabled_through_environment(monkeypatch):
    assert state_contracts.ENABLE_NEW_CANCELLATION_STATE is False
    for key in ("ENABLE_NEW_CANCELLATION_STATE","GUARDOATH_ENABLE_NEW_CANCELLATION_STATE",
                "GUARDOATH_ADMIN_TEST_WRITER_PHASE","GUARDOATH_CANCELLATION_WRITER_ENABLED"):
        monkeypatch.setenv(key,"1")
    assert state_contracts.new_cancellation_state_enabled() is False
    assert state_contracts.release_metadata("a"*40)["introduced_state_contracts"] == []


@pytest.mark.parametrize("caps,cancellation", [(False,False),(True,False),(False,True),(True,True)])
def test_writer_contract_introductions_are_independent(monkeypatch,caps,cancellation):
    monkeypatch.setattr(state_contracts,"ENABLE_NEW_SUBSCRIPTION_STATE",caps)
    monkeypatch.setattr(state_contracts,"ENABLE_NEW_CANCELLATION_STATE",cancellation)
    metadata = state_contracts.release_metadata("a"*40)
    assert metadata["supported_state_contracts"] == ["subscription-cap-provenance-v1",CONTRACT,
                                                       "catalog-publication-v1"]
    assert metadata["introduced_state_contracts"] == (["subscription-cap-provenance-v1"] if caps else []) + ([CONTRACT] if cancellation else [])


@pytest.mark.parametrize("invalid", [None,0,1,"true"])
def test_writer_source_gate_requires_actual_boolean(monkeypatch,invalid):
    monkeypatch.setattr(state_contracts,"ENABLE_NEW_CANCELLATION_STATE",invalid)
    with pytest.raises(ValueError): state_contracts.new_cancellation_state_enabled()
    with pytest.raises(ValueError): state_contracts.release_metadata("a"*40)


def test_baseline_preview_is_read_only_and_direct_injected_writer_is_unmounted():
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


def test_baseline_real_reader_verifies_future_records_without_credentials_and_denies_state_writers(monkeypatch):
    actions, db, boundary = service()
    with monkeypatch.context() as writer:
        writer.setattr(state_contracts,"ENABLE_NEW_CANCELLATION_STATE",True)
        payload = request(review(actions))
        result = actions.apply(**payload)
    state = actions.state
    intent, outcome = state.history(result["intent"]["action_id"])
    control = state.read("control",intent["target_id"])
    before = db.audit_items()
    count = len(boundary.calls)
    actions.provider = None
    browser = client(actions,db)
    history = browser.post(PREFIX+"/cancellation/receipt",headers=HEADERS,json={"idempotency_key":payload["idempotency_key"]})
    assert history.status_code == 200 and history.json()["cancellation"] == result
    for entry in (lambda:state.reserve({},expected_control_hash="a"*64),
        lambda:state.dispatching(control,tenant_table="tenant_table",tenant_id=TENANT,expected_revision=3,original_document="{}"),
        lambda:state.outcome(intent,"verified",intent["created_at"]), lambda:state.finish(control,verified=True)):
        with pytest.raises(CancellationStateUnavailable): entry()
    assert len(boundary.calls) == count
    assert [r for r in db.audit_items() if "financial_operation" in r] == before
