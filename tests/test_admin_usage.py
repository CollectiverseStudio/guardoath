"""Admin usage is authoritative and never returns fake zeroes or credentials."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from guardoath.admin_console import VerifiedAdmin, create_admin_router
from guardoath.admin_usage import UsageUnavailable, tenant_usage_report


@dataclass
class Tenant:
    tenant_id: str = "usr-usage"
    display_name: str = "A tenant"
    status: str = "active"
    demo: bool = False
    revision: int = 7
    monthly_allowance: int = 1000
    overage: str = "block"
    usage: dict = field(default_factory=lambda: {"2026-11": 41, "2026-10": 123})
    updated_at: str = "2026-11-19T17:00:00Z"
    subscription_status: str = "active"


NOW = lambda: datetime(2026, 11, 19, tzinfo=timezone.utc)


def test_authoritative_usage_and_bounded_history_with_missing_is_unknown():
    report = tenant_usage_report(Tenant(), clock=NOW)
    assert report["requests_recorded"] == 41
    assert report["remaining_before_allowance"] == 959
    assert report["blocking_cap_enabled"] is True
    assert report["recent_recorded_months"] == [
        {"month_utc": "2026-11", "requests": 41}, {"month_utc": "2026-10", "requests": 123}]
    assert report["aws_cost_usd"] is None and report["stripe_invoice_usd"] is None
    missing = tenant_usage_report(Tenant(usage={}), clock=NOW)
    assert missing["requests_recorded"] is None
    assert missing["remaining_before_allowance"] is None
    assert not missing["recent_recorded_months"]


def test_overage_policy_cannot_be_misreported_as_hard_cap():
    result = tenant_usage_report(Tenant(overage="allow_and_record", usage={"2026-11": 1500}), clock=NOW)
    assert result["remaining_before_allowance"] == 0
    assert result["blocking_cap_enabled"] is False


@pytest.mark.parametrize("changes", [
    {"usage": {"2026-11": -1}}, {"usage": {"2026-13": 1}},
    {"usage": {"2026-11": True}}, {"usage": {"2026-11": "43"}},
    {"monthly_allowance": -1}, {"revision": True}, {"overage": "open"},
    {"tenant_id": "../../etc/passwd"},
])
def test_malformed_record_fails_closed(changes):
    with pytest.raises(UsageUnavailable):
        tenant_usage_report(Tenant(**changes), clock=NOW)


class Store:
    def __init__(self, record): self.record = record
    def get(self, tid): return self.record if tid == "usr-usage" else None
    def tenant_ids(self): return ("usr-usage",)


def test_owner_only_usage_route_masks_provider_data_and_missing_tenants():
    app = FastAPI()
    app.include_router(create_admin_router(
        tenant_store=Store(Tenant(usage={datetime.now(timezone.utc).strftime("%Y-%m"): 41})),
        verify_cognito_admin=lambda tok: VerifiedAdmin("trusted-subject", frozenset({"owner"}), True)
        if tok == "owner-token" else None,
    ))
    client = TestClient(app)
    path = "/v1/admin/tenants/usr-usage/usage"
    for headers in [{}, {"X-GuardOath-Key":"owner-token"}, {"Authorization":"Bearer customer-token"}]:
        assert client.get(path, headers=headers).status_code == 401
    response = client.get(path, headers={"X-GuardOath-Admin-Session":"owner-token"})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["usage"]["requests_recorded"] == 41
    assert "payer_id" not in response.text and "key_hash" not in response.text
    assert client.get("/v1/admin/tenants/usr-other/usage", headers={"X-GuardOath-Admin-Session":"owner-token"}).status_code == 404


def test_corrupt_usage_never_returns_partial_data():
    app = FastAPI()
    app.include_router(create_admin_router(
        tenant_store=Store(Tenant(usage={"bogus":12})),
        verify_cognito_admin=lambda tok: VerifiedAdmin("trusted-subject",frozenset({"owner"}),True)
        if tok == "owner-token" else None,
    ))
    response = TestClient(app).get("/v1/admin/tenants/usr-usage/usage", headers={"X-GuardOath-Admin-Session":"owner-token"})
    assert response.status_code == 503
    assert "bogus" not in response.text

@pytest.mark.parametrize("count,cap,state,percent", [
    (None,1000,"unavailable",None), (0,1000,"within_limit",0),
    (799,1000,"within_limit",79), (800,1000,"watch",80),
    (949,1000,"watch",94), (950,1000,"near_limit",95),
    (999,1000,"near_limit",99), (1000,1000,"at_limit",100),
    (1001,1000,"over_limit",100), (0,0,"at_limit",None),
    (1,0,"over_limit",None),
])
def test_owner_usage_quota_signal_is_derived_only_from_authoritative_counter(count, cap, state, percent):
    data = {} if count is None else {"2026-11": count}
    report = tenant_usage_report(Tenant(usage=data, monthly_allowance=cap), clock=NOW)
    signal = report["usage_threshold"]
    assert signal["state"] == state
    assert signal["utilization_percent_floor"] == percent
    assert signal["policy"] == "blocks_at_saved_cap"
    assert signal["persisted_alert"] is False
    assert report["requests_recorded"] == count
    assert report["aws_cost_usd"] is None and report["stripe_invoice_usd"] is None


def test_usage_soft_overage_signal_cannot_claim_blocking_or_estimated_billing():
    report = tenant_usage_report(Tenant(usage={"2026-11":1200}, monthly_allowance=1000,
                                        overage="allow_and_record"), clock=NOW)
    assert report["usage_threshold"]["state"] == "over_limit"
    assert report["usage_threshold"]["policy"] == "records_overage"
    assert report["blocking_cap_enabled"] is False
    assert report["aws_cost_usd"] is None and report["stripe_invoice_usd"] is None


def test_quota_pacing_is_bounded_utc_projection_not_financial_spend():
    late = lambda: datetime(2026, 11, 19, 18, tzinfo=timezone.utc)
    record = Tenant(usage={"2026-11": 410}, monthly_allowance=500,
                    updated_at="2026-11-19T17:00:00Z")
    report = tenant_usage_report(record, clock=late)
    pacing = report["quota_pacing"]
    assert pacing["projected_month_end_requests_floor"] == 410 * 30 // 19
    assert pacing["state"] == "projected_over_limit"
    assert pacing["record_metadata_freshness"] == "recent_record_metadata"
    assert pacing["calendar_days_elapsed"] == 19
    assert pacing["calendar_days_in_month"] == 30
    assert pacing["persisted_alert"] is False
    assert report["aws_cost_usd"] is None and report["stripe_invoice_usd"] is None
    assert record.monthly_allowance == 500 and record.usage == {"2026-11": 410}


@pytest.mark.parametrize("usage, cap, updated, day, expected", [
    ({}, 1000, "2026-11-19T17:00:00Z", 19, "unknown_counter"),
    ({"2026-11": 0}, 1000, "2026-11-19T17:00:00Z", 19, "projected_within_limit"),
    ({"2026-11": 410}, 500, "2026-11-15T17:00:00Z", 19, "stale_or_unknown_metadata"),
    ({"2026-11": 410}, 500, None, 19, "stale_or_unknown_metadata"),
    ({"2026-11": 410}, 500, "not-a-time", 19, "stale_or_unknown_metadata"),
    ({"2026-11": 410}, 500, "2026-11-30T17:00:00Z", 19, "stale_or_unknown_metadata"),
    ({"2026-11": 5}, 500, "2026-11-06T17:00:00Z", 6, "insufficient_observation"),
    ({"2026-11": 0}, 0, "2026-11-19T17:00:00Z", 19, "zero_allowance"),
    ({"2026-11": 500}, 500, "2026-11-19T17:00:00Z", 19, "already_at_limit"),
    ({"2026-11": 501}, 500, "2026-11-19T17:00:00Z", 19, "already_over_limit"),
])
def test_quota_pacing_unknown_stale_and_limits_are_explicit(usage, cap, updated, day, expected):
    now = lambda: datetime(2026, 11, day, 18, tzinfo=timezone.utc)
    pacing = tenant_usage_report(Tenant(usage=usage, monthly_allowance=cap,
                                        updated_at=updated), clock=now)["quota_pacing"]
    assert pacing["state"] == expected
    if expected in {"unknown_counter", "stale_or_unknown_metadata",
                    "insufficient_observation", "zero_allowance"}:
        assert pacing["projected_month_end_requests_floor"] is None
    assert pacing["persisted_alert"] is False


def test_quota_pacing_api_is_owner_only():
    app = FastAPI()
    app.include_router(create_admin_router(
        tenant_store=Store(Tenant(usage={"2026-11": 400},
                                  updated_at="2026-11-19T17:00:00Z")),
        verify_cognito_admin=lambda tok: VerifiedAdmin("trusted-subject", frozenset({"owner"}), True)
        if tok == "owner-token" else None,
    ))
    cli = TestClient(app)
    path = "/v1/admin/tenants/usr-usage/usage"
    assert cli.get(path).status_code == 401
    assert cli.get(path, headers={"X-GuardOath-Key": "owner-token"}).status_code == 401
    response = cli.get(path, headers={"X-GuardOath-Admin-Session": "owner-token"})
    assert response.status_code == 200
    assert response.json()["usage"]["quota_pacing"]["persisted_alert"] is False
