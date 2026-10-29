"""Structured matched-decision consistency, not semantic bias or fairness proof."""
import asyncio
from copy import deepcopy
from decimal import localcontext
import json
from pathlib import Path
import socket

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from guardoath.app import create_app
from guardoath.checkers import default_checkers
from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.evaluate import evaluate, load_suite
from guardoath.models import CheckRequest, CheckerResult, MatchedOutcomes, Policy, Rule, Stage

ROOT = Path(__file__).resolve().parents[1]


def payload(first='approve',second='approve',attributes=None):
    a,b = attributes or ({'group':'A'},{'group':'B'})
    return {'scenarios': [{'factors': {'qualification':'same'},'attributes':a,'decision':first},
                          {'factors': {'qualification':'same'},'attributes':b,'decision':second}]}


def policy(kind='string',mode='observe'):
    version='bias-matched-'+kind+('' if mode=='observe' else '-enforce')+'-v1'
    return next(p for p in load_policies(ROOT/'policies/bias-batch.json') if p.version==version)


def check(value,kind='string',stage=Stage.INPUT,mode='observe',configured=None,fmt='json',extras=None):
    selected=configured or policy(kind,mode)
    engine=Engine([selected], {**default_checkers(),**(extras or {})})
    text=value if isinstance(value,str) else json.dumps(value)
    return asyncio.run(engine.check(CheckRequest(application_id='demo',policy_version=selected.version,
        content=text,content_format=fmt,request_context='private-unscanned-context'),stage))


def test_whole_bias_group_matches_explicit_expectations():
    engine=Engine(load_policies(ROOT/'policies/bias-batch.json'),default_checkers())
    report=asyncio.run(evaluate(load_suite(ROOT/'evals/bias.json'),engine))
    assert report['summary']=={'total':100,'matched':100,'mismatched':0,'incomplete':50,'simulated':0}
    assert 'qualification' not in json.dumps(report) and 'private-unscanned-context' not in json.dumps(report)


@pytest.mark.parametrize('kind,a,b',[('string','approve','deny'),('boolean',True,False),('number',10,11)])
@pytest.mark.parametrize('stage',list(Stage))
@pytest.mark.parametrize('mode',['observe','enforce'])
def test_outcome_types_stages_and_decisions(kind,a,b,stage,mode):
    result=check(payload(a,b),kind,stage,mode)
    action='flag' if stage==Stage.INPUT else 'block'
    assert result.evaluation_status=='complete'
    assert result.recommended_action==action and result.effective_action==(action if mode=='enforce' else 'allow')
    assert [f.rule_id for f in result.findings]==['bias.matched_outcome_difference']
    assert result.findings[0].category=='bias'
    assert not result.simulated and result.cost_usd==0 and result.cost_basis=='local_only'
    assert next(g for g in result.groups if g.group_id=='bias').status=='findings'


@pytest.mark.parametrize('a,b',[(1,1.0),(0,-0.0),(-5,-5)])
def test_numeric_equivalence_is_explicit(a,b):
    assert not check(payload(a,b),'number').findings


@pytest.mark.parametrize('left,right',[('1.0000000000000001','1.0'),('1e-400','0'),('9007199254740993','9007199254740992')])
def test_numeric_precision_never_rounds_a_difference(left,right):
    text=json.dumps(payload('LEFT','RIGHT')).replace('"LEFT"',left).replace('"RIGHT"',right)
    with localcontext() as context:
        context.prec=2
        result=check(text,'number')
    assert result.findings and 'Submitted outcomes differ' in result.findings[0].explanation


@pytest.mark.parametrize('kind,value',[('boolean',1),('boolean','true'),('number',True),('number','10'),('number',None),('string',10),('string',False),('string','   ')])
def test_no_outcome_coercion(kind,value):
    result=check(payload(value,value),kind)
    assert result.evaluation_status=='incomplete' and not result.findings
    assert result.checker_results[0].error_code=='matched_outcomes_invalid_records'


@pytest.mark.parametrize('names',[(),('group','group'),('Group','group'),(' group',),('group-name',),tuple('abcdefghij')])
def test_invalid_configuration_names(names):
    with pytest.raises(ValidationError):MatchedOutcomes(attributes=names)


def test_options_and_checker_must_be_configured_together():
    with pytest.raises(ValueError,match='configuration and checker'):
        Engine([Policy(application_id='demo',version='v1',rules=(Rule(checker_id='matched_outcomes'),))],default_checkers())
    with pytest.raises(ValueError,match='configuration and checker'):
        Engine([Policy(application_id='demo',version='v1',rules=(),matched_outcomes=MatchedOutcomes(attributes=('group',)))],default_checkers())


def test_several_configured_attributes_can_change_without_automatic_finding():
    selected=policy().model_copy(update={'matched_outcomes':MatchedOutcomes(attributes=('age_band','gender'))})
    p=payload(attributes=({'age_band':'older','gender':'woman'},{'age_band':'younger','gender':'man'}))
    result=check(p,configured=selected)
    assert result.evaluation_status=='complete' and not result.findings
    p['scenarios'][1]['decision']='deny'
    assert check(p,configured=selected).findings


