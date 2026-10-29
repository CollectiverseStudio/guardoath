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
    for key in ['evaluation_status','recommended_action','effective_action']:assert data[key]==expected[key]
    assert {r['checker_id']:r['status'] for r in data['checker_results']}==expected['checker_statuses']
    assert sorted(f['rule_id'] for f in data['findings'])==sorted(expected['finding_rule_ids'])
    assert data['cost_usd']==0 and data['simulated'] is False

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

@pytest.mark.parametrize('name',NAMES)
@pytest.mark.parametrize('status',['flagged','error'])
def test_nonpassing_local_quality_checks_prevent_external_transfer(name,status):
    original=load_policies(ROOT/'policies'/f'{name}-batch.json')[0]
    external_rule=Rule(checker_id='remote',on_finding='block')
    policy=original.model_copy(update={'rules':original.rules+(external_rule,),'external_checks':'after_local_pass'})
    calls=[]
    async def remote(*args):
        calls.append(True)
        return CheckerResult(checker_id='remote',checker_version='1',status='passed')
    remote.checker_version='1'
    remote.external=True
    adapters=default_checkers();adapters['remote']=remote
    case=next(c for n,c in CASES if n==name and c['expected']['checker_statuses'][name]==status)
    result=asyncio.run(Engine([policy],adapters).check(CheckRequest(**{**case['request'],'policy_version':policy.version}),Stage.INPUT))
    assert not calls
    assert next(r for r in result.checker_results if r.checker_id=='remote').status=='skipped'
