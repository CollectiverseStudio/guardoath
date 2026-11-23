"""Offline evidence for the one-call live verifier's dispatch/privacy boundaries."""
from copy import deepcopy
import hashlib
import json

import pytest

from tools import verify_model_invocation as tool


APP = "synthetic-customer"
KEY = "gok_synthetic-customer_" + "synthetic-private-key" * 3
SHA = "a" * 40
HEALTH = {"status": "ok", "source_sha": SHA}
CATALOG = {
    "application_id": APP, "available": True, "reason_code": "ready",
    "connections": [{"id": "approved", "version": "v1", "label": "Synthetic",
                     "provider": "openai", "models": [
                         {"id": "model-a", "label": "Synthetic model",
                          "provider_model_id": "synthetic-model-1", "max_output_tokens": 256}],
                     "monthly_call_limit": 20, "monthly_output_token_limit": 5120}],
}
TARGET = {"connection_id": "approved", "connection_version": "v1", "provider": "openai",
          "region": None, "model_id": "model-a", "provider_model_id": "synthetic-model-1"}
CHECK = {"check_id": "synthetic-check", "decision": "allow", "evaluation_status": "complete",
         "simulated": False, "checker_ids": ["human_safety"], "finding_ids": [],
         "checker_results": [{"checker_id": "human_safety", "checker_version": "1",
                              "status": "passed"}]}
RECEIPT = {
    "application_id": APP, "interaction_id": "a" * 32, "request_id": "synthetic-request",
    "created_at": "2026-11-23T00:00:00Z", "updated_at": "2026-11-23T00:00:01Z",
    "status": "completed", "target": TARGET, "policy_revision": 1, "policy_digest": "b" * 64,
    "catalog_version": "v1", "input_check": CHECK, "output_check": CHECK,
    "dispatch_state": "response_observed", "reserved_output_tokens": 128,
    "quota_period": "2026-11", "sequence": 5, "event_digest": "c" * 64,
    "provider_request_id": "synthetic-private-provider-request",
}


def response(value, status=200):
    return tool.WireResponse(status, json.dumps(value).encode())


class Wire:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, method, path, body, headers):
        self.calls.append((method, path, body, headers))
        assert self.replies, "No additional request or retry is allowed"
        result = self.replies.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def kwargs(**changes):
    return {"application_id": APP, "api_key": KEY, "invoke": True,
            "connection_id": "approved", "model_id": "model-a",
            "request_id": "synthetic-request", "expected_sha": SHA, **changes}


def post_response(receipt=None, **changes):
    return response({"receipt": deepcopy(receipt or RECEIPT),
                     "response": "PRIVATE synthetic provider greeting", "replayed": False, **changes})


def live_wire(*, post=None, recovered=None, catalog=None):
    return Wire(response(HEALTH), response(catalog or CATALOG), response({}, 404),
                post if post is not None else post_response(), response(recovered or RECEIPT))


def assert_private_report(report):
    serialized = json.dumps(report)
    for secret in (APP, KEY, "synthetic-request", "PRIVATE", "synthetic-private-provider-request",
                   "a" * 32, tool.SYNTHETIC_MESSAGE, "synthetic-model-1"):
        assert secret not in serialized


def test_default_is_only_public_health_and_authenticated_catalog():
    wire = Wire(response(HEALTH), response(CATALOG))
    report = tool.verify(application_id=APP, api_key=KEY, transport=wire)
    assert report["passed"] and report["mode"] == "read_only"
    assert not report["post_attempted"] and not report["original_checked_response_verified"]
    assert not report["full_production_acceptance"] and not report["source_sha_verified"]
    assert [(c[0], c[1]) for c in wire.calls] == [("GET", "/healthz"), ("GET", "/v1/connections")]
    assert "X-GuardOath-Key" not in wire.calls[0][3]
    assert wire.calls[1][3]["X-GuardOath-Key"] == KEY
    assert_private_report(report)


def test_one_synthetic_post_exact_body_hash_and_correlated_metadata_recovery():
    wire = live_wire()
    report = tool.verify(**kwargs(), transport=wire)
    assert report["passed"] and report["source_sha_verified"]
    assert report["post_attempted"] and report["original_checked_response_verified"]
    assert report["durable_receipt_correlated"] and report["receipt_status"] == "completed"
    assert report["keep_request_id"] and not report["full_production_acceptance"]
    assert [c[0] for c in wire.calls] == ["GET", "GET", "GET", "POST", "GET"]
    post = wire.calls[3]
    assert post[1] == "/v1/interactions"
    assert post[3]["x-amz-content-sha256"] == hashlib.sha256(post[2]).hexdigest()
    assert json.loads(post[2])["message"] == tool.SYNTHETIC_MESSAGE
    assert json.loads(post[2])["max_output_tokens"] == 128
    assert wire.calls[2][1] == wire.calls[4][1] == "/v1/interactions/by-request/synthetic-request"
    assert_private_report(report)


