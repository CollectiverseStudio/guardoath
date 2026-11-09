"""Verified subscription updates must be tenant-bound, revisioned and fail closed."""
import pytest

from guardoath.billing import BillingMode
from guardoath.tenant_record import TenantRecord
from guardoath.tenant_store import MemoryTenantStore, TenantConflict


def record(**changes):
    return TenantRecord(tenant_id="acme", display_name="ACME",
                        input_check_text_security="block", output_check_text_security="block",
                        billing_mode=BillingMode.STRIPE, payer_id="cus_acme",
                        subscription_status="inactive", **changes)


def provider(status="active", customer="cus_acme", subscription="sub_acme"):
    return {"object": "subscription", "id": subscription, "customer": customer, "status": status}


def test_verified_subscription_updates_authoritative_record():
    store = MemoryTenantStore((record(),))
    first = store.get("acme")
    updated = store.apply_verified_subscription("acme", customer_id="cus_acme",
        subscription_id="sub_acme", provider_subscription=provider(),
        expected_revision=first.revision)
    assert updated.subscription_status == "active"
    assert updated.revision == first.revision + 1
    assert store.get("acme").subscription_status == "active"
    assert store.get("acme").key_hashes == ()


def test_provider_cancellation_revokes_access():
    store = MemoryTenantStore((record(),))
    first = store.get("acme")
    active = store.apply_verified_subscription("acme", customer_id="cus_acme",
        subscription_id="sub_acme", provider_subscription=provider(),
        expected_revision=first.revision)
    canceled = store.apply_verified_subscription("acme", customer_id="cus_acme",
        subscription_id="sub_acme", provider_subscription=provider("canceled"),
        expected_revision=active.revision)
    assert canceled.subscription_status == "canceled"


def test_wrong_customer_or_provider_subscription_cannot_activate():
    store = MemoryTenantStore((record(),))
    revision = store.get("acme").revision
    for customer, snapshot in [
        ("cus_other", provider()),
        ("cus_acme", provider(customer="cus_other")),
        ("cus_acme", provider(subscription="sub_other")),
    ]:
        with pytest.raises(ValueError):
            store.apply_verified_subscription("acme", customer_id=customer,
                subscription_id="sub_acme", provider_subscription=snapshot,
                expected_revision=revision)
        assert store.get("acme").subscription_status == "inactive"
        assert store.get("acme").revision == revision


def test_stale_revision_cannot_overwrite_newer_subscription_status():
    store = MemoryTenantStore((record(),))
    original = store.get("acme")
    store.apply_verified_subscription("acme", customer_id="cus_acme",
        subscription_id="sub_acme", provider_subscription=provider("canceled"),
        expected_revision=original.revision)
    with pytest.raises(TenantConflict):
        store.apply_verified_subscription("acme", customer_id="cus_acme",
            subscription_id="sub_acme", provider_subscription=provider(),
            expected_revision=original.revision)
    assert store.get("acme").subscription_status == "canceled"
