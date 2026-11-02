"""Actual tenant compiler/engine, isolated one-call ASGI surface, fake AI only."""
import asyncio

from fastapi.testclient import TestClient
import pytest

from guardoath.gateway_prototype.api import create_prototype_app
from guardoath.gateway_prototype.service import PrototypeGateway, ProviderBinding, ProviderReply
from guardoath.models import CheckerResult, Finding, Stage
from guardoath.tenant_record import TenantRecord


class Provider:
    def __init__(self, text='approved reply', *, delay=0, error=None, mutate=None, tools=()):
        self.text, self.delay, self.error, self.mutate, self.tools = text, delay, error, mutate, tools
        self.calls = []

    async def generate(self, *, message, model):
        self.calls.append((message, model))
        if self.mutate:
            self.mutate()
        await asyncio.sleep(self.delay)
        if self.error:
            raise RuntimeError(self.error)
        return ProviderReply(text=self.text, input_tokens=7, output_tokens=9, tool_calls=self.tools)


def checker(identifier, events, *, redact=False, error=False):
    async def run(request, policy, stage):
        events.append((identifier, stage.value, request.content))
        if error:
            raise RuntimeError('private validation error')
        matched = 'PRIVATE' in request.content
        return CheckerResult(checker_id=identifier, checker_version='fixture-1',
            status='flagged' if matched else 'passed',
            findings=(Finding(rule_id='fixture.private', category='fixture', severity='high', explanation='Fixture finding'),) if matched else (),
            transformed_content=request.content.replace('PRIVATE', '[REDACTED]') if matched and redact else None)
    run.checker_version = 'fixture-1'
    return run


def record(owner='tenant-a', **changes):
    return TenantRecord(**{'tenant_id': owner, 'display_name': owner, 'revision': 2, 'check_secrets': 'block', **changes})


def setup(record_a=None, provider=None, *, records=None, checkers=None, binding_changes=None, **kwargs):
    events = []
    provider = provider or Provider()
    record_a = record_a or record()
    records = records or {'key-a': record_a}
    checkers = checkers if checkers is not None else {'secrets': checker('secrets', events)}
    binding = ProviderBinding(record_a.tenant_id, provider, **(binding_changes or {}))
    gateway = PrototypeGateway(checkers, {record_a.tenant_id: binding}, **kwargs)
    app = create_prototype_app(gateway, records.get)
    return TestClient(app), gateway, provider, events, records


def post(client, message='hello', request_id='request-1', **extra):
    return client.post('/v1/interactions', headers={'X-GuardOath-Key': 'key-a'},
                       json={'message': message, 'request_id': request_id, **extra})


def test_single_call_runs_input_provider_output_with_tenant_source_actions():
    client, _, provider, events, _ = setup()
    result = post(client).json()
    assert result['status'] == 'completed'
    assert result['response'] == 'approved reply'
    assert provider.calls == [('hello', 'tenant-default')]
    assert events == [('secrets', 'input', 'hello'), ('secrets', 'output', 'approved reply')]
    assert result['tenant_policy_revision'] == 2
    assert len(result['policy_digest']) == 64
    assert result['catalog_version'] == 'prototype-1'
    assert result['usage'] == {'input_tokens': 7, 'output_tokens': 9}
    assert all(c['action'] == 'block' and c['checker_version'] == 'fixture-1' for c in result['checks'])


def test_input_block_causes_zero_provider_calls():
    client, _, provider, _, _ = setup()
    result = post(client, 'PRIVATE input').json()
    assert result['status'] == 'input_blocked'
    assert result['response'] is None
    assert result['provider_called'] is False
    assert provider.calls == []


@pytest.mark.parametrize('action', ['allow', 'flag'])
def test_allow_and_flag_still_execute_and_report_findings(action):
    tenant = record(check_secrets=action)
    client, _, provider, events, _ = setup(tenant)
    result = post(client, 'PRIVATE input').json()
    assert result['status'] == 'completed'
    assert len(provider.calls) == 1
    assert events[0][2] == 'PRIVATE input'
    assert result['checks'][0]['status'] == 'flagged'
    assert result['checks'][0]['action'] == action
    assert result['checks'][0]['rule_ids'] == ['fixture.private']


def test_disabled_check_does_not_run():
    events = []
    tenant = record(check_secrets=None, check_lexical_guards='allow')
    client, _, _, _, _ = setup(tenant, checkers={'secrets': checker('secrets', events, error=True),
                                               'lexical_guards': checker('lexical_guards', events)})
    assert post(client, 'PRIVATE input').json()['status'] == 'completed'
    assert {row[0] for row in events} == {'lexical_guards'}


def test_redacted_input_and_output_are_revalidated_before_forwarding_or_release():
    events = []
    tenant = record(check_secrets=None, check_pii='redact')
    client, _, provider, _, _ = setup(tenant, Provider('answer PRIVATE'), checkers={'pii': checker('pii', events, redact=True)})
    result = post(client, 'input PRIVATE').json()
    assert result['status'] == 'completed'
    assert provider.calls == [('input [REDACTED]', 'tenant-default')]
    assert result['response'] == 'answer [REDACTED]'
    assert events == [('pii', 'input', 'input PRIVATE'), ('pii', 'input', 'input [REDACTED]'),
                      ('pii', 'output', 'answer PRIVATE'), ('pii', 'output', 'answer [REDACTED]')]


