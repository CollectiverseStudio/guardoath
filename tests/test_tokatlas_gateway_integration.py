"""Tokatlas -> actual isolated GuardOath gateway, no external traffic or charges."""
import json

import pytest

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
        return json.dumps({"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": answer}}],
                           "usage": {"prompt_tokens": 3, "completion_tokens": 5}}).encode()
    adapter = TokatlasChatAdapter(model_limits=(('gpt-5', 128),), api_key="offline-test-credential", transport=fake_transport)
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
    assert sent == [{"model": "gpt-5", "messages": [{"role": "user", "content": "safe input"}], "stream": False, "max_completion_tokens": 128, "n": 1, "store": False}]
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


@pytest.mark.parametrize('wrapped', [False, True])
def test_redacted_input_is_revalidated_before_direct_or_wrapped_completion(wrapped):
    events = []
    async def pii(request, policy, stage):
        events.append(('check', stage.value, request.content))
        matched = 'PRIVATE' in request.content
        return CheckerResult(checker_id='pii', checker_version='fixture-redaction-1',
            status='flagged' if matched else 'passed',
            findings=(Finding(rule_id='fixture.private', category='test', severity='high', explanation='Fixture'),) if matched else (),
            transformed_content=request.content.replace('PRIVATE', '[REDACTED]') if matched else None)
    def transport(url, headers, body, timeout):
        events.append(('transport', json.loads(body)))
        reply = {'choices': [{'finish_reason': 'stop', 'message': {'content': 'approved reply'}}],
                 'usage': {'prompt_tokens': 4, 'completion_tokens': 2}}
        return json.dumps({'data': reply} if wrapped else reply).encode()
    tenant = TenantRecord(tenant_id='tenant-a', display_name='Tenant A', check_pii='redact')
    adapter = TokatlasChatAdapter(model_limits=(('gpt-5', 128),), api_key='offline-private-credential', transport=transport)
    gateway = PrototypeGateway({'pii': pii}, {'tenant-a': ProviderBinding('tenant-a', adapter, allowed_models=('gpt-5',))})
    client = TestClient(create_prototype_app(gateway, lambda key: tenant if key == 'tenant-test-key' else None))
    result = post(client, 'PRIVATE question', 'redacted').json()
    assert result['status'] == 'completed' and result['response'] == 'approved reply'
    assert events[:2] == [('check', 'input', 'PRIVATE question'), ('check', 'input', '[REDACTED] question')]
    assert events[2][0] == 'transport'
    assert events[2][1]['messages'] == [{'role': 'user', 'content': '[REDACTED] question'}]
    assert 'PRIVATE' not in json.dumps(events[2][1])
    assert events[3] == ('check', 'output', 'approved reply')


@pytest.mark.parametrize('usage', [{}, {'prompt_tokens': True, 'completion_tokens': 1},
                                   {'prompt_tokens': 1, 'completion_tokens': -1}])
def test_invalid_measured_usage_is_not_a_clean_gateway_completion(usage, caplog):
    # Coroutines still run under the unchanged 500 ms gateway deadline,
    # without Windows thread-start scheduling changing the expected parse error.
    # This tests the actual Tokatlas parser, not a mocked ProviderReply.
    async def transport(*args):
        return json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': 'private-rejected-output'}}], 'usage': usage}).encode()
    tenant = TenantRecord(tenant_id='tenant-a', display_name='Tenant A', check_secrets='block')
    gateway = PrototypeGateway({'secrets': fixture_checker}, {'tenant-a': ProviderBinding('tenant-a',
        TokatlasChatAdapter(model_limits=(('gpt-5', 128),), api_key='offline-private-credential', transport=transport), allowed_models=('gpt-5',))})
    client = TestClient(create_prototype_app(gateway, lambda key: tenant if key == 'tenant-test-key' else None))
    response = post(client, 'safe input', 'invalid-usage')
    assert response.json()['status'] == 'provider_error'
    assert response.json()['response'] is None and response.json()['usage'] is None
    assert 'private-rejected-output' not in response.text + caplog.text


def test_tool_proposals_are_withheld_through_real_gateway():
    def transport(*args):
        return json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': None, 'tool_calls': [
            {'function': {'name': 'never-execute', 'arguments': 'private-arguments'}}]}}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1}}).encode()
    tenant = TenantRecord(tenant_id='tenant-a', display_name='Tenant A', check_secrets='allow')
    gateway = PrototypeGateway({'secrets': fixture_checker}, {'tenant-a': ProviderBinding('tenant-a',
        TokatlasChatAdapter(model_limits=(('gpt-5', 128),), api_key='offline-private-credential', transport=transport), allowed_models=('gpt-5',))})
    client = TestClient(create_prototype_app(gateway, lambda key: tenant if key == 'tenant-test-key' else None))
    response = post(client, 'safe input', 'tool')
    assert response.json()['status'] == 'tool_withheld' and response.json()['response'] is None
    assert 'never-execute' not in response.text and 'private-arguments' not in response.text


@pytest.mark.parametrize('field', ['max_tokens', 'max_completion_tokens', 'generation_budget'])
def test_end_user_cannot_supply_provider_generation_budget(field):
    client, sent = make_client()
    response = client.post('/v1/interactions', headers={'X-GuardOath-Key': 'tenant-test-key'},
        json={'message': 'safe input', 'model': 'gpt-5', 'request_id': 'budget-override', field: 1})
    assert response.status_code == 422 and sent == []
