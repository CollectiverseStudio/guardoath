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


@pytest.mark.parametrize('stage', [Stage.INPUT, Stage.OUTPUT])
def test_builtin_timestamp_check_avoids_uninterruptible_timer_and_still_blocks_late_result(monkeypatch, stage):
    """Only the exact local non-yielding timestamp checker may omit timer setup.

    The original monotonic completion deadline remains mandatory; this test
    makes a late 200-sample valid verdict fail closed without sleeping.
    """
    import json
    from pathlib import Path

    from guardoath.config import load_policies
    from guardoath.data_quality import timestamps_check

    policies = load_policies(Path(__file__).resolve().parents[1] / 'policies' / 'timestamps-batch.json')
    checker_engine = Engine(policies, {'timestamps': timestamps_check})
    request = CheckRequest(application_id='demo', policy_version='timestamps-enforce-v1',
        content=json.dumps({'samples': [
            {'timestamp': f'2026-11-04T00:{i // 60:02d}:{i % 60:02d}Z', 'value': i}
            for i in range(200)]}), content_format='json')

    def forbid_timer(_seconds):
        raise AssertionError('Non-yielding built-in timestamp checker must not schedule a timer')

    monkeypatch.setattr(engine_module.asyncio, 'timeout', forbid_timer)
    normal = asyncio.run(checker_engine.check(request, stage))
    assert normal.evaluation_status == 'complete'
    assert normal.checker_results[0].status == 'passed'
    assert normal.recommended_action == normal.effective_action == Action.ALLOW

    ticks = [0]
    def late_clock():
        ticks[0] += 1
        return 0.0 if ticks[0] <= 2 else 0.601

    monkeypatch.setattr(engine_module, 'perf_counter', late_clock)
    late = asyncio.run(checker_engine.check(request, stage))
    assert late.evaluation_status == 'incomplete'
    assert late.checker_results[0].status == 'error'
    assert late.checker_results[0].error_code == 'checker_timeout'
    assert late.checker_results[0].latency_ms == 601.0
    assert late.recommended_action == late.effective_action == Action.BLOCK


@pytest.mark.parametrize('stage', [Stage.INPUT, Stage.OUTPUT])
def test_replaced_timestamp_checker_retains_cancellable_timeout(monkeypatch, stage):
    """An asynchronous customer adapter must not inherit the local fast path."""
    from pathlib import Path

    from guardoath.config import load_policies
    from guardoath.data_quality import timestamps_check

    seen = []
    original_timeout = engine_module.asyncio.timeout
    def observed_timeout(seconds):
        seen.append(seconds)
        return original_timeout(seconds)
    monkeypatch.setattr(engine_module.asyncio, 'timeout', observed_timeout)

    async def replacement(request, policy, current_stage):
        return await timestamps_check(request, policy, current_stage)

    policies = load_policies(Path(__file__).resolve().parents[1] / 'policies' / 'timestamps-batch.json')
    checker_engine = Engine(policies, {'timestamps': replacement})
    request = CheckRequest(application_id='demo', policy_version='timestamps-enforce-v1',
        content='{"samples":[{"timestamp":"2026-11-04T00:00:00Z","value":1},'
                '{"timestamp":"2026-11-04T00:00:01Z","value":2}]}', content_format='json')
    result = asyncio.run(checker_engine.check(request, stage))
    assert result.evaluation_status == 'complete'
    assert result.checker_results[0].status == 'passed'
    assert seen == [policies[1].timeout_ms / 1000]
