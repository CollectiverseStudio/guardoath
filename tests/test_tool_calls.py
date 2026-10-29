"""Allowlist/schema checks for proposed calls never execute or authorize actions."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from guardoath.app import create_app
from guardoath.checkers import default_checkers
from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.evaluate import evaluate, load_suite
from guardoath.models import CheckRequest, Policy, Rule, Stage, ToolDefinition, ToolPolicy

ROOT=Path(__file__).resolve().parents[1]


def policy(mode='observe'):
    return next(p for p in load_policies(ROOT/'policies/actions-batch.json') if p.mode==mode)


def lookup(**kwargs):return {'name':'lookup_order','arguments':{'order_id':'ORD-1234',**kwargs}}


def check(calls,stage=Stage.INPUT,mode='observe',configured=None,fmt='json'):
    p=configured or policy(mode)
    text=calls if isinstance(calls,str) else json.dumps({'tool_calls':calls})
    return asyncio.run(Engine([p],default_checkers()).check(CheckRequest(application_id='demo',policy_version=p.version,
        content=text,content_format=fmt),stage))


def test_complete_actions_batch():
    report=asyncio.run(evaluate(load_suite(ROOT/'evals/actions.json'),Engine(load_policies(ROOT/'policies/actions-batch.json'),default_checkers())))
    assert report['summary']=={'total':100,'matched':100,'mismatched':0,'incomplete':28,'simulated':0}
    assert 'Never execute this synthetic command.' not in json.dumps(report)


@pytest.mark.parametrize('stage',list(Stage))
@pytest.mark.parametrize('mode',['observe','enforce'])
def test_both_stages_and_mode_actions(stage,mode):
    result=check([{'name':'unknown','arguments':{}}],stage,mode)
    action='flag' if stage==Stage.INPUT else 'block'
    assert result.evaluation_status=='complete' and result.recommended_action==action
    assert result.effective_action==(action if mode=='enforce' else 'allow')
    assert {f.rule_id for f in result.findings}=={'actions.unknown_tool'}
    assert next(g for g in result.groups if g.group_id=='actions').status=='findings'
    assert not result.simulated and result.cost_usd==0


@pytest.mark.parametrize('name',['Lookup_order','lookup_order_extra','delete_all_files'])
def test_allowlist_uses_exact_names(name):
    assert {f.rule_id for f in check([{'name':name,'arguments':{}}]).findings}=={'actions.unknown_tool'}


@pytest.mark.parametrize('arguments',[{}, {'order_id':1234},{'order_id':True},{'order_id':'ORD-1234\n'},
    {'order_id':'ORD-1234','command':'private-command'},{'order_id':'wrong'}])
def test_arguments_follow_trusted_schema(arguments):
    assert {f.rule_id for f in check([{'name':'lookup_order','arguments':arguments}]).findings}=={'actions.invalid_arguments'}


def test_multiple_failure_types_are_retained_without_duplicate_finding_ids():
    result=check([{'name':'unknown','arguments':{}},{'name':'unknown','arguments':{}},lookup(order_id='wrong')])
    assert [f.rule_id for f in result.findings]==['actions.unknown_tool','actions.invalid_arguments']


def test_no_calls_is_not_an_execution_or_authorization_claim():
    result=check([])
    assert result.evaluation_status=='complete' and not result.findings


@pytest.mark.parametrize('count,complete',[(10,True),(11,False)])
def test_request_call_limit(count,complete):
    result=check([lookup() for _ in range(count)])
    assert (result.evaluation_status=='complete') is complete


@pytest.mark.parametrize('token',['100.0000000000000001','1e-400','1e-1000001'])
def test_lossy_or_extreme_float_tokens_are_incomplete(token):
    text=json.dumps({'tool_calls':[{'name':'request_refund','arguments':{'order_id':'ORD-1234','amount':'RAW','reason':'damaged','currency':'USD'}}]}).replace('"RAW"',token)
    result=check(text)
    assert result.evaluation_status=='incomplete' and result.checker_results[0].error_code=='tool_calls_invalid_document'


@pytest.mark.parametrize('bad',['{bad','{"tool_calls":[],"tool_calls":[]}','[]','{"tool_calls":null}',
    '{"tool_calls":[{"name":"lookup_order","arguments":[]}]}','{"tool_calls":[{"name":"lookup_order"}]}'])
def test_invalid_documents_fail_incomplete(bad):
    assert check(bad).checker_results[0].error_code=='tool_calls_invalid_document'


def test_caller_cannot_replace_the_allowed_tools():
    value={'tool_calls':[{'name':'private_tool','arguments':{}}],'tools':[{'name':'private_tool','arguments_schema':{}}]}
    assert check(json.dumps(value)).evaluation_status=='incomplete'


def test_plain_code_is_not_treated_as_validated_action_safety():
    result=check('print("hello")',fmt='text')
    assert result.evaluation_status=='incomplete' and result.checker_results[0].error_code=='tool_calls_json_required'


def test_options_require_checker_and_unique_valid_definitions():
    with pytest.raises(ValueError,match='configuration and checker'):
        Engine([Policy(application_id='demo',version='v1',rules=(Rule(checker_id='tool_calls'),))],default_checkers())
    with pytest.raises(ValueError,match='configuration and checker'):
        Engine([policy().model_copy(update={'rules':()})],default_checkers())
    with pytest.raises(ValidationError):ToolPolicy(tools=())
    definition=ToolDefinition(name='tool',arguments_schema={})
    with pytest.raises(ValidationError):ToolPolicy(tools=(definition,definition))
    with pytest.raises(ValidationError):ToolDefinition(name='tool;command',arguments_schema={})


@pytest.mark.parametrize('schema',[{'$ref':'https://example.com/schema'}, {'type':'object','properties':{'x':{'format':'unknown-format'}}}])
def test_startup_rejects_remote_refs_and_unknown_formats(schema):
    p=policy().model_copy(update={'tool_calls':ToolPolicy(tools=(ToolDefinition(name='tool',arguments_schema=schema),))})
    with pytest.raises(ValueError):Engine([p],default_checkers())


def test_local_reference_and_supported_format_are_validated():
    schema={'type':'object','$defs':{'email':{'type':'string','format':'email'}},'properties':{'email':{'$ref':'#/$defs/email'}},'required':['email'],'additionalProperties':False}
    p=policy().model_copy(update={'tool_calls':ToolPolicy(tools=(ToolDefinition(name='contact',arguments_schema=schema),))})
    assert not check([{'name':'contact','arguments':{'email':'jane@example.com'}}],configured=p).findings
    assert check([{'name':'contact','arguments':{'email':'invalid'}}],configured=p).findings


def test_policy_schema_snapshot_is_not_changed_by_original_mutation():
    p=policy();engine=Engine([p],default_checkers())
    p.tool_calls.tools[0].arguments_schema['required']=[]
    result=asyncio.run(engine.check(CheckRequest(application_id='demo',policy_version=p.version,
        content='{"tool_calls":[{"name":"lookup_order","arguments":{}}]}',content_format='json'),Stage.OUTPUT))
    assert result.findings


def test_api_and_metadata_do_not_echo_tool_names_values_or_schema_errors(caplog):
    p=policy();client=TestClient(create_app(engine=Engine([p],default_checkers()),credentials={'test-key':'demo'}))
    headers={'Authorization':'Bearer test-key'}
    meta=client.get('/v1/applications/current',headers=headers)
    assert 'lookup_order' not in meta.text and 'arguments_schema' not in meta.text
    for stage in Stage:
        response=client.post('/v1/checks/'+stage,headers=headers,json={'application_id':'demo','policy_version':p.version,
            'content':json.dumps({'tool_calls':[{'name':'private_tool_name','arguments':{'private_value':'private_argument_value'}},lookup(order_id='private_order_value')]}),'content_format':'json'})
        assert response.status_code==200 and len(response.json()['findings'])==2
        for private in ['private_tool_name','private_argument_value','private_order_value']:
            assert private not in response.text+caplog.text


def test_the_entire_batch_cannot_invoke_tools_or_use_network(monkeypatch):
    def deny(*args,**kwargs):raise AssertionError('Execution/network attempted')
    monkeypatch.setattr(socket.socket,'connect',deny);monkeypatch.setattr(subprocess,'Popen',deny)
    report=asyncio.run(evaluate(load_suite(ROOT/'evals/actions.json'),Engine(load_policies(ROOT/'policies/actions-batch.json'),default_checkers())))
    assert report['summary']['matched']==100
