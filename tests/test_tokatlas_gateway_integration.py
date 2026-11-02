"""Tokatlas -> actual isolated GuardOath gateway, no external traffic or charges."""
import json

from fastapi.testclient import TestClient

from guardoath.gateway_prototype.api import create_prototype_app
from guardoath.gateway_prototype.service import PrototypeGateway, ProviderBinding
from guardoath.gateway_prototype.tokatlas import TokatlasChatAdapter
from guardoath.models import CheckerResult, Finding
from guardoath.tenant_record import TenantRecord


def fixture_checker(request, policy, stage):
    flagged = "PRIVATE" in request.content
    async def build():
        return CheckerResult(
            checker_id="secrets", checker_version="test-1",
            status="flagged" if flagged else "passed",
            findings=(Finding(rule_id="fixture.private", category="test", severity="high",
                              explanation="Sensitive marker"),) if flagged else (),
        )
    return build()


def make_client(*, action="block", answer="approved answer", fail=False):
    sent = []
    def fake_transport(url, headers, body, timeout):
        payload = json.loads(body)
        sent.append(payload)
        if fail:
            return b'{"choices":[]}'
        return json.dumps({"choices": [{"message": {"role": "assistant", "content": answer}}],
                           "usage": {"prompt_tokens": 3, "completion_tokens": 5}}).encode()
    adapter = TokatlasChatAdapter(api_key="offline-test-credential", transport=fake_transport)
    tenant = TenantRecord(tenant_id="tenant-a", display_name="Tenant A", check_secrets=action)
    binding = ProviderBinding(tenant_id="tenant-a", provider=adapter, allowed_models=("gpt-5",))
    gateway = PrototypeGateway({"secrets": fixture_checker}, {"tenant-a": binding})
    app = create_prototype_app(gateway, lambda key: tenant if key == "tenant-test-key" else None)
    return TestClient(app), sent


def post(client, message, request_id):
    return client.post("/v1/interactions", headers={"X-GuardOath-Key": "tenant-test-key"},
                       json={"message": message, "model": "gpt-5", "request_id": request_id})


def test_blocked_input_never_reaches_tokatlas_transport():
    client, sent = make_client()
    result = post(client, "PRIVATE question", "tokatlas-block").json()
    assert result["status"] == "input_blocked"
    assert result["response"] is None
    assert sent == []


def test_allowed_input_reaches_tokatlas_and_passes_output_checks():
    client, sent = make_client()
    result = post(client, "safe input", "tokatlas-success").json()
    assert result["status"] == "completed"
    assert result["response"] == "approved answer"
    assert result["usage"] == {"input_tokens": 3, "output_tokens": 5}
    assert sent == [{"model": "gpt-5", "messages": [{"role": "user", "content": "safe input"}], "stream": False}]
    assert [row["stage"] for row in result["checks"]] == ["input", "output"]


def test_tokatlas_unsafe_output_is_withheld():
    client, sent = make_client(answer="PRIVATE upstream response")
    response = post(client, "safe input", "tokatlas-output")
    assert response.json()["status"] == "output_withheld"
    assert response.json()["response"] is None
    assert "PRIVATE upstream response" not in response.text
    assert len(sent) == 1


def test_tokatlas_malformed_response_is_sanitized():
    client, sent = make_client(fail=True)
    result = post(client, "safe input", "tokatlas-failure").json()
    assert result["status"] == "provider_error"
    assert result["response"] is None
    assert result["usage"] is None
    assert len(sent) == 1


def test_disallowed_model_causes_no_upstream_call():
    client, sent = make_client()
    response = client.post("/v1/interactions", headers={"X-GuardOath-Key": "tenant-test-key"},
        json={"message": "safe input", "model": "other-model", "request_id": "tokatlas-model"})
    assert response.status_code == 403
    assert sent == []
