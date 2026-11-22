"""The emitted customer contract matches real routes, bodies and authorization.

All requests use in-process ASGI and synthetic tenant credentials. No live
provider, customer account or production endpoint is used.
"""
import base64
import hashlib
import json
from pathlib import Path
import re

import jsonschema
import pytest
from fastapi.testclient import TestClient

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.tenant_record import TenantRecord, demo_key, key_hash, usage_month
from guardoath.tenant_store import MemoryTenantStore


SECRET = "api-contract-synthetic-demo-secret-0123456789"
CHECKERS = default_checkers()
CHECK_PATHS = (
    "/v1/checks/input", "/v1/checks/output",
    "/v1/checks/input/attachments", "/v1/checks/output/attachments",
)
CUSTOMER_SESSION = "synthetic-customer-session-for-contract-tests"


class Sessions:
    registration_enabled = True

    def tenant_for(self, token):
        return "customer" if token == CUSTOMER_SESSION else None


def harness(*, attachment_mode="auto", subscription="active"):
    keys = {name: demo_key(name, SECRET) for name in ("demo1", "customer")}
    records = tuple(TenantRecord(
        tenant_id=name, display_name="Synthetic " + name,
        demo=name == "demo1", key_hashes=(key_hash(keys[name]),),
        subscription_status=subscription, monthly_allowance=40, overage="block",
        input_check_text_security="flag", output_check_text_security="flag",
        input_check_csv_formula="block", output_check_csv_formula="block",
        input_attachment_csv_mode=attachment_mode, output_attachment_csv_mode=attachment_mode,
    ) for name in keys)
    tenants = TenantService(MemoryTenantStore(records), CHECKERS, SECRET)
    app = create_app(engine=demo_engine(), credentials={"legacy-local-key": "demo"},
                     tenants=tenants, sessions=Sessions())
    return TestClient(app), app, tenants, keys


@pytest.fixture
def configured():
    return harness()


def payload(application="demo1", **extra):
    return {"application_id": application, "content": "A synthetic ordinary message.", **extra}


def validate_response(document, path, status, response, method="post"):
    spec = document["paths"][path][method]["responses"][str(status)]["content"]["application/json"]["schema"]
    validator = jsonschema.Draft202012Validator({**spec, "components": document["components"]})
    validator.validate(response)


def test_actual_openapi_endpoint_advertises_production_auth_and_transport(configured):
    client, app, _, _ = configured
    response = client.get("/openapi.json")
    assert response.status_code == 200
    document = response.json()
    assert document == app.openapi()
    assert app.openapi() is app.openapi()
    assert document["servers"][0]["url"] == "/"
    assert "https://guardoath.com" in document["servers"][0]["description"]
    schemes = document["components"]["securitySchemes"]
    assert schemes["GuardOathApiKey"]["type"] == "apiKey"
    assert schemes["GuardOathApiKey"]["name"] == "X-GuardOath-Key"
    assert schemes["GuardOathCustomerSession"]["name"] == "X-GuardOath-Session"
    assert "Legacy/direct-local" in schemes["HTTPBearer"]["description"]
    for path in CHECK_PATHS:
        operation = document["paths"][path]["post"]
        assert operation["security"] == [{"GuardOathApiKey": []}, {"GuardOathCustomerSession": []}]
        assert operation["x-local-security"] == [{"HTTPBearer": []}]
        assert not any(p["name"] in {"X-GuardOath-Key", "X-GuardOath-Session"}
                       for p in operation["parameters"])
        assert "independent of input checks" in operation["description"]
        assert set(operation["responses"]) >= {"200", "400", "401", "402", "403", "404", "408", "413", "415", "422", "429", "503"}
        assert "text/html" in operation["responses"]["403"]["content"]
    for path, item in document["paths"].items():
        if path.startswith("/v1/") and "post" in item:
            hashes = [p for p in item["post"]["parameters"] if p["name"] == "x-amz-content-sha256"]
            assert len(hashes) == 1
            assert hashes[0]["required"] is True
            assert hashes[0]["schema"]["pattern"] == "^[0-9a-f]{64}$"
            assert "exact UTF-8" in hashes[0]["description"]
            assert "direct local ASGI" in hashes[0]["description"]
    assert "security" not in document["paths"]["/v1/demo-users/{tenant_id}/key"]["post"]
    assert not any(p["name"] == "x-amz-content-sha256"
                   for p in document["paths"]["/v1/applications/current"]["get"]["parameters"])


