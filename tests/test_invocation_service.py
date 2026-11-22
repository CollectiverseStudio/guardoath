"""Real hosted auth, tenant Engine, durable store and offline provider boundaries."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient
import pytest

from guardoath.app import create_app
from guardoath.budgets import ResourceLimits
from guardoath.hosted import TenantService
from guardoath.models import CheckerResult, Finding
from guardoath.tenant_record import TenantRecord, new_key, usage_month
from guardoath.tenant_store import MemoryTenantStore
from guardoath.invocations.adapters import ProviderResult
from guardoath.invocations.contracts import ConnectionInfo, ModelInfo
from guardoath.invocations.service import ConnectionBinding, InvocationGateway
from guardoath.invocations.store import SQLiteInvocationStore, InvocationStoreUnavailable


SIGNING_KEY = b"synthetic-invocation-signing-key-0123456789"


class Provider:
    tenant_id = "customer-a"
    connection_id = "approved"
    connection_version = "v1"

    def __init__(self, text="Approved answer", *, effect=None, result=None):
        self.text, self.effect, self.result = text, effect, result
        self.calls = []

    def invoke(self, call):
        self.calls.append(call)
        if self.effect:
            self.effect()
        return self.result or ProviderResult(text=self.text, input_tokens=6, output_tokens=3,
            observed_model="approved-model-1", observed_service_tier="default",
            provider_request_id="provider-request-1", response_id="response-1", outcome="completed", reason_code="completed")


def detector(events, *, error=False, redact=False, simulated=False, on_output=None):
    async def run(request, policy, stage):
        events.append((stage.value, request.content, policy.mode))
        if on_output is not None and stage.value == "output":
            on_output()
        if error:
            raise RuntimeError("PRIVATE detector exception")
        private = "PRIVATE" in request.content
        return CheckerResult(checker_id="pii" if redact else "secrets", checker_version="fixture-1",
            status="flagged" if private else "passed", simulated=simulated,
            findings=(Finding(rule_id="pii.fixture" if redact else "secrets.fixture", category="fixture",
                              severity="high", explanation="Synthetic finding"),) if private else (),
            transformed_content=request.content.replace("PRIVATE", "[REDACTED]") if private and redact else None)
    run.checker_version = "fixture-1"
    return run


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("GUARDOATH_INVOCATIONS_ENABLED", "0")
    gateways = []

    def build(*, provider=None, record_changes=None, checkers=None, calls=50,
              output_quota=50000, provider_timeout=1.0, request_timeout=3.0, path=None, store=None):
        key, digest = new_key("customer-a")
        key_b, digest_b = new_key("customer-b")
        base = dict(tenant_id="customer-a", display_name="Synthetic customer", key_hashes=(digest,),
                    subscription_status="active", monthly_allowance=1000, overage="block", revision=1,
                    input_check_secrets="block", output_check_secrets="block")
        record = TenantRecord(**{**base, **(record_changes or {})})
        other = TenantRecord(tenant_id="customer-b", display_name="Other synthetic customer",
                             key_hashes=(digest_b,), subscription_status="active", revision=1,
                             input_check_secrets="block")
        events = []
        tenants = TenantService(MemoryTenantStore((record, other)), checkers if checkers is not None else
                                {"secrets": detector(events)})
        provider = provider or Provider()
        info = ConnectionInfo(id="approved", version="v1", label="Approved connection", provider="openai",
                              models=(ModelInfo(id="model-a", label="Approved model",
                                                provider_model_id="approved-model-1", max_output_tokens=512),),
                              monthly_call_limit=calls, monthly_output_token_limit=output_quota)
        store = store or SQLiteInvocationStore(str(path or tmp_path / "interactions.sqlite3"), SIGNING_KEY)
        gateway = InvocationGateway(store=store, connections=(ConnectionBinding("customer-a", info, provider),),
                                    provider_timeout=provider_timeout, request_timeout=request_timeout, max_workers=1)
        gateways.append(gateway)
        app = create_app(tenants=tenants, invocation_gateway=gateway,
                         limits=ResourceLimits(checks_per_minute=10000, burst=100))
        client = TestClient(app)
        return client, gateway, provider, events, tenants, key, key_b

    yield build
    for gateway in gateways:
        gateway.workers.close()


def payload(**changes):
    return {"application_id": "customer-a", "request_id": "request-1",
            "target": {"kind": "model", "connection_id": "approved", "model_id": "model-a"},
            "message": "Synthetic request", "max_output_tokens": 128, **changes}


def post(client, key, **changes):
    return client.post("/v1/interactions", headers={"X-GuardOath-Key": key}, json=payload(**changes))


def test_original_releases_checked_text_and_restart_replays_only_durable_receipt(setup, tmp_path):
    client, gateway, provider, events, tenants, key, _ = setup()
    response = post(client, key)
    assert response.status_code == 200, response.text
    result = response.json()
    receipt = result["receipt"]
    assert result["response"] == "Approved answer" and result["replayed"] is False
    assert receipt["status"] == "completed" and receipt["dispatch_state"] == "response_observed"
    assert receipt["usage"] == {"source": "provider_reported", "input_tokens": 6, "output_tokens": 3}
    assert receipt["input_check"]["checker_results"] == [
        {"checker_id": "secrets", "checker_version": "fixture-1", "status": "passed"}]
    assert [e[0] for e in events] == ["input", "output"]
    assert tenants.store.get("customer-a").used(usage_month()) == 2
    assert len(provider.calls) == 1 and provider.calls[0].model_id == "approved-model-1"
    assert client.get("/v1/interactions/" + receipt["interaction_id"], headers={"X-GuardOath-Key": key}).json() == receipt
    assert client.get("/v1/interactions/by-request/request-1", headers={"X-GuardOath-Key": key}).json() == receipt
    assert client.get("/v1/interactions", headers={"X-GuardOath-Key": key}).json()["items"] == [receipt]
    second, _, other_provider, _, _, key2, _ = setup()
    replay = post(second, key2).json()
    assert replay == {"receipt": receipt, "response": None, "replayed": True}
    assert other_provider.calls == []
    raw = (tmp_path / "interactions.sqlite3").read_bytes()
    for private in (b"Synthetic request", b"Approved answer", key.encode()):
        assert private not in raw
    assert response.headers["cache-control"] == "no-store"


def test_same_id_changed_request_conflicts_without_second_provider_call(setup):
    client, _, provider, _, _, key, _ = setup()
    assert post(client, key).json()["receipt"]["status"] == "completed"
    assert post(client, key, message="Different text").status_code == 409
    assert post(client, key, max_output_tokens=129).status_code == 409
    assert len(provider.calls) == 1


@pytest.mark.parametrize("changes,expected", [
    ({"message": "PRIVATE input"}, "input_blocked"),
    ({}, "output_blocked"),
])
def test_blocked_input_never_dispatches_and_blocked_output_never_leaks(setup, caplog, changes, expected):
    client, _, provider, _, _, key, _ = setup(provider=Provider("PRIVATE provider answer"))
    response = post(client, key, **changes)
    assert response.status_code == 200
    assert response.json()["receipt"]["status"] == expected
    assert response.json()["response"] is None
    assert len(provider.calls) == (0 if expected == "input_blocked" else 1)
    assert "PRIVATE" not in response.text and "PRIVATE" not in caplog.text


@pytest.mark.parametrize("checkers", [{}, {"secrets": detector([], error=True)}, {"secrets": detector([], simulated=True)}])
def test_incomplete_or_simulated_validation_cannot_dispatch(setup, checkers):
    client, _, provider, _, _, key, _ = setup(checkers=checkers, record_changes={"failure_action": "flag"})
    result = post(client, key).json()
    assert result["receipt"]["status"] == "input_incomplete"
    assert result["response"] is None and provider.calls == []


def test_verified_redaction_is_the_only_text_forwarded_and_released(setup):
    events = []
    client, _, provider, _, _, key, _ = setup(provider=Provider("Answer PRIVATE"),
        record_changes={"input_check_secrets": None, "output_check_secrets": None,
                        "input_check_pii": "redact", "output_check_pii": "redact"},
        checkers={"pii": detector(events, redact=True)})
    result = post(client, key, message="Input PRIVATE").json()
    assert result["receipt"]["status"] == "completed"
    assert provider.calls[0].text == "Input [REDACTED]"
    assert result["response"] == "Answer [REDACTED]"
    assert [e[1] for e in events] == ["Input PRIVATE", "Input [REDACTED]", "Answer PRIVATE", "Answer [REDACTED]"]


def test_saved_off_remains_authoritative(setup):
    events = []
    async def enabled_check(request, policy, stage):
        return CheckerResult(checker_id="text_security", checker_version="fixture-1", status="passed")
    enabled_check.checker_version = "fixture-1"
    client, _, provider, _, _, key, _ = setup(record_changes={"input_check_secrets": None, "output_check_secrets": None,
                                                            "input_check_text_security": "allow"},
        checkers={"secrets": detector(events, error=True), "text_security": enabled_check})
    result = post(client, key, message="PRIVATE but check Off").json()
    assert result["receipt"]["status"] == "completed" and len(provider.calls) == 1
    assert events == []


@pytest.mark.parametrize("extra", [
    {"tools": []}, {"stream": True}, {"agent_id": "agent"}, {"provider_url": "https://example.invalid"},
    {"temperature": 0.2}, {"max_output_tokens": True}, {"max_output_tokens": 513},
    {"message": "\u20ac" * 6000}, {"target": {"kind": "agent", "connection_id": "approved", "model_id": "model-a"}},
])
def test_unsupported_or_oversized_parameters_never_call_provider(setup, extra):
    client, _, provider, _, _, key, _ = setup()
    assert post(client, key, **extra).status_code == 422
    assert provider.calls == []


def test_tenant_catalog_target_and_receipt_are_bound_to_real_auth(setup):
    client, _, provider, _, _, key, key_b = setup()
    assert client.get("/v1/connections").status_code == 401
    own = client.get("/v1/connections", headers={"X-GuardOath-Key": key}).json()
    assert own["available"] and own["connections"][0]["models"][0]["id"] == "model-a"
    other = client.get("/v1/connections", headers={"X-GuardOath-Key": key_b}).json()
    assert other["connections"] == [] and other["reason_code"] == "no_connections"
    assert post(client, key_b).status_code == 403
    assert post(client, key, target={"kind": "model", "connection_id": "approved", "model_id": "arbitrary"}).status_code == 404
    result = post(client, key).json()
    path = "/v1/interactions/" + result["receipt"]["interaction_id"]
    assert client.get(path, headers={"X-GuardOath-Key": key_b}).status_code == 404
    assert client.get("/v1/interactions/by-request/request-1", headers={"X-GuardOath-Key": key_b}).status_code == 404
    assert client.get("/v1/interactions", headers={"X-GuardOath-Key": key_b}).json() == {"items": []}
    assert len(provider.calls) == 1


@pytest.mark.parametrize("change,reason,status", [
    ({"demo": True}, "demo_not_enabled", 403),
    ({"subscription_status": "inactive"}, "subscription_required", 402),
    ({"mode": "observe"}, "enforcement_required", 403),
])
def test_entitlement_and_enforcement_before_paid_dispatch(setup, change, reason, status):
    client, _, provider, _, _, key, _ = setup(record_changes=change)
    catalog = client.get("/v1/connections", headers={"X-GuardOath-Key": key}).json()
    assert catalog["available"] is False and catalog["reason_code"] == reason
    assert post(client, key).status_code == status and provider.calls == []


@pytest.mark.parametrize("calls,output_quota", [(1, 50000), (50, 128)])
def test_durable_provider_quota_stops_next_request(setup, calls, output_quota):
    client, _, provider, _, _, key, _ = setup(calls=calls, output_quota=output_quota)
    assert post(client, key).json()["receipt"]["status"] == "completed"
    next_result = post(client, key, request_id="request-2").json()
    assert next_result["receipt"]["status"] == "quota_exceeded"
    assert len(provider.calls) == 1


def test_validation_allowance_exhaustion_after_dispatch_withholds_answer(setup):
    client, _, provider, _, tenants, key, _ = setup(record_changes={"monthly_allowance": 1})
    result = post(client, key).json()
    assert result["receipt"]["status"] == "output_incomplete" and result["response"] is None
    assert result["receipt"]["reason_code"] == "validation_quota_exceeded"
    assert len(provider.calls) == 1 and tenants.store.get("customer-a").used(usage_month()) == 1


def test_tenant_change_during_provider_withholds_without_changing_frozen_policy(setup):
    provider = Provider()
    client, _, _, _, tenants, key, _ = setup(provider=provider)
    provider.effect = lambda: tenants.store.update_checks("customer-a", {"output_check_secrets": "allow"}, 1)
    result = post(client, key).json()
    assert result["receipt"]["status"] == "unavailable" and result["response"] is None
    assert result["receipt"]["policy_revision"] == 1
    assert result["receipt"]["reason_code"] == "tenant_changed"


def test_ambiguous_dispatch_write_never_calls_provider_and_retry_never_resends(setup, monkeypatch):
    client, gateway, provider, _, _, key, _ = setup()
    original = gateway.store.reserve_dispatch

    def ambiguous(*args):
        original(*args)
        raise InvocationStoreUnavailable("PRIVATE storage exception")

    monkeypatch.setattr(gateway.store, "reserve_dispatch", ambiguous)
    response = post(client, key)
    assert response.status_code == 503 and "PRIVATE" not in response.text
    assert provider.calls == []
    replay = post(client, key).json()
    assert replay["replayed"] is True and replay["receipt"]["status"] == "dispatch_intent"
    assert replay["response"] is None and provider.calls == []


def test_terminal_storage_failure_withholds_answer(setup, monkeypatch):
    client, gateway, provider, _, _, key, _ = setup()
    original = gateway.store.advance

    def fail_final(current, next_receipt):
        if next_receipt.status == "completed":
            raise InvocationStoreUnavailable("PRIVATE storage exception")
        return original(current, next_receipt)

    monkeypatch.setattr(gateway.store, "advance", fail_final)
    result = post(client, key)
    assert result.status_code == 503 and "Approved answer" not in result.text
    assert len(provider.calls) == 1
    assert post(client, key).json()["replayed"] is True and len(provider.calls) == 1


def test_timeout_keeps_worker_slot_until_actual_sync_call_finishes(setup):
    entered, finish, done = Event(), Event(), Event()

    def blocked():
        entered.set()
        try:
            assert finish.wait(5)
        finally:
            done.set()

    provider = Provider(effect=blocked)
    client, gateway, _, _, _, key, _ = setup(provider=provider, provider_timeout=0.1, request_timeout=2)
    try:
        first = post(client, key).json()
        assert entered.is_set() and first["receipt"]["status"] == "outcome_unknown"
        second = post(client, key, request_id="request-2").json()
        assert second["receipt"]["status"] == "unavailable" and second["receipt"]["reason_code"] == "provider_capacity"
        assert len(provider.calls) == 1
        assert post(client, key).json()["replayed"] is True
    finally:
        finish.set()
        assert done.wait(2)


def test_two_hosted_instances_cannot_dispatch_same_request_twice(setup, tmp_path):
    entered, release = Event(), Event()
    provider = Provider(effect=lambda: (entered.set(), release.wait(5)))
    a, _, _, _, _, key_a, _ = setup(provider=provider, provider_timeout=3, request_timeout=5)
    b, _, provider_b, _, _, key_b, _ = setup(provider_timeout=3, request_timeout=5)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(post, a, key_a)
        try:
            assert entered.wait(2)
            second = pool.submit(post, b, key_b).result(timeout=2)
            assert second.status_code == 200 and second.json()["replayed"] is True
            assert provider_b.calls == []
        finally:
            release.set()
        assert first.result(timeout=2).json()["receipt"]["status"] == "completed"
    assert len(provider.calls) == 1


def test_unknown_provider_outcome_keeps_trustworthy_ids_and_usage(setup):
    normalized = ProviderResult(outcome="unknown", reason_code="provider_unavailable", input_tokens=6,
                                output_tokens=1, provider_request_id="known-request", response_id="known-response",
                                observed_model="approved-model-1")
    client, _, _, _, _, key, _ = setup(provider=Provider(result=normalized))
    result = post(client, key).json()
    assert result["receipt"]["status"] == "outcome_unknown" and result["response"] is None
    assert result["receipt"]["provider_request_id"] == "known-request"
    assert result["receipt"]["provider_response_id"] == "known-response"
    assert result["receipt"]["usage"]["output_tokens"] == 1


def test_adapter_local_refusal_does_not_claim_provider_response(setup):
    normalized = ProviderResult(outcome="rejected", reason_code="configuration_unavailable")
    client, _, _, _, _, key, _ = setup(provider=Provider(result=normalized))
    result = post(client, key).json()["receipt"]
    assert result["status"] == "provider_rejected" and result["dispatch_state"] == "not_dispatched"
    assert result["usage"] is None and result["reserved_output_tokens"] == 128


def test_authentication_rejects_wrong_record_from_tenant_store(setup, monkeypatch):
    client, gateway, provider, _, tenants, key, _ = setup()
    other = tenants.store.get("customer-b")
    monkeypatch.setattr(tenants.store, "get", lambda tenant_id: other)
    assert client.get("/v1/connections", headers={"X-GuardOath-Key": key}).status_code == 401
    class Sessions:
        def tenant_for(self, token):
            return "customer-a"
    customer = TestClient(create_app(tenants=tenants, sessions=Sessions(), invocation_gateway=gateway))
    assert customer.get("/v1/connections", headers={"X-GuardOath-Session": "synthetic-session"}).status_code == 401
    assert customer.get("/v1/interactions", headers={"X-GuardOath-Session": "synthetic-session"}).status_code == 401
    assert provider.calls == []


def test_policy_change_during_output_check_withholds_original_text(setup):
    holder = {}
    def mutate():
        holder["tenants"].store.update_checks("customer-a", {"output_check_secrets": "allow"}, 1)
    client, _, provider, _, tenants, key, _ = setup(checkers={"secrets": detector([], on_output=mutate)})
    holder["tenants"] = tenants
    result = post(client, key).json()
    assert result["receipt"]["status"] == "output_incomplete" and result["response"] is None
    assert result["receipt"]["reason_code"] == "tenant_changed" and len(provider.calls) == 1


def test_receipt_reads_are_rate_limited_before_expensive_store_access(setup, monkeypatch):
    _, gateway, _, _, tenants, key, _ = setup()
    reads = []
    monkeypatch.setattr(gateway.store, "recent", lambda *args: (reads.append("recent") or ()))
    monkeypatch.setattr(gateway.store, "get", lambda *args: reads.append("get"))
    monkeypatch.setattr(gateway.store, "lookup_request", lambda *args: reads.append("lookup"))
    client = TestClient(create_app(tenants=tenants, invocation_gateway=gateway,
                                  limits=ResourceLimits(checks_per_minute=1, burst=1)))
    headers = {"X-GuardOath-Key": key}
    assert client.get("/v1/interactions", headers=headers).status_code == 200
    assert client.get("/v1/interactions/by-request/request-1", headers=headers).status_code == 429
    assert client.get("/v1/interactions/" + "a" * 32, headers=headers).status_code == 429
    assert reads == ["recent"]