@pytest.mark.parametrize('case', ['different-factors','same-attributes','uncovered','boolean-vs-number','array-order','string-vs-number'])
def test_noncomparable_records_do_not_become_a_fairness_pass(case):
    p=payload()
    if case=='different-factors':p['scenarios'][1]['factors']['qualification']='different'
    elif case=='same-attributes':p['scenarios'][1]['attributes']={'group':'A'}
    elif case=='uncovered':p['scenarios'].append({'factors':{'other':True},'attributes':{'group':'C'},'decision':'approve'})
    else:
        values={'boolean-vs-number':(True,1),'array-order':([1,2],[2,1]),'string-vs-number':('1',1)}[case]
        for row,value in zip(p['scenarios'],values):row['factors']={'value':value}
    result=check(p)
    assert result.evaluation_status=='incomplete' and not result.findings
    assert result.checker_results[0].error_code=='matched_outcomes_insufficient_comparisons'


def test_partial_findings_are_withheld_when_any_record_is_unassessed():
    p=payload('approve','deny')
    p['scenarios'].append({'factors':{'other':True},'attributes':{'group':'C'},'decision':'approve'})
    result=check(p)
    assert result.evaluation_status=='incomplete' and not result.findings


@pytest.mark.parametrize('factors',[{}, {'group':'A'}, {'nested':[{'GROUP':'A'}]}, dict.fromkeys([str(i) for i in range(21)],1)])
def test_missing_overlapping_or_overbudget_factors_are_incomplete(factors):
    p=payload();p['scenarios'][0]['factors']=factors
    result=check(p)
    assert result.evaluation_status=='incomplete'
    assert result.checker_results[0].error_code=='matched_outcomes_invalid_records'


@pytest.mark.parametrize('count',[1,21])
def test_record_count_boundaries(count):
    p=payload();p['scenarios']=[deepcopy(p['scenarios'][i%2]) for i in range(count)]
    assert check(p).evaluation_status=='incomplete'


def test_twenty_records_compare_all_100_cross_attribute_pairs():
    p=payload('approve','deny');p['scenarios']=[deepcopy(p['scenarios'][i%2]) for i in range(20)]
    result=check(p)
    assert '100 of 100 comparable scenario pairs' in result.findings[0].explanation


@pytest.mark.parametrize('bad', ['{bad', '{"scenarios":[],"scenarios":[]}', '{"scenarios":NaN}', '[]'])
def test_strict_json_guards(bad):
    assert check(bad).checker_results[0].error_code=='matched_outcomes_invalid_records'


def test_unbounded_exponent_and_boolean_numeric_confusion_are_not_accepted():
    text=json.dumps(payload('X','Y')).replace('"X"','1e-1000001').replace('"Y"','0')
    assert check(text,'number').checker_results[0].error_code=='matched_outcomes_invalid_records'


def test_plain_text_cannot_look_like_a_completed_bias_assessment():
    result=check('People of every religion are welcome.',fmt='text')
    assert result.evaluation_status=='incomplete' and result.checker_results[0].status=='skipped'
    assert not result.findings


def test_context_and_private_attributes_are_not_returned_or_logged(caplog):
    p=payload('private-outcome-one','private-outcome-two',attributes=({'group':'private-attribute-one'},{'group':'private-attribute-two'}))
    for row in p['scenarios']:row['factors']={'private-factor':'private-factor-value'}
    selected=policy();engine=Engine([selected],default_checkers())
    client=TestClient(create_app(engine=engine,credentials={'test-key':'demo'}))
    for stage in Stage:
        response=client.post('/v1/checks/'+stage,headers={'Authorization':'Bearer test-key'},json={
            'application_id':'demo','policy_version':selected.version,'content':json.dumps(p),'content_format':'json',
            'request_context':'private-unscanned-context'})
        assert response.status_code==200 and response.json()['findings']
        for private in ['private-outcome','private-attribute','private-factor','private-unscanned-context']:
            assert private not in response.text+caplog.text


def test_local_bias_batch_never_uses_network(monkeypatch):
    def deny(*args,**kwargs):raise AssertionError('Network attempted')
    monkeypatch.setattr(socket.socket,'connect',deny)
    engine=Engine(load_policies(ROOT/'policies/bias-batch.json'),default_checkers())
    assert asyncio.run(evaluate(load_suite(ROOT/'evals/bias.json'),engine))['summary']['matched']==100


@pytest.mark.parametrize('finding', [False,True])
def test_external_gate_uses_bias_result_even_when_its_action_is_allow(finding):
    calls=[]
    async def remote(request,policy,stage):
        calls.append(stage)
        return CheckerResult(checker_id='external',checker_version='fixture',status='passed',external=True)
    remote.external=True
    selected=policy().model_copy(update={'rules':(Rule(checker_id='matched_outcomes',on_finding='allow'),Rule(checker_id='external')),
        'external_checks':'after_local_pass'})
    result=check(payload('approve','deny' if finding else 'approve'),configured=selected,extras={'external':remote})
    assert bool(calls) is not finding
    if finding:
        assert result.checker_results[-1].error_code=='external_local_gate' and result.evaluation_status=='incomplete'


def test_required_failure_action_is_respected_in_enforce_mode():
    selected=policy(mode='enforce').model_copy(update={'failure_action':'flag'})
    result=check({'scenarios':[]},configured=selected)
    assert result.evaluation_status=='incomplete' and result.effective_action=='flag'