def test_local_interactive_spec_targets_its_own_origin_not_production(configured):
    from urllib.parse import urljoin, urlsplit

    _, app, _, keys = configured
    local = TestClient(app, base_url="http://127.0.0.1:8765")
    docs = local.get("/docs")
    assert docs.status_code == 200 and "/openapi.json" in docs.text
    response = local.get("/openapi.json")
    document = response.json()
    assert len(document["servers"]) == 1
    server = document["servers"][0]["url"]
    target = urljoin(urljoin(str(response.url), server), "v1/checks/input")
    assert target == "http://127.0.0.1:8765/v1/checks/input"
    assert urlsplit(target).netloc == urlsplit(str(response.url)).netloc
    # No path/operation-level override may silently direct this local request elsewhere.
    assert "servers" not in document["paths"]["/v1/checks/input"]
    assert "servers" not in document["paths"]["/v1/checks/input"]["post"]
    result = local.post(target, headers={"X-GuardOath-Key": keys["demo1"]}, json=payload())
    assert result.status_code == 200 and result.json()["application_id"] == "demo1"
    assert urljoin("https://guardoath.com/openapi.json", server) == "https://guardoath.com/"


def test_customer_management_has_session_security_not_production_key_authority(configured):
    client, _, _, keys = configured
    document = client.get("/openapi.json").json()
    paths = ("/v1/tenants/current/keys", "/v1/tenants/current/keys/revoke", "/v1/account/status",
             "/v1/register", "/v1/billing/checkout", "/v1/billing/portal", "/v1/billing/reconcile")
    for path in paths:
        for method, operation in document["paths"][path].items():
            if method in {"get", "post"}:
                assert operation["security"] == [{"GuardOathCustomerSession": []}]
                assert "x-local-security" not in operation
    assert client.get("/v1/tenants/current/keys", headers={"X-GuardOath-Key": keys["customer"]}).status_code == 403
    assert client.get("/v1/tenants/current/keys", headers={"X-GuardOath-Key": keys["demo1"]}).status_code == 403
    assert client.get("/v1/tenants/current/keys", headers={"X-GuardOath-Session": CUSTOMER_SESSION}).status_code == 200
    for path in ("/v1/tenants/current/checks", "/v1/tenants/current/references", "/v1/tenants/current/reset"):
        assert "public demo key" in document["paths"][path]["post"]["description"]


