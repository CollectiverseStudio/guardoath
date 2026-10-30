"""Structured contracts, format compatibility and value-free failure explanations."""
import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from guardoath.app import create_app
from guardoath.checkers import default_checkers
from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.evaluate import evaluate,load_suite
from guardoath.models import CheckRequest,Policy,Rule,Stage
from network_guard import deny_network

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture(scope='module')
def engine():
    return Engine(load_policies(ROOT/'policies/structure-batch.json'),default_checkers())


def test_entire_structure_group_matches_explicit_expectations(engine):
    report=asyncio.run(evaluate(load_suite(ROOT/'evals/structure.json'),engine))
    assert report['summary']=={'total':100,'matched':100,'mismatched':0,'incomplete':4,'simulated':0}
    # Reports exclude fields/values and distinguish optional skips from completed checks.
    encoded=json.dumps(report)
    assert 'builder@example.com' not in encoded
    assert 'ABC-123' not in encoded
    assert 'contact' not in encoded
    skipped=[c for c in report['cases'] if c['case_id'].startswith('text.optional')]
    assert all(c['checker_statuses']=={'schema':'skipped'} and c['evaluation_status']=='complete' for c in skipped)


@pytest.mark.parametrize('value,valid',[
 ('12:30:00Z',True),('12:30:00z',True),('12:30:00+05:30',True),('12:30:00-04:00',True),
 ('12:30:00.125Z',True),('00:00:00Z',True),('23:59:59Z',True),
 ('12:30:00',False),('25:00:00Z',False),('12:30:00+24:00',False),
 ('12:30:00+00:60',False),('12:30Z',False),('2026-10-28T12:30:00Z',False),
 ('12:30:00Z trailing',False),('12:30:00Z\n',False),('23:59:60Z',False),
])
def test_time_format_uses_explicit_offsets_and_documented_supported_subset(value,valid):
    policy=Policy(application_id='demo',version='v1',rules=(Rule(checker_id='schema'),),input_schema={'type':'string','format':'time'})
    result=asyncio.run(Engine([policy],default_checkers()).check(CheckRequest(
        application_id='demo',policy_version='v1',content=json.dumps(value),content_format='json'),Stage.INPUT))
    assert bool(result.findings) is not valid
    assert result.checker_results[0].checker_version=='2'


@pytest.mark.parametrize('schema,payload,explanation',[
 ({'type':'object','required':['private_field']},{},'A required field is missing.'),
 ({'type':'integer'},'private submitted value','A value has the wrong data type.'),
 ({'type':'object','additionalProperties':False},{'private_field':'private submitted value'},'An unexpected field is present.'),
 ({'enum':['private expected value']},'private submitted value','A value is outside the allowed choices.'),
 ({'type':'string','format':'email'},'private submitted value','A value does not match a supported format.'),
 ({'type':'array','maxItems':1},[1,2],'An array has too many items.'),
 ({'type':'array','uniqueItems':True},[1,1],'An array contains duplicate items.'),
 ({'type':'integer','multipleOf':5},7,'A number is not a configured multiple.'),
])
def test_safe_failure_reasons_never_expose_values_or_schema_names(schema,payload,explanation,caplog):
    policy=Policy(application_id='demo',version='v1',rules=(Rule(checker_id='schema'),),output_schema=schema)
    client=TestClient(create_app(engine=Engine([policy],default_checkers()),credentials={'test-key':'demo'}))
    response=client.post('/v1/checks/output',headers={'Authorization':'Bearer test-key'},json={
        'application_id':'demo','policy_version':'v1','content':json.dumps(payload),'content_format':'json'})
    assert response.status_code==200
    result=response.json()
    assert result['findings'][0]['rule_id']=='schema.invalid'
    assert result['findings'][0]['explanation']==explanation
    for value in ['private_field','private expected value','private submitted value']:
        assert value not in response.text+caplog.text


def test_schema_batch_and_local_references_use_no_network(monkeypatch):
    deny_network(monkeypatch,'Network attempted')
    engine=Engine(load_policies(ROOT/'policies/structure-batch.json'),default_checkers())
    suite=load_suite(ROOT/'evals/structure.json')
    assert asyncio.run(evaluate(suite,engine))['summary']['matched']==100


def test_real_api_uses_distinct_input_and_output_contracts(engine):
    client=TestClient(create_app(engine=engine,credentials={'test-key':'demo'}))
    suite=load_suite(ROOT/'evals/structure.json')
    request=suite.cases[0].request.model_dump(mode='json')
    headers={'Authorization':'Bearer test-key'}
    assert client.post('/v1/checks/input',headers=headers,json=request).json()['findings']==[]
    result=client.post('/v1/checks/output',headers=headers,json=request).json()
    assert result['findings'][0]['rule_id']=='schema.invalid'
    assert next(g for g in result['groups'] if g['group_id']=='structure')['status']=='findings'


@pytest.mark.parametrize('value,valid',[
 ('2026-10-28T12:30:00Z',True),('2026-10-28t12:30:00z',True),
 ('2026-10-28T12:30:00+05:30',True),('2026-10-28T12:30:00Z\n',False),
 (' 2026-10-28T12:30:00Z',False),('2026-10-28T12:30:00',False),
 ('2026-02-30T12:30:00Z',False),
])
def test_date_time_rejects_whitespace_and_missing_offsets(value,valid):
    policy=Policy(application_id='demo',version='v1',rules=(Rule(checker_id='schema'),),
                  input_schema={'type':'string','format':'date-time'})
    result=asyncio.run(Engine([policy],default_checkers()).check(CheckRequest(
        application_id='demo',policy_version='v1',content=json.dumps(value),content_format='json'),Stage.INPUT))
    assert bool(result.findings) is not valid