@pytest.mark.parametrize("reason", ["runtime_unconfigured", "runtime_invalid", "demo_not_enabled",
                                    "subscription_required", "enforcement_required", "no_connections"])
def test_unready_catalog_never_attempts_generation(reason):
    catalog = {"application_id": APP, "available": False, "reason_code": reason}
    wire = Wire(response(HEALTH), response(catalog))
    report = tool.verify(**kwargs(), transport=wire)
    assert not report["passed"] and not report["post_attempted"]
    assert report["catalog_reason_code"] == reason
    assert len(wire.calls) == 2


@pytest.mark.parametrize("change", [
    {"api_key": "PRIVATE-invalid"}, {"api_key": KEY + "\n"}, {"api_key": KEY + "\x7f"},
    {"application_id": "another-customer"}, {"max_output_tokens": 257},
    {"max_output_tokens": 0}, {"max_output_tokens": True}, {"request_id": "../PRIVATE"},
    {"request_id": None}, {"connection_id": None}, {"model_id": None}, {"expected_sha": "PRIVATE"},
    {"invoke": False},
])
def test_invalid_inputs_stop_before_any_network_request(change):
    wire = Wire()
    report = tool.verify(**kwargs(**change), transport=wire)
    assert report["issues"] == ["invalid_arguments_or_credential"]
    assert wire.calls == [] and not report["post_attempted"]
    assert_private_report(report)


@pytest.mark.parametrize("status", [301, 302, 401, 403, 429, 500, 503])
def test_catalog_errors_cannot_trigger_post_or_expose_bodies(status):
    wire = Wire(response(HEALTH), response({"detail": "PRIVATE " + KEY}, status))
    report = tool.verify(**kwargs(), transport=wire)
    assert report["issues"] == ["catalog_unavailable_or_mismatched"]
    assert len(wire.calls) == 2 and not report["post_attempted"]
    assert_private_report(report)


def test_source_sha_mismatch_blocks_even_catalog_read():
    wire = Wire(response({**HEALTH, "source_sha": "b" * 40}))
    report = tool.verify(**kwargs(), transport=wire)
    assert report["issues"] == ["source_sha_mismatch"] and len(wire.calls) == 1


@pytest.mark.parametrize("mutation", [
    lambda c: c.update(application_id="other-customer"),
    lambda c: c["connections"].append(deepcopy(c["connections"][0])),
    lambda c: c["connections"][0]["models"].append(deepcopy(c["connections"][0]["models"][0])),
    lambda c: c.update(reason_code="private_provider_error"),
])
def test_mismatched_or_ambiguous_catalog_fails_before_dispatch(mutation):
    catalog = deepcopy(CATALOG)
    mutation(catalog)
    wire = Wire(response(HEALTH), response(catalog))
    report = tool.verify(**kwargs(), transport=wire)
    assert report["issues"] == ["catalog_unavailable_or_mismatched"]
    assert not report["post_attempted"]


@pytest.mark.parametrize("change", [{"connection_id": "unapproved"}, {"model_id": "unapproved"},
                                    {"max_output_tokens": 256}])
def test_target_and_token_ceiling_checked_against_approved_catalog(change):
    catalog = deepcopy(CATALOG)
    catalog["connections"][0]["models"][0]["max_output_tokens"] = 128
    wire = Wire(response(HEALTH), response(catalog))
    report = tool.verify(**kwargs(**change), transport=wire)
    assert report["issues"] == ["target_or_request_preflight_failed"]
    assert not report["post_attempted"] and len(wire.calls) == 2


def test_existing_request_never_reposts_even_when_its_receipt_completed():
    wire = Wire(response(HEALTH), response(CATALOG), response(RECEIPT))
    report = tool.verify(**kwargs(), transport=wire)
    assert report["existing_receipt_detected"] and report["receipt_recovered"]
    assert not report["passed"] and not report["post_attempted"]
    assert report["issues"] == ["request_id_already_used"]
    assert len(wire.calls) == 3 and report["keep_request_id"]