def test_failed_redaction_is_withheld_before_provider():
    events = []
    tenant = record(check_secrets=None, check_pii='redact')
    client, _, provider, _, _ = setup(tenant, checkers={'pii': checker('pii', events)})
    assert post(client, 'PRIVATE').json()['status'] == 'input_blocked'
    assert provider.calls == []


def test_blocked_output_not_in_body_logs_or_safe_metadata(caplog):
    rejected = 'PRIVATE provider output'
    client, _, provider, _, _ = setup(provider=Provider(rejected))
    response = post(client)
    assert response.json()['status'] == 'output_withheld'
    assert response.json()['response'] is None
    assert rejected not in response.text
    assert rejected not in caplog.text
    assert response.json()['usage'] == {'input_tokens': 7, 'output_tokens': 9}
    assert len(provider.calls) == 1


@pytest.mark.parametrize('available,error', [(False, False), (True, True)])
def test_required_unavailable_or_failed_check_blocks_before_provider_even_failure_flag(available, error):
    events = []
    tenant = record(failure_action='flag')
    checks = {'secrets': checker('secrets', events, error=error)} if available else {}
    client, _, provider, _, _ = setup(tenant, checkers=checks)
    result = post(client).json()
    assert result['status'] == 'validation_incomplete'
    assert result['response'] is None
    assert provider.calls == []


def test_immutable_snapshot_survives_record_change_mid_provider_call():
    records = {'key-a': record()}
    def mutate():
        records['key-a'] = TenantRecord.model_validate({**records['key-a'].model_dump(), 'revision': 3, 'check_secrets': 'allow'})
    client, _, _, _, _ = setup(records=records, provider=Provider('PRIVATE reply', mutate=mutate))
    result = post(client).json()
    assert records['key-a'].revision == 3
    assert result['tenant_policy_revision'] == 2
    assert result['status'] == 'output_withheld'
    assert result['checks'][-1]['action'] == 'block'


def test_two_tenants_same_content_different_authoritative_actions():
    events = []
    a = record()
    b = record('tenant-b', check_secrets='allow')
    pa, pb = Provider(), Provider()
    gateway = PrototypeGateway({'secrets': checker('secrets', events)},
        {'tenant-a': ProviderBinding('tenant-a', pa), 'tenant-b': ProviderBinding('tenant-b', pb)})
    client = TestClient(create_prototype_app(gateway, {'key-a': a, 'key-b': b}.get))
    assert post(client, 'PRIVATE').json()['status'] == 'input_blocked'
    result = client.post('/v1/interactions', headers={'Authorization': 'Bearer key-b'},
        json={'message': 'PRIVATE', 'request_id': 'request-1'}).json()
    assert result['status'] == 'completed'
    assert pa.calls == [] and len(pb.calls) == 1


@pytest.mark.parametrize('field,value', [('policy_version', 'allow-all'), ('profile', 'safe'),
    ('validation_codes', []), ('tenant_id', 'tenant-b'), ('provider_url', 'https://example.invalid'),
    ('provider_secret', 'private credential'), ('tool_authorized', True), ('validations', {})])
def test_request_cannot_override_tenant_authority_or_provider_configuration(field, value):
    client, _, provider, _, _ = setup()
    response = post(client, **{field: value})
    assert response.status_code == 422
    assert 'private credential' not in response.text
    assert provider.calls == []


@pytest.mark.parametrize('changes', [{'default_profile': 'other-profile'}, {'mode': 'observe'}])
def test_profile_or_observe_tenant_requires_explicit_migration(changes):
    client, _, provider, _, _ = setup(record(**changes))
    assert post(client).status_code == 409
    assert provider.calls == []


@pytest.mark.parametrize('binding', [{'entitled': False}, {'allowed_models': ('other-model',)}])
def test_provider_entitlement_and_model_allowlist(binding):
    client, _, provider, _, _ = setup(binding_changes=binding)
    assert post(client).status_code == 403
    assert provider.calls == []


def test_trusted_provider_budget_is_not_optional_content_policy():
    client, _, provider, _, _ = setup(record(check_secrets='allow'), binding_changes={'remaining_calls': 0})
    assert post(client, 'PRIVATE').status_code == 429
    assert provider.calls == []


def test_tool_proposal_is_never_executed_by_allowing_content():
    client, _, provider, _, _ = setup(record(check_secrets='allow'), provider=Provider('PRIVATE', tools=('delete-account',)))
    result = post(client).json()
    assert result['status'] == 'tool_withheld'
    assert result['response'] is None
    assert len(provider.calls) == 1


@pytest.mark.parametrize('provider,status', [(Provider(delay=0.05), 'provider_timeout'),
                                           (Provider(error='PRIVATE provider error'), 'provider_error')])
