"""Provider-neutral tenant usage metering for Stripe or partner settlement.

This is an accounting event builder, not a payment processor or durable ledger.
"""
from dataclasses import dataclass
from enum import StrEnum
import hashlib


class BillingMode(StrEnum):
    STRIPE = "stripe"
    PARTNER = "partner"
    INTERNAL = "internal"


@dataclass(frozen=True)
class BillingAccount:
    tenant_id: str
    mode: BillingMode
    payer_id: str

    def __post_init__(self):
        if not self.tenant_id or not self.payer_id:
            raise ValueError("Billing account requires tenant and payer")


@dataclass(frozen=True)
class UsageEvent:
    event_id: str
    tenant_id: str
    payer_id: str
    mode: BillingMode
    units: int
    event_type: str


def usage_event(account: BillingAccount, *, check_id: str, units: int = 1,
                event_type: str = "validation_request") -> UsageEvent:
    if not check_id or type(units) is not int or not 1 <= units <= 100000:
        raise ValueError("Invalid usage event")
    if event_type not in {"validation_request", "override_review"}:
        raise ValueError("Unsupported usage event type")
    digest = hashlib.sha256(
        (account.tenant_id + "\x00" + check_id + "\x00" + event_type).encode()
    ).hexdigest()
    return UsageEvent(
        event_id=digest, tenant_id=account.tenant_id,
        payer_id=account.payer_id, mode=account.mode,
        units=units, event_type=event_type,
    )