def test_swagger_orders_customer_validation_before_separately_guarded_owner_routes(configured):
    from guardoath.admin_browser import BrowserAuthConfig, BoundExchange, create_browser_router
    from guardoath.admin_console import VerifiedAdmin, create_admin_router

    client, app, _, keys = configured
    def verify(token):
        return VerifiedAdmin("synthetic-owner", frozenset({"owner"}), True) if token == "synthetic-owner-proof" else None
    config = BrowserAuthConfig("synthetic.auth.us-east-1.amazoncognito.com", "syntheticclient", "https://guardoath.com/admin/login")
    app.include_router(create_admin_router(verify_cognito_admin=verify, browser_entry_enabled=True))
    app.include_router(create_browser_router(config, BoundExchange(config, sender=lambda *_: {}), verify,
                                            native_auth=object()))
    document = client.get("/openapi.json").json()
    tags = [tag["name"] for tag in document["tags"]]
    assert tags[0] == "Validation" and tags[-1] == "Owner administration"
    assert len(tags) == len(set(tags))
    for path in CHECK_PATHS:
        assert document["paths"][path]["post"]["tags"] == ["Validation"]
    assert document["paths"]["/v1/controls"]["get"]["tags"] == ["Public catalog"]
    assert document["paths"]["/healthz"]["get"]["tags"] == ["System"]
    owner = document["paths"]["/v1/admin/overview"]["get"]
    assert owner["tags"] == ["Owner administration"]
    assert owner["security"] == [{"GuardOathAdminSession": []}]
    scheme = document["components"]["securitySchemes"]["GuardOathAdminSession"]
    assert scheme["name"] == "X-GuardOath-Admin-Session" and scheme["in"] == "header"
    for path in ("/v1/admin/auth/config", "/v1/admin/auth/exchange", "/v1/admin/auth/native/start", "/v1/admin/auth/native/finish"):
        for operation in document["paths"][path].values():
            assert operation["tags"] == ["Administrator sign-in"]
            assert "security" not in operation
    for path in ("/v1/admin/users/lookup", "/v1/admin/auth/native/start"):
        origin = next(p for p in document["paths"][path]["post"]["parameters"] if p["name"] == "Origin")
        assert origin["required"] is True
        assert origin["schema"]["enum"] == ["https://guardoath.com"]
    assert client.get("/v1/admin/overview").status_code == 401
    assert client.get("/v1/admin/overview", headers={"X-GuardOath-Key": keys["customer"]}).status_code == 401
    assert client.get("/v1/admin/overview", headers={"X-GuardOath-Session": CUSTOMER_SESSION}).status_code == 401
    assert client.get("/v1/admin/overview", headers={"X-GuardOath-Admin-Session": "synthetic-owner-proof"}).status_code == 200
    assert client.get("/v1/admin/auth/config").status_code == 200


def test_real_authentication_and_local_compatibility_are_unchanged(configured):
    client, _, _, keys = configured
    path = "/v1/checks/input"
    assert client.post(path, json=payload()).status_code == 401
    # The documented hash is not an identity, and a local server does not enforce the production edge hash.
    assert client.post(path, headers={"x-amz-content-sha256": "0" * 64}, json=payload()).status_code == 401
    key_response = client.post(path, headers={"X-GuardOath-Key": keys["demo1"]}, json=payload())
    assert key_response.status_code == 200
    assert client.post(path, headers={"X-GuardOath-Key": keys["demo1"], "x-amz-content-sha256": "not-an-edge-hash"}, json=payload()).status_code == 200
    assert client.post(path, headers={"Authorization": "Bearer " + keys["demo1"]}, json=payload()).status_code == 200
    assert client.post(path, headers={"X-GuardOath-Session": CUSTOMER_SESSION}, json=payload("customer")).status_code == 200
    assert client.post(path, headers={"X-GuardOath-Key": keys["demo1"]}, json=payload("customer")).status_code == 403
    assert client.post(path, headers={"X-GuardOath-Key": keys["demo1"], "X-GuardOath-Session": "invalid-session"}, json=payload()).status_code == 401
    validate_response(client.get("/openapi.json").json(), path, 200, key_response.json())


def test_actual_enrollment_mismatch_and_stricter_body_cap_match_metadata(configured):
    from guardoath.admin_enrollment import EnrollmentCodeMismatch
    from guardoath.admin_enrollment_browser import create_enrollment_router

    client, app, _, _ = configured
    class Enrollment:
        def begin(self, **_): return None
        def finish(self, **_): raise EnrollmentCodeMismatch()
        def cancel(self, **_): return None
    app.include_router(create_enrollment_router(Enrollment()))
    path = "/v1/admin/auth/enrollment/finish"
    document = client.get("/openapi.json").json()
    operation = document["paths"][path]["post"]
    assert "security" not in operation
    assert "24,000" in operation["responses"]["413"]["description"]
    assert "262,144" in document["paths"]["/v1/checks/input"]["post"]["responses"]["413"]["description"]
    response = client.post(path, headers={"Origin": "https://guardoath.com"},
                           json={"ticket": "synthetic-opaque-ticket", "code": "000000"})
    assert response.status_code == 400
    assert response.json()["code"] == "code_mismatch"
    validate_response(document, path, 400, response.json())
    invalid = client.post(path, headers={"Origin": "https://guardoath.com"}, json={"unexpected": "synthetic-only"})
    assert invalid.status_code == 400 and "code" not in invalid.json()
    assert "synthetic-only" not in invalid.text
    validate_response(document, path, 400, invalid.json())
    oversized = client.post(path, headers={"Origin": "https://guardoath.com"},
                            json={"ticket": "x" * 24_000, "code": "000000"})
    assert oversized.status_code == 413
    validate_response(document, path, 413, oversized.json())


