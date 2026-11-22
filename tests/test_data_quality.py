"""Local data-quality contracts through the API, including safe failure paths."""
import asyncio
import hashlib
import json
from pathlib import Path
import socket

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from guardoath.app import create_app
from guardoath.budgets import ResourceLimits
from guardoath.checkers import default_checkers
from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.models import CheckRequest, CheckerResult, IntegrityPolicy, Policy, Rule, Stage

ROOT=Path(__file__).resolve().parents[1]
NAMES=['ledger','timestamps','graph','integrity']
CASES=[(name,case) for name in NAMES for case in json.loads((ROOT/'evals'/f'{name}.json').read_text())['cases']]

@pytest.fixture(scope='module')
def clients():
    return {name:TestClient(create_app(engine=Engine(load_policies(ROOT/'policies'/f'{name}-batch.json'),default_checkers()),credentials={'test-key':'demo'},limits=ResourceLimits(burst=1000,checks_per_minute=100000))) for name in NAMES}

@pytest.mark.parametrize('name,case',CASES,ids=[n+':'+c['case_id'] for n,c in CASES])
def test_data_quality_api_expectations(clients,name,case):
    response=clients[name].post('/v1/checks/'+case['stage'],headers={'Authorization':'Bearer test-key'},json=case['request'])
    assert response.status_code==200
    data=response.json();expected=case['expected']
    # Keep strict expectations; report only bounded checker codes and timings
    # on intermittent CI failures, never private submitted content.
    diagnostic = [(r.get('checker_id'), r.get('status'), r.get('error_code'),
                   r.get('latency_ms')) for r in data['checker_results']]
    for key in ['evaluation_status','recommended_action','effective_action']:
        assert data[key]==expected[key], (
            f"{name}:{case['case_id']} {key}; checker_status_code_latency={diagnostic}")
    assert {r['checker_id']:r['status'] for r in data['checker_results']}==expected['checker_statuses']
    assert sorted(f['rule_id'] for f in data['findings'])==sorted(expected['finding_rule_ids'])
    assert data['cost_usd']==0 and data['simulated'] is False

@pytest.mark.parametrize('stage', list(Stage))
def test_non_json_ledger_skip_does_not_depend_on_checker_deadline(stage, monkeypatch):
    """Known inapplicable built-in check is not misreported as an error under scheduler lag."""
    import guardoath.engine as engine_module

    policy = load_policies(ROOT / 'policies' / 'ledger-batch.json')[0]
    # Simulate a large scheduler/clock jump around Engine.check. The bypass
    # applies only to the original built-in ledger checker, not a replacement.
    elapsed = iter(range(100000))
    monkeypatch.setattr(engine_module, 'perf_counter', lambda: next(elapsed) / 10)
    request = CheckRequest(
        application_id='demo', policy_version='ledger-v1',
        content='This is not a ledger packet.', content_format='text',
    )
    response = asyncio.run(Engine([policy], default_checkers()).check(request, stage))
    assert len(response.checker_results) == 1
    assert response.checker_results[0].status == 'skipped'
    assert response.checker_results[0].error_code == 'ledger_json_required'



@pytest.mark.parametrize('stage', list(Stage))
def test_builtin_json_ledger_avoids_redundant_timer_but_blocks_late_results(stage, monkeypatch):
    """Only the exact non-yielding ledger checker skips timer setup, not its deadline."""
    import guardoath.engine as engine_module
    from guardoath.data_quality import ledger_check

    policy = load_policies(ROOT / 'policies' / 'ledger-batch.json')[0]
    case = next(case for name, case in CASES
                if name == 'ledger' and case['expected']['checker_statuses']['ledger'] == 'passed')
    request = CheckRequest(**case['request'])
    checker_engine = Engine([policy], {'ledger': ledger_check})

    def forbid_timer(_seconds):
        raise AssertionError('Built-in non-yielding ledger must not schedule a timer')

    monkeypatch.setattr(engine_module.asyncio, 'timeout', forbid_timer)
    monkeypatch.setattr(engine_module, 'perf_counter', lambda: 0.0)
    normal = asyncio.run(checker_engine.check(request, stage))
    assert normal.evaluation_status == 'complete'
    assert normal.checker_results[0].status == 'passed'

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


