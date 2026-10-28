"""Exact submitted-number comparisons and labeled-section regression coverage."""
import asyncio
from decimal import localcontext
import json
from pathlib import Path
import socket

import pytest
from fastapi.testclient import TestClient

from valinator.app import create_app
from valinator.checkers import default_checkers
from valinator.config import load_policies
from valinator.engine import Engine
from valinator.evaluate import evaluate,load_suite
from valinator.models import CheckRequest,Stage

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture(scope='module')
def engine():
    return Engine(load_policies(ROOT/'policies/business-batch.json'),default_checkers())


def check(engine,content,op='le',stage=Stage.INPUT):
    return asyncio.run(engine.check(CheckRequest(application_id='demo',policy_version='business-number-'+op+'-v1',content=content,content_format='json'),stage))


def test_whole_business_group_matches_explicit_expectations(engine):
    report=asyncio.run(evaluate(load_suite(ROOT/'evals/business.json'),engine))
    assert report['summary']=={'total':100,'matched':100,'mismatched':0,'incomplete':6,'simulated':0}
    assert '1.0000000000000001' not in json.dumps(report)
    assert 'Customer requests return instructions.' not in json.dumps(report)


@pytest.mark.parametrize('content,op,passed',[
 ('{"left":1.0000000000000001,"right":1.0}','le',False),
 ('{"left":1.0,"right":1.0000000000000001}','le',True),
 ('{"left":0.10000000000000001,"right":0.1}','eq',False),
 ('{"left":1e-400,"right":0}','gt',True),
 ('{"left":-1e-400,"right":0}','lt',True),
 ('{"left":1e-400,"right":0}','eq',False),
 ('{"left":-0.0,"right":0}','eq',True),
 ('{"left":9007199254740993,"right":9007199254740992}','gt',True),
 ('{"left":1.000000000000000000000000000001,"right":1.000000000000000000000000000002}','lt',True),
])
def test_raw_decimal_tokens_are_compared_without_binary_rounding(engine,content,op,passed):
    with localcontext() as context:
        context.prec=2  # Construction/comparison must not round to arithmetic precision.
        result=check(engine,content,op)
    assert bool(result.findings) is not passed
    assert result.evaluation_status=='complete'
    assert result.checker_results[0].checker_version=='2'


@pytest.mark.parametrize('content',[
 '{"left":NaN,"right":2}','{"left":1e400,"right":2}',
 '{"left":1,"left":2,"right":3}',r'{"left":1,"\u006ceft":2,"right":3}',
 '{"left":'+('1'*129)+',"right":2}',
 '{"left":1e-'+('9'*60)+',"right":2}',
])
def test_exact_decoder_retains_strict_json_guards_and_rejects_unrepresentable_exponents(engine,content):
    result=check(engine,content)
    assert {f.rule_id for f in result.findings}=={'business.invalid_json'}
    assert result.evaluation_status=='complete'


@pytest.mark.parametrize('value',['"1"','true','null','{}','[]'])
def test_missing_or_invalid_values_are_not_coerced_or_defaulted(engine,value):
    result=check(engine,'{"left":'+value+',"right":2}')
    assert 'missing or invalid number' in result.findings[0].explanation


def test_failed_relationship_is_distinct_from_invalid_data(engine):
    assert 'less than or equal to' in check(engine,'{"left":3,"right":2}').findings[0].explanation
    assert 'missing or invalid' in check(engine,'{"right":2}').findings[0].explanation


def test_public_api_reports_no_values_or_json_keys(engine,caplog):
    client=TestClient(create_app(engine=engine,credentials={'test-key':'demo'}))
    response=client.post('/v1/checks/output',headers={'Authorization':'Bearer test-key'},json={
        'application_id':'demo','policy_version':'business-number-le-v1','content':'{"left":1.0000000000000001,"right":1.0,"private_account":"synthetic-private-value"}','content_format':'json'})
    assert response.status_code==200
    assert response.json()['recommended_action']=='block'
    for value in ['1.0000000000000001','private_account','synthetic-private-value']:
        assert value not in response.text+caplog.text


@pytest.mark.parametrize('text,reason',[
 ('Details: No summary provided.','is missing'),
 ('Summary:   ','is too short'),
 ('Summary: First.\nSUMMARY: Second.','is repeated'),
])
def test_section_failure_reasons_are_specific_without_copying_bodies(engine,text,reason):
    result=asyncio.run(engine.check(CheckRequest(application_id='demo',policy_version='completeness-v1',content=text),Stage.INPUT))
    assert reason in result.findings[0].explanation
    assert 'Presence checks do not assess meaning' in result.findings[0].explanation
    assert result.checker_results[0].checker_version=='2'


def test_full_batch_uses_no_network(monkeypatch):
    def deny(*args,**kwargs):raise AssertionError('Network attempted')
    monkeypatch.setattr(socket.socket,'connect',deny)
    engine=Engine(load_policies(ROOT/'policies/business-batch.json'),default_checkers())
    assert asyncio.run(evaluate(load_suite(ROOT/'evals/business.json'),engine))['summary']['matched']==100