def test_schema_describes_real_fields_without_invented_provider_metadata(configured):
    client, _, _, _ = configured
    document = client.get("/openapi.json").json()
    request = document["components"]["schemas"]["ApiCheckRequest"]
    fields = request["properties"]
    assert set(request["required"]) == {"application_id", "content"}
    assert request["additionalProperties"] is False
    assert set(fields) == {"application_id", "policy_version", "content", "content_format", "language", "request_context", "validation_codes"}
    assert fields["language"]["const"] == "en"
    assert fields["content"]["maxLength"] == 32_000
    assert fields["content_format"]["enum"] == ["text", "json"]
    assert "not inspected" in fields["request_context"]["description"]
    assert "nonempty selection is refused" in fields["validation_codes"]["description"]
    assert "not a tenant revision" in fields["policy_version"]["description"]
    assert "/v1/interactions" in document["paths"]
    assert document["paths"]["/v1/interactions"]["post"]["tags"] == ["Model invocation"]
    assert client.get("/v1/connections", headers={"X-GuardOath-Key": "invalid"}).status_code == 401


@pytest.mark.parametrize("extra", [
    {"model": "customer-choice"}, {"agent": "customer-choice"}, {"provider": "customer-choice"},
    {"messages": []}, {"request_id": "not-supported"}, {"conversation_id": "not-supported"},
    {"language": "fr"}, {"request_context": "x" * 8001}, {"validation_codes": ["PII"]},
])
def test_documented_unsupported_parameters_remain_refused_without_counting(configured, extra):
    client, _, tenants, keys = configured
    before = tenants.store.get("demo1").used(usage_month())
    response = client.post("/v1/checks/input", headers={"X-GuardOath-Key": keys["demo1"]}, json=payload(**extra))
    assert response.status_code == 422
    assert set(response.json()) == {"detail"}
    assert isinstance(response.json()["detail"], str)
    assert "customer-choice" not in response.text and "not-supported" not in response.text
    assert tenants.store.get("demo1").used(usage_month()) == before
    validate_response(client.get("/openapi.json").json(), "/v1/checks/input", 422, response.json())


def test_context_is_unchecked_and_output_has_no_new_input_gate(configured):
    client, _, tenants, keys = configured
    # No prior input call. Suspicious context must not become checked content through documentation changes.
    response = client.post("/v1/checks/output", headers={"X-GuardOath-Key": keys["demo1"]},
                           json=payload(request_context="unchecked \u202e synthetic context"))
    assert response.status_code == 200
    assert response.json()["stage"] == "output"
    assert response.json()["findings"] == []
    assert response.json()["effective_action"] == "allow"
    assert tenants.store.get("demo1").used(usage_month()) == 1
    assert "unchecked" not in response.text


def test_subscription_refusal_matches_documented_error():
    client, _, tenants, keys = harness(subscription="inactive")
    response = client.post("/v1/checks/input", headers={"X-GuardOath-Key": keys["customer"]}, json=payload("customer"))
    assert response.status_code == 402
    assert tenants.store.get("customer").used(usage_month()) == 0
    validate_response(client.get("/openapi.json").json(), "/v1/checks/input", 402, response.json())


