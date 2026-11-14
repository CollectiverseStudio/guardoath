"""Monotonic completion checks supplement asyncio cancellation deadlines.

A non-yielding adapter or delayed event loop can complete before the timeout
callback runs. A fake monotonic clock reproduces this without sleeps or load.
"""
import asyncio

import pytest

import guardoath.engine as engine_module
from guardoath.engine import Engine
from guardoath.models import Action, CheckerResult, CheckRequest, Finding, Policy, Rule, Stage


@pytest.mark.parametrize('stage', [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize('mode', ['enforce', 'observe'])
@pytest.mark.parametrize('elapsed_ms', [499, 500, 501, 600])
def test_privacy_completion_obeys_monotonic_deadline(monkeypatch, stage, mode, elapsed_ms):
    clock = [0.0]
    monkeypatch.setattr(engine_module, 'perf_counter', lambda: clock[0])

    async def checker(*_args):
        clock[0] += elapsed_ms / 1000
        return CheckerResult(checker_id='pii', checker_version='synthetic', status='passed')

    policy = Policy(application_id='synthetic', version='v1', timeout_ms=500,
                    mode=mode, failure_action='flag',
                    rules=(Rule(checker_id='pii', required=True),))
    request = CheckRequest(application_id='synthetic', policy_version='v1',
                           content='synthetic', content_format='text')
    result = asyncio.run(Engine([policy], {'pii': checker}).check(request, stage))
    check = result.checker_results[0]
    if elapsed_ms <= 500:
        assert result.evaluation_status == 'complete'
        assert check.status == 'passed'
        assert result.effective_action == Action.ALLOW
    else:
        assert result.evaluation_status == 'incomplete'
        assert check.status == 'error'
        assert check.error_code == 'checker_timeout'
        assert check.latency_ms == elapsed_ms
        assert result.recommended_action == result.effective_action == Action.BLOCK


@pytest.mark.parametrize('stage', [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize('mode', ['enforce', 'observe'])
def test_late_redaction_recheck_never_releases_transformation(monkeypatch, stage, mode):
    clock = [0.0]
    monkeypatch.setattr(engine_module, 'perf_counter', lambda: clock[0])
    calls = []

    async def checker(request, *_args):
        calls.append(request.content)
        if len(calls) == 1:
            clock[0] += 0.01
            return CheckerResult(checker_id='pii', checker_version='synthetic', status='flagged',
                findings=(Finding(rule_id='pii.email_address', category='privacy',
                                  severity='high', explanation='Synthetic finding'),),
                transformed_content='PRIVATE-SYNTHETIC-TRANSFORM')
        clock[0] += 0.6
        return CheckerResult(checker_id='pii', checker_version='synthetic', status='passed')

    policy = Policy(application_id='synthetic', version='v1', timeout_ms=500, mode=mode,
                    rules=(Rule(checker_id='pii', on_finding=Action.REDACT),))
    request = CheckRequest(application_id='synthetic', policy_version='v1',
                           content='synthetic', content_format='text')
    result = asyncio.run(Engine([policy], {'pii': checker}).check(request, stage))
    assert len(calls) == 2
    assert result.evaluation_status == 'incomplete'
    assert result.effective_action == result.recommended_action == Action.BLOCK
    assert result.redaction.status == 'withheld'
    assert result.redaction.content is None
    assert result.redaction.validation_results[0].error_code == 'checker_timeout'
    assert result.checker_results[0].status == 'flagged'
    assert result.findings[0].rule_id == 'pii.email_address'


@pytest.mark.parametrize('stage', [Stage.INPUT, Stage.OUTPUT])
@pytest.mark.parametrize('mode', ['enforce', 'observe'])
def test_late_first_pass_transformation_is_discarded(monkeypatch, stage, mode):
    clock = [0.0]
    monkeypatch.setattr(engine_module, 'perf_counter', lambda: clock[0])
    calls = []

    async def checker(*_args):
        calls.append(True)
        clock[0] += 0.6
        return CheckerResult(checker_id='pii', checker_version='synthetic', status='flagged',
            findings=(Finding(rule_id='pii.email_address', category='privacy', severity='high',
                              explanation='Synthetic finding'),),
            transformed_content='PRIVATE-LATE-TRANSFORM')

    policy = Policy(application_id='synthetic', version='v1', timeout_ms=500, mode=mode,
                    rules=(Rule(checker_id='pii', on_finding=Action.REDACT),))
    request = CheckRequest(application_id='synthetic', policy_version='v1',
                           content='synthetic', content_format='text')
    result = asyncio.run(Engine([policy], {'pii': checker}).check(request, stage))
    assert len(calls) == 1
    assert result.evaluation_status == 'incomplete'
    assert result.effective_action == result.recommended_action == Action.BLOCK
    assert result.redaction.status == 'withheld'
    assert result.redaction.content is None
    assert result.checker_results[0].error_code == 'checker_timeout'
    assert 'PRIVATE-LATE-TRANSFORM' not in result.model_dump_json()


@pytest.mark.parametrize('stage', ['input', 'output'])
@pytest.mark.parametrize('mode', ['enforce', 'observe'])
def test_authenticated_tenant_api_blocks_late_privacy_verdict(monkeypatch, stage, mode):
    from fastapi.testclient import TestClient
    from guardoath.app import create_app, demo_engine
    from guardoath.hosted import TenantService
    from guardoath.tenant_record import TenantRecord, demo_key, key_hash, usage_month
    from guardoath.tenant_store import MemoryTenantStore

    clock = [0.0]
    monkeypatch.setattr(engine_module, 'perf_counter', lambda: clock[0])

    async def checker(*_args):
        clock[0] += 0.6
        return CheckerResult(checker_id='pii', checker_version='synthetic', status='passed')

    secret = 'synthetic-deadline-tenant-secret-0123456789'
    token = demo_key('deadline-tenant', secret)
    tenant = TenantRecord(tenant_id='deadline-tenant', display_name='Synthetic', demo=True,
        monthly_allowance=10, overage='block', key_hashes=(key_hash(token),), mode=mode, timeout_ms=500,
        failure_action='flag', input_check_pii='flag', output_check_pii='flag')
    store = MemoryTenantStore((tenant,))
    service = TenantService(store, {'pii': checker}, secret)
    app = create_app(engine=demo_engine(), credentials={}, tenants=service)
    with TestClient(app) as client:
        response = client.post('/v1/checks/' + stage,
            headers={'Authorization': 'Bearer ' + token},
            json={'application_id': tenant.tenant_id, 'content': 'synthetic', 'content_format': 'text'})
    assert response.status_code == 200
    result = response.json()
    assert result['evaluation_status'] == 'incomplete'
    assert result['effective_action'] == result['recommended_action'] == 'block'
    assert result['checker_results'][0]['error_code'] == 'checker_timeout'
    assert result['checker_results'][0]['latency_ms'] == 600
    assert store.get(tenant.tenant_id).used(usage_month()) == 1
    assert store.get(tenant.tenant_id).timeout_ms == 500
