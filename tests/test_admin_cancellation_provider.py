"""Fixed-host credential, strict JSON and supported-state boundaries."""
import json
import urllib.request

import pytest

from admin_cancellation_support import NOW, Response, snapshot
from guardoath.admin_cancellation_provider import CancellationProviderUnavailable, StripePeriodEndCancellation
from guardoath.subscription_reconciliation import StripeBinding

BINDING = StripeBinding("usr-cancel","cus_cancel","sub_cancel")
CONFIG = dict(api_key="rk_test_synthetic_cancel_0123456789",expected_account_id="acct_owner",
    expected_price_id="price_plan",expected_livemode=False)


@pytest.mark.parametrize("key", ["pk_test_01234567890123", "whsec_01234567890123", "rk_live_01234567890123",
    "sk_live_01234567890123", "rk_test_short", "rk_test_01234567890123\n", "rk_test_01234567 bad"])
def test_explicit_cancellation_credential_validation(key):
    with pytest.raises(ValueError): StripePeriodEndCancellation(**{**CONFIG,"api_key":key})


@pytest.mark.parametrize("key", ["rk_test_01234567890123","sk_test_01234567890123"])
def test_narrow_valid_key_support_does_not_change_existing_reporter(key):
    assert StripePeriodEndCancellation(**{**CONFIG,"api_key":key}).livemode is False
    from guardoath.stripe_provider import fetch_subscription_snapshot
    from guardoath.subscription_reconciliation import SubscriptionReconciliationError
    if key.startswith("rk_"):
        with pytest.raises(SubscriptionReconciliationError): fetch_subscription_snapshot(BINDING,key)


@pytest.mark.parametrize("raw", [b'{"object":"account","object":"account","id":"acct_owner"}',
    b'{"value":NaN}', b'{"value":1e999}', b'[]', b'null', b'\xff', b'{', b' ' * 262145])
def test_strict_bounded_provider_json_is_required(raw):
    calls = []
    def opened(req,**_kw): calls.append(req); return Response(raw)
    provider = StripePeriodEndCancellation(**CONFIG,opener=opened)
    with pytest.raises(CancellationProviderUnavailable): provider.inspect(BINDING,scheduled=False,now=NOW)
    assert len(calls) == 1


@pytest.mark.parametrize("status", [201,301,302,307,400,401,429,500])
def test_unexpected_provider_status_never_counts_as_verification(status):
    def opened(req,**_kw):
        result = Response(json.dumps({"object":"account","id":"acct_owner"}).encode())
        result.status = status
        return result
    provider = StripePeriodEndCancellation(**CONFIG,opener=opened)
    with pytest.raises(CancellationProviderUnavailable): provider.inspect(BINDING,scheduled=False,now=NOW)


def test_default_transport_installs_redirect_refusal(monkeypatch):
    handlers = []
    def builder(handler):
        handlers.append(handler)
        return type("Opener",(),{"open":lambda *_a,**_k:None})()
    monkeypatch.setattr(urllib.request,"build_opener",builder)
    StripePeriodEndCancellation(**CONFIG)
    assert handlers[0].redirect_request(None,None,302,"redirect",{},"https://foreign.invalid") is None


@pytest.mark.parametrize("change", ["missing_flag","flag_false","custom_date","date_bool","item","period"])
def test_ambiguous_update_result_is_not_success(change):
    provider = StripePeriodEndCancellation(**CONFIG)
    value = snapshot(); value["cancel_at_period_end"] = True
    if change == "missing_flag": del value["cancel_at_period_end"]
    if change == "flag_false": value["cancel_at_period_end"] = False
    if change == "custom_date": value["cancel_at"] = NOW + 1
    if change == "date_bool": value["cancel_at"] = True
    if change == "item": value["items"]["data"][0]["subscription"] = "sub_other"
    if change == "period": value["current_period_end"] = NOW
    with pytest.raises(CancellationProviderUnavailable): provider.validate(value,BINDING,scheduled=True,now=NOW)


@pytest.mark.parametrize("cancel_at", [None,NOW+86400*10])
def test_confirmed_schedule_accepts_null_or_exact_boundary_and_matching_optional_item_periods(cancel_at):
    provider = StripePeriodEndCancellation(**CONFIG)
    value = snapshot(); value["cancel_at_period_end"] = True; value["cancel_at"] = cancel_at
    item = value["items"]["data"][0]
    item["current_period_start"] = value["current_period_start"]
    item["current_period_end"] = value["current_period_end"]
    assert provider.validate(value,BINDING,scheduled=True,now=NOW)["period_end"] == value["current_period_end"]
