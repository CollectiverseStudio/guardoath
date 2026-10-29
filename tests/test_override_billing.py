"""Tenant override decisions and provider-neutral billing events."""
import pytest

from guardoath.override_policy import OverridePolicy, decide_override
from guardoath.billing import BillingAccount, BillingMode, usage_event


def test_two_tenants_have_independent_override_policies():
    permissive = OverridePolicy("hospital-a", frozenset({"privacy.possible_phi"}),
                                frozenset({"privacy.possible_phi"}))
    restrictive = OverridePolicy("company-b", frozenset())
    args = dict(finding_ids=("privacy.possible_phi",), approved_by_reviewer=True,
                assessment_complete=True)
    assert decide_override(policy=permissive, tenant_id="hospital-a", **args).allowed
    assert not decide_override(policy=restrictive, tenant_id="company-b", **args).allowed
    assert not decide_override(policy=permissive, tenant_id="company-b", **args).allowed


def test_review_required_and_other_findings_cannot_be_silently_ignored():
    policy = OverridePolicy("hospital-a", frozenset({"privacy.possible_phi"}),
                            frozenset({"privacy.possible_phi"}))
    def decide(findings, reviewed=False, complete=True):
        return decide_override(policy=policy, tenant_id="hospital-a",
                               finding_ids=findings, approved_by_reviewer=reviewed,
                               assessment_complete=complete)
    assert decide(("privacy.possible_phi",)).reason == "reviewer_approval_required"
    assert decide(("privacy.possible_phi", "actions.unknown_tool"), True).reason == "rule_not_overrideable"
    assert decide(("privacy.possible_phi",), True, False).reason == "incomplete_assessment"
    assert decide(("privacy.possible_phi",), True).allowed


@pytest.mark.parametrize("mode,payer", [
    (BillingMode.STRIPE, "cus_example"),
    (BillingMode.PARTNER, "tokatlas"),
    (BillingMode.INTERNAL, "internal-account"),
])
def test_usage_events_support_distinct_payers(mode, payer):
    account = BillingAccount("tenant-a", mode, payer)
    a = usage_event(account, check_id="check-1")
    b = usage_event(account, check_id="check-1")
    c = usage_event(account, check_id="check-2")
    assert a == b
    assert a.event_id != c.event_id
    assert a.mode == mode and a.payer_id == payer


def test_usage_event_is_tenant_scoped_and_bounded():
    a = usage_event(BillingAccount("a", BillingMode.STRIPE, "cus_a"), check_id="same")
    b = usage_event(BillingAccount("b", BillingMode.STRIPE, "cus_b"), check_id="same")
    assert a.event_id != b.event_id
    with pytest.raises(ValueError):
        usage_event(BillingAccount("a", BillingMode.STRIPE, "cus_a"),
                    check_id="same", units=0)