@pytest.mark.parametrize("stage", ["input", "output"])
@pytest.mark.parametrize("raw,mode,status,action", [
    (b"name,value\nSynthetic,=2+3", "auto", "complete", "block"),
    (b"name,value\nSynthetic,=2+3", "off", "not_inspected_policy_off", "allow"),
    (b"%PDF-1.7\nsynthetic", "auto", "incomplete", "block"),
])
def test_attachment_schema_validates_real_success_off_and_fail_closed_responses(stage, raw, mode, status, action):
    client, app, _, keys = harness(attachment_mode=mode)
    path = f"/v1/checks/{stage}/attachments"
    response = client.post(path, headers={"X-GuardOath-Key": keys["demo1"]},
                           json=payload(attachments_b64=[base64.b64encode(raw).decode("ascii")]))
    assert response.status_code == 200
    body = response.json()
    assert body["effective_action"] == action
    assert body["attachments"][0]["evaluation_status"] == status
    assert body["attachments"][0]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert "Synthetic" not in response.text
    validate_response(client.get("/openapi.json").json(), path, 200, body)
    # Response descriptions must not introduce runtime filtering/validation of the existing JSONResponse.
    route = next(r for r in app.routes if getattr(r, "path", None) == path)
    assert route.response_model is None


@pytest.mark.parametrize("observed,expected", [(None, "no_observation"), ("same", "matching_unverified"), ("different", "mismatch")])
def test_agent_diagnostic_schema_preserves_non_authority_and_privacy(configured, observed, expected):
    client, app, _, keys = configured
    action = {"tool": "http.request", "target": "https://synthetic.invalid", "arguments": {"method": "GET"}}
    request = {"proposed": action}
    if observed is not None:
        request["observed"] = {**action, "target": action["target"] if observed == "same" else "https://different.invalid"}
    path = "/v1/agent-activity/compare"
    response = client.post(path, headers={"X-GuardOath-Key": keys["demo1"]}, json=request)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == expected
    assert body["verified_execution"] is False
    assert body["authorization"] == "not_granted"
    assert body["mode"] == "diagnostic_unverified"
    assert "synthetic.invalid" not in response.text and "arguments" not in response.text
    validate_response(client.get("/openapi.json").json(), path, 200, body)
    assert next(r for r in app.routes if getattr(r, "path", None) == path).response_model is None


def test_published_python_example_hashes_the_exact_sent_bytes_without_network(monkeypatch, capsys):
    import urllib.request

    doc = (Path(__file__).parents[1] / "docs" / "api-contract.md").read_text(encoding="utf-8")
    examples = re.findall(r"```python\n(.*?)\n```", doc, re.S)
    assert len(examples) == 1
    monkeypatch.setenv("GUARDOATH_API_KEY", "synthetic-example-key")
    monkeypatch.setenv("GUARDOATH_APPLICATION_ID", "synthetic-tenant")
    captured = []
    redirect_handlers = []

    class Reply:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, limit):
            assert limit == 1_048_577
            return b'{"effective_action":"block","evaluation_status":"complete"}'

    class Opener:
        def open(self, request, timeout):
            captured.append(request)
            assert timeout == 20
            return Reply()

    def build_opener(*handlers):
        redirect_handlers.extend(handlers)
        return Opener()

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)
    exec(compile(examples[0], "docs/api-contract.md:python-example", "exec"), {})
    assert len(captured) == 1
    request = captured[0]
    assert request.full_url == "https://guardoath.com/v1/checks/input"
    assert request.get_method() == "POST"
    assert json.loads(request.data) == payload("synthetic-tenant", content="What is the return policy?", content_format="text")
    assert request.get_header("X-amz-content-sha256") == hashlib.sha256(request.data).hexdigest()
    assert request.get_header("X-guardoath-key") == "synthetic-example-key"
    assert len(redirect_handlers) == 1
    assert isinstance(redirect_handlers[0], urllib.request.HTTPRedirectHandler)
    for status in (301, 302, 303, 307, 308):
        # No replacement request is constructed with the credential, even for a same-origin redirect.
        for destination in ("https://different.invalid/collect", "https://guardoath.com/other"):
            assert redirect_handlers[0].redirect_request(request, None, status, "Redirect", {}, destination) is None
    assert len(captured) == 1
    output = capsys.readouterr().out
    assert "synthetic-example-key" not in output
    assert '"effective_action": "block"' in output