@pytest.mark.parametrize("reply", [response({}, 503), TimeoutError("PRIVATE error"),
                                    response({**RECEIPT, "application_id": "other-customer"})])
def test_unconfirmed_existing_request_prevents_generation(reply):
    wire = Wire(response(HEALTH), response(CATALOG), reply)
    report = tool.verify(**kwargs(), transport=wire)
    assert not report["post_attempted"] and report["keep_request_id"]
    assert_private_report(report)


@pytest.mark.parametrize("post", [TimeoutError("PRIVATE " + KEY), response({"detail": "PRIVATE"}, 503),
                                   tool.WireResponse(200, b"not json"),
                                   post_response(response=None, replayed=True)])
def test_unknown_or_replayed_post_only_recovers_receipt_without_retry(post):
    wire = live_wire(post=post)
    report = tool.verify(**kwargs(), transport=wire)
    assert not report["passed"] and not report["original_checked_response_verified"]
    assert report["receipt_recovered"] and report["receipt_status"] == "completed"
    assert report["keep_request_id"]
    assert sum(c[0] == "POST" for c in wire.calls) == 1
    assert_private_report(report)


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(application_id="other-customer"),
    lambda r: r.update(request_id="other-request"),
    lambda r: r["target"].update(connection_version="v2"),
    lambda r: r["target"].update(provider_model_id="wrong-model"),
    lambda r: r.update(reserved_output_tokens=256),
    lambda r: r.update(usage={"input_tokens": 2, "output_tokens": 129}),
    lambda r: r.update(sequence=0),
    lambda r: r.update(event_digest="0" * 64),
    lambda r: r["input_check"].update(evaluation_status="incomplete"),
    lambda r: r["input_check"].update(simulated=True),
    lambda r: r["input_check"].update(decision="block"),
    lambda r: r["output_check"]["checker_results"][0].update(status="error"),
])
def test_original_response_must_match_target_and_actual_completed_checks(mutation):
    receipt = deepcopy(RECEIPT)
    mutation(receipt)
    wire = live_wire(post=post_response(receipt))
    report = tool.verify(**kwargs(), transport=wire)
    assert not report["passed"] and not report["original_checked_response_verified"]
    assert sum(c[0] == "POST" for c in wire.calls) == 1
    assert_private_report(report)


@pytest.mark.parametrize("status", ["input_blocked", "output_blocked", "provider_withheld",
                                    "outcome_unknown", "quota_exceeded"])
def test_http_success_is_not_checked_answer_success(status):
    receipt = deepcopy(RECEIPT)
    receipt["status"] = status
    wire = live_wire(post=post_response(receipt, response=None), recovered=receipt)
    report = tool.verify(**kwargs(), transport=wire)
    assert not report["passed"] and report["receipt_status"] == status
    assert report["durable_receipt_correlated"] and report["keep_request_id"]
    assert not report["original_checked_response_verified"]


def test_recovery_must_match_entire_original_receipt():
    recovered = deepcopy(RECEIPT)
    recovered["event_digest"] = "d" * 64
    wire = live_wire(recovered=recovered)
    report = tool.verify(**kwargs(), transport=wire)
    assert report["original_checked_response_verified"] and not report["passed"]
    assert report["issues"] == ["durable_receipt_mismatch"]


def test_recovery_failure_never_retries_generation():
    wire = live_wire()
    wire.replies[-1] = response({"detail": "PRIVATE"}, 503)
    report = tool.verify(**kwargs(), transport=wire)
    assert not report["passed"] and report["keep_request_id"]
    assert report["issues"] == ["receipt_recovery_unconfirmed"]
    assert sum(c[0] == "POST" for c in wire.calls) == 1


def test_duplicate_json_keys_cannot_attest_readiness():
    wire = Wire(response(HEALTH), tool.WireResponse(200, b'{"available":true,"available":false}'))
    report = tool.verify(**kwargs(), transport=wire)
    assert not report["passed"] and not report["post_attempted"]


def test_https_transport_refuses_redirect_without_forwarding_key_or_reading_body(monkeypatch):
    calls = []
    class Reply:
        status = 302
        def read(self, size):
            raise AssertionError("Redirect body must not be consumed")
        def close(self):
            calls.append("response_closed")
    class Connection:
        def __init__(self, host, timeout):
            calls.append((host, timeout))
        def request(self, method, path, body, headers):
            calls.append((method, path, headers))
        def getresponse(self):
            return Reply()
        def close(self):
            calls.append("connection_closed")
    monkeypatch.setattr(tool.http.client, "HTTPSConnection", Connection)
    result = tool.HTTPSExchange()("GET", "/v1/connections", None, {"X-GuardOath-Key": KEY})
    assert result == tool.WireResponse(302, b"")
    assert calls[0] == ("guardoath.com", 15)
    assert len(calls) == 4 and calls[-2:] == ["response_closed", "connection_closed"]


