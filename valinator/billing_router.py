"""Stripe/partner billing routing contracts; no payment or network calls."""
from dataclasses import dataclass

from valinator.billing import BillingMode, UsageEvent


@dataclass(frozen=True)
class SettlementRecord:
    event_id: str
    tenant_id: str
    payer_id: str
    units: int
    channel: str


def settlement_record(event: UsageEvent) -> SettlementRecord:
    if event.mode == BillingMode.STRIPE:
        channel = "stripe_meter_pending"
    elif event.mode == BillingMode.PARTNER:
        channel = "partner_settlement_pending"
    else:
        channel = "internal_usage_only"
    return SettlementRecord(event_id=event.event_id, tenant_id=event.tenant_id,
                            payer_id=event.payer_id, units=event.units,
                            channel=channel)