def test_provider_timeout_and_error_are_safe_and_idempotent(provider, status, caplog):
    client, _, _, _, _ = setup(provider=provider, provider_timeout=0.01)
    first = post(client)
    assert first.json()['status'] == status
    assert first.json()['provider_called'] is True and first.json()['usage'] is None
    assert post(client).json()['replayed'] is True
    assert len(provider.calls) == 1
    assert 'PRIVATE provider error' not in first.text + caplog.text


def test_successful_retry_reuses_operation_but_conflicting_body_does_not_call_provider():
    client, _, provider, _, _ = setup()
    assert post(client).json()['replayed'] is False
    assert post(client).json()['replayed'] is True
    assert post(client, message='different').status_code == 409
    assert len(provider.calls) == 1


def test_authentication_and_envelope_bound_prevent_provider_calls():
    client, _, provider, _, _ = setup()
    assert client.post('/v1/interactions', json={'message': 'hello', 'request_id': 'id'}).status_code == 401
    assert client.post('/v1/interactions', headers={'X-GuardOath-Key': 'key-a'}, content=b'x'*65537).status_code == 413
    assert post(client, message='x'*32001).status_code == 422
    assert provider.calls == []


def test_real_local_pii_checker_blocks_email_in_real_engine():
    from guardoath.checkers import default_checkers
    tenant = record(check_secrets=None, check_pii='block')
    client, _, provider, _, _ = setup(tenant, checkers=default_checkers(enable_pii=True))
    result = post(client, 'Contact alice@example.com').json()
    assert result['status'] == 'input_blocked'
    assert provider.calls == []


def test_concurrent_idempotent_retries_do_not_duplicate_provider_call():
    from guardoath.gateway_prototype.service import InteractionRequest
    _, gateway, provider, _, _ = setup(provider=Provider(delay=0.01))
    async def run():
        request = InteractionRequest(message='hello', request_id='concurrent')
        return await asyncio.gather(gateway.interact(record(), request), gateway.interact(record(), request))
    first, second = asyncio.run(run())
    assert first.status == second.status == 'completed'
    assert [first.replayed, second.replayed] == [False, True]
    assert len(provider.calls) == 1


def test_request_budget_withholds_slow_validation_before_provider():
    async def slow(request, policy, stage):
        await asyncio.sleep(0.05)
        return CheckerResult(checker_id='secrets', checker_version='fixture-1', status='passed')
    client, _, provider, _, _ = setup(checkers={'secrets': slow}, request_timeout=0.01, provider_timeout=0.005)
    result = post(client).json()
    assert result['status'] == 'gateway_timeout'
    assert result['response'] is None
    assert provider.calls == []


def test_queue_and_execution_share_one_request_deadline():
    from guardoath.gateway_prototype.service import InteractionRequest
    _, gateway, provider, _, _ = setup(provider=Provider(delay=0.02), provider_timeout=0.025, request_timeout=0.03)
    async def run():
        await gateway._lock.acquire()
        task = asyncio.create_task(gateway.interact(record(), InteractionRequest(message='hello', request_id='queued')))
        await asyncio.sleep(0.02)
        gateway._lock.release()
        return await task
    result = asyncio.run(run())
    assert result.status in {'gateway_timeout', 'provider_timeout', 'gateway_busy'}
    assert result.response is None
    assert len(provider.calls) <= 1


def test_query_profile_override_is_explicitly_rejected():
    client, _, provider, _, _ = setup()
    response = client.post('/v1/interactions?policy_version=allow-all', headers={'X-GuardOath-Key': 'key-a'},
                           json={'message': 'PRIVATE', 'request_id': 'query'})
    assert response.status_code == 422 and provider.calls == []


@pytest.mark.parametrize('entitled', ['true', 1, None])
def test_trusted_entitlement_flags_are_strict_booleans(entitled):
    with pytest.raises(ValueError):
        ProviderBinding('tenant-a', Provider(), entitled=entitled)


@pytest.mark.parametrize('timeout', [True, float('nan'), float('inf'), 0, 6])
def test_trusted_request_deadline_configuration_is_bounded(timeout):
    with pytest.raises(ValueError):
        PrototypeGateway({}, {}, request_timeout=timeout)


def test_provider_binding_cannot_be_attached_to_wrong_tenant():
    with pytest.raises(ValueError, match='owner'):
        PrototypeGateway({}, {'tenant-b': ProviderBinding('tenant-a', Provider())})


def test_cancelled_operation_does_not_repeat_provider_on_same_id_retry():
    from guardoath.gateway_prototype.service import InteractionRequest
    class CancellableProvider:
        calls = 0
        async def generate(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                started.set()
                await asyncio.sleep(60)
            return ProviderReply(text='approved', input_tokens=1, output_tokens=1)
    async def run():
        nonlocal started
        started = asyncio.Event()
        provider = CancellableProvider()
        _, gateway, _, _, _ = setup(provider=provider)
        request = InteractionRequest(message='hello', request_id='cancelled')
        operation = asyncio.create_task(gateway.interact(record(), request))
        await started.wait()
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        retry = await gateway.interact(record(), request)
        assert retry.status == 'operation_cancelled' and retry.replayed
        assert retry.response is None and retry.provider_called
        assert retry.usage is None
        assert provider.calls == 1
    started = None
    asyncio.run(run())
