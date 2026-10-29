"""No-charge settlement routing tests."""
import pytest

from guardoath.billing import BillingAccount, BillingMode, usage_event
from guardoath.billing_router import settlement_record


@pytest.mark.parametrize("mode,channel", [
    (BillingMode.STRIPE, "stripe_meter_pending"),
    (BillingMode.PARTNER, "partner_settlement_pending"),
    (BillingMode.INTERNAL, "internal_usage_only"),
])
def test_billing_channel_routing(mode, channel):
    event = usage_event(BillingAccount("tenant-a", mode, "payer-a"),
                        check_id="check-1", units=3)
    routed = settlement_record(event)
    assert routed.channel == channel
    assert routed.units == 3
    assert routed.tenant_id == "tenant-a"
    assert routed.event_id == event.event_id