def test_https_transport_caps_response_read_and_closes_on_overflow(monkeypatch):
    calls = []
    class Reply:
        status = 200
        def read(self, size):
            calls.append(size)
            return b"x" * size
        def close(self):
            calls.append("response_closed")
    class Connection:
        def request(self, *args, **kwargs):
            pass
        def getresponse(self):
            return Reply()
        def close(self):
            calls.append("connection_closed")
    monkeypatch.setattr(tool.http.client, "HTTPSConnection", lambda *args, **kwargs: Connection())
    with pytest.raises(ValueError, match="verification bound"):
        tool.HTTPSExchange()("GET", "/healthz", None, {})
    assert calls == [tool.MAX_JSON_BYTES + 1, "response_closed", "connection_closed"]


def test_cli_uses_environment_and_prints_only_private_safe_report(monkeypatch, capsys):
    monkeypatch.setenv("GUARDOATH_API_KEY", KEY)
    monkeypatch.setattr(tool, "HTTPSExchange", lambda: Wire(response(HEALTH), response(CATALOG)))
    assert tool.main(["--application-id", APP]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "read_only" and not report["post_attempted"]
    assert_private_report(report)


def test_mistaken_cli_secret_is_not_echoed_in_parser_error(capsys):
    with pytest.raises(SystemExit) as error:
        tool.main(["--application-id", APP, "--api-key", KEY])
    assert error.value.code == 2
    assert KEY not in capsys.readouterr().err


def test_verifier_accepts_real_hosted_gateway_and_existing_id_only_reads(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from guardoath.app import create_app
    from guardoath.hosted import TenantService
    from guardoath.human_safety import human_safety_check
    from guardoath.invocations.adapters import ProviderResult
    from guardoath.invocations.contracts import ConnectionInfo
    from guardoath.invocations.service import ConnectionBinding, InvocationGateway
    from guardoath.invocations.store import SQLiteInvocationStore
    from guardoath.tenant_record import TenantRecord, new_key
    from guardoath.tenant_store import MemoryTenantStore

    monkeypatch.setenv("GUARDOATH_INVOCATIONS_ENABLED", "0")
    key, digest = new_key(APP)
    record = TenantRecord(tenant_id=APP, display_name="Synthetic", key_hashes=(digest,),
        subscription_status="active", monthly_allowance=100, overage="block", revision=1,
        input_check_human_safety="flag", output_check_human_safety="block")
    tenants = TenantService(MemoryTenantStore((record,)), {"human_safety": human_safety_check})
    calls = []
    def invoke(call):
        calls.append(call)
        return ProviderResult(text="Hello from this synthetic provider.", outcome="completed",
                              reason_code="completed", input_tokens=12, output_tokens=6)
    provider = SimpleNamespace(tenant_id=APP, connection_id="approved", connection_version="v1", invoke=invoke)
    info = ConnectionInfo.model_validate(CATALOG["connections"][0])
    gateway = InvocationGateway(
        store=SQLiteInvocationStore(str(tmp_path / "receipts.sqlite3"), b"synthetic-signing-key-0123456789abcdef"),
        connections=(ConnectionBinding(APP, info, provider),))
    methods = []
    try:
        with TestClient(create_app(tenants=tenants, invocation_gateway=gateway)) as client:
            def transport(method, path, body, headers):
                methods.append(method)
                result = client.request(method, path, content=body, headers=headers)
                return tool.WireResponse(result.status_code, result.content)
            report = tool.verify(**kwargs(api_key=key, expected_sha=None), transport=transport)
            assert report["passed"] and report["durable_receipt_correlated"]
            assert len(calls) == 1 and calls[0].text == tool.SYNTHETIC_MESSAGE
            repeated = tool.verify(**kwargs(api_key=key, expected_sha=None), transport=transport)
            assert not repeated["post_attempted"] and repeated["existing_receipt_detected"]
            assert len(calls) == 1 and methods.count("POST") == 1
            assert key not in json.dumps(report)
    finally:
        gateway.workers.close()