@pytest.mark.parametrize('stage', list(Stage))
def test_replacement_json_ledger_retains_cancellable_timeout(stage, monkeypatch):
    """User-provided async adapters cannot inherit the built-in shortcut."""
    import guardoath.engine as engine_module
    from guardoath.data_quality import ledger_check

    policy = load_policies(ROOT / 'policies' / 'ledger-batch.json')[0]
    case = next(case for name, case in CASES
                if name == 'ledger' and case['expected']['checker_statuses']['ledger'] == 'passed')
    seen = []
    original_timeout = engine_module.asyncio.timeout
    def observed_timeout(seconds):
        seen.append(seconds)
        return original_timeout(seconds)
    monkeypatch.setattr(engine_module.asyncio, 'timeout', observed_timeout)

    async def replacement(request, policy, current_stage):
        return await ledger_check(request, policy, current_stage)

    response = asyncio.run(Engine([policy], {'ledger': replacement}).check(
        CheckRequest(**case['request']), stage))
    assert response.evaluation_status == 'complete'
    assert response.checker_results[0].status == 'passed'
    assert seen == [policy.timeout_ms / 1000]

@pytest.mark.parametrize('name',NAMES)
@pytest.mark.parametrize('stage',list(Stage))
def test_checks_never_need_a_network_connection(name,stage,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('Network must not be used')
    monkeypatch.setattr(socket,'create_connection',forbidden)
    case=next(c for n,c in CASES if n==name and c['expected']['checker_statuses'][name]=='passed')
    result=asyncio.run(Engine(load_policies(ROOT/'policies'/f'{name}-batch.json'),default_checkers()).check(CheckRequest(**case['request']),stage))
    assert result.evaluation_status=='complete' and not result.findings

@pytest.mark.parametrize('name',NAMES)
def test_checker_and_configuration_must_be_selected_together(name):
    original=load_policies(ROOT/'policies'/f'{name}-batch.json')[0]
    for broken in [original.model_copy(update={name:None}),original.model_copy(update={'rules':()})]:
        with pytest.raises(ValueError,match='configuration'):Engine([broken],default_checkers())

@pytest.mark.parametrize('name,field,value',[
 ('ledger','decimal_places',5),('ledger','decimal_places',True),('ledger','max_entries',201),('ledger','currency','usd'),
 ('timestamps','allow_equal','false'),('timestamps','max_samples',501),('graph','max_nodes',201),('graph','max_edges',1001),
 ('integrity','sha256','not-a-digest'),('integrity','max_bytes',262145),('integrity','encoding','url'),
])
def test_configuration_is_bounded_and_strict(name,field,value):
    original=load_policies(ROOT/'policies'/f'{name}-batch.json')[0].model_dump()
    original[name][field]=value
    with pytest.raises(ValidationError):Policy.model_validate(original)

@pytest.mark.parametrize('stage',list(Stage))
def test_digest_reference_is_private_and_not_caller_controlled(stage):
    digest=hashlib.sha256(b'approved').hexdigest()
    policy=Policy(application_id='demo',version='private',rules=(Rule(checker_id='integrity',on_finding='block'),),integrity=IntegrityPolicy(sha256=digest.upper()))
    assert policy.integrity.sha256==digest
    client=TestClient(create_app(engine=Engine([policy],default_checkers()),credentials={'test-key':'demo'}))
    headers={'Authorization':'Bearer test-key'}
    metadata=client.get('/v1/applications/current',headers=headers).text
    assert digest not in metadata.lower()
    request={'application_id':'demo','policy_version':'private','content':'private submitted bytes','request_context':'private context'}
    response=client.post('/v1/checks/'+stage.value,headers=headers,json=request)
    assert response.status_code==200
    assert response.json()['checker_results'][0]['status']=='flagged'
    for secret in [digest,'private submitted bytes','private context']:assert secret not in response.text
    response=client.post('/v1/checks/'+stage.value,headers=headers,json={**request,'sha256':hashlib.sha256(request['content'].encode()).hexdigest()})
    assert response.status_code==422

