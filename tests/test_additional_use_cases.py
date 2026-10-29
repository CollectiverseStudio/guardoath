"""Executable portions of use cases 51–100; no semantic/industrial coverage claim."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from guardoath.app import create_app
from guardoath.budgets import ResourceLimits
from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import CheckRequest,CheckerResult,ConfidentialTerm,FieldComparison,Policy,Rule,Stage

STAGES=['input','output']


def schema_client(schema):
    policy=Policy(application_id='demo',version='v1',mode='enforce',rules=(Rule(checker_id='schema',on_finding='block'),),input_schema=schema,output_schema=schema)
    return TestClient(create_app(engine=Engine([policy],default_checkers()),credentials={'test-key':'demo'}))


def post(client,stage,data):
    return client.post('/v1/checks/'+stage,headers={'Authorization':'Bearer test-key'},json={
        'application_id':'demo','policy_version':'v1','content':json.dumps(data),'content_format':'json'})


def assert_schema(client,stage,data,valid):
    response=post(client,stage,data)
    assert response.status_code==200
    result=response.json()
    assert result['evaluation_status']=='complete'
    assert (not result['findings']) is valid
    assert result['effective_action']==('allow' if valid else 'block')
    return result


@pytest.mark.parametrize('stage',STAGES)
@pytest.mark.parametrize('count,valid',[(0,True),(3,True),(4,False)])
def test_uc068_coordinate_array_uses_an_explicit_application_limit(stage,count,valid):
    schema={'type':'object','properties':{'coordinates':{'type':'array','maxItems':3,'items':{'type':'array','minItems':2,'maxItems':2,'items':{'type':'number'}}}},'required':['coordinates'],'additionalProperties':False}
    assert_schema(schema_client(schema),stage,{'coordinates':[[10,20]]*count},valid)


@pytest.mark.parametrize('stage',STAGES)
@pytest.mark.parametrize('nulls,valid',[(0,True),(1,True),(2,False),(4,False)])
def test_uc074_missing_feature_quota_is_validated_without_imputation(stage,nulls,valid):
    schema={'type':'object','properties':{'features':{'type':'array','minItems':4,'maxItems':4,'items':{'type':['number','null']},'contains':{'type':'null'},'minContains':0,'maxContains':1}},'required':['features'],'additionalProperties':False}
    original={'features':[None]*nulls+[1]*(4-nulls)}
    assert_schema(schema_client(schema),stage,original,valid)
    assert original['features'].count(None)==nulls  # Validation does not fill/alter values.


@pytest.mark.parametrize('stage',STAGES)
@pytest.mark.parametrize('category,valid',[('SUPPORT',True),('BILLING',True),('finance',False),('support',False),('',False)])
def test_uc079_exact_taxonomy_codes_are_not_semantic_classification(stage,category,valid):
    schema={'type':'object','properties':{'category':{'enum':['SUPPORT','BILLING']}},'required':['category'],'additionalProperties':False}
    assert_schema(schema_client(schema),stage,{'category':category},valid)


@pytest.mark.parametrize('stage',STAGES)
@pytest.mark.parametrize('value,valid',[(0,True),(120,True),(500,False),(-1,False),(True,False)])
def test_uc085_sensor_reading_is_checked_against_declared_bounds_only(stage,value,valid):
    schema={'type':'object','properties':{'temperature':{'type':'number','minimum':0,'maximum':120},'unit':{'const':'F'}},'required':['temperature','unit'],'additionalProperties':False}
    assert_schema(schema_client(schema),stage,{'temperature':value,'unit':'F'},valid)


@pytest.mark.parametrize('stage',STAGES)
@pytest.mark.parametrize('lat,lon,valid',[(10,30,True),(20,40,True),(15,35,True),(9,35,False),(15,41,False)])
def test_uc087_box_bounds_are_not_polygon_or_navigation_authorization(stage,lat,lon,valid):
    schema={'type':'object','properties':{'lat':{'type':'number','minimum':10,'maximum':20},'lon':{'type':'number','minimum':30,'maximum':40}},'required':['lat','lon'],'additionalProperties':False}
    assert_schema(schema_client(schema),stage,{'lat':lat,'lon':lon},valid)


@pytest.mark.parametrize('stage',STAGES)
def test_uc072_versioned_schema_changes_do_not_mutate_an_active_policy(stage):
    first={'type':'object','properties':{'message':{'type':'string'}},'required':['message'],'additionalProperties':False}
    second={'type':'object','properties':{'message':{'type':'string'},'priority':{'enum':['normal','urgent']}},'required':['message','priority'],'additionalProperties':False}
    policies=[Policy(application_id='demo',version=version,rules=(Rule(checker_id='schema'),),input_schema=schema,output_schema=schema) for version,schema in [('v1',first),('v2',second)]]
    engine=Engine(policies,default_checkers())
    for version,flagged in [('v1',False),('v2',True)]:
        result=asyncio.run(engine.check(CheckRequest(application_id='demo',policy_version=version,content='{"message":"hello"}',content_format='json'),Stage(stage)))
        assert bool(result.findings) is flagged
    assert_schema(schema_client(second),stage,{'message':'hello','priority':'normal'},True)


@pytest.mark.parametrize('stage',STAGES)
@pytest.mark.parametrize('left,right,valid',[(123.45,123.45,True),(123.45,123.46,False)])
def test_uc088_supplied_aggregate_totals_compare_without_claiming_line_item_sums(stage,left,right,valid):
    p=Policy(application_id='demo',version='v1',rules=(Rule(checker_id='cross_field'),),comparisons=(FieldComparison(rule_id='declared_balance',left='/debits_total',right='/credits_total',operator='eq'),))
    result=asyncio.run(Engine([p],default_checkers()).check(CheckRequest(application_id='demo',policy_version='v1',
        content=json.dumps({'debits_total':left,'credits_total':right,'rows':'not summed by this checker'}),content_format='json'),Stage(stage)))
    assert (not result.findings) is valid


@pytest.mark.parametrize('stage',STAGES)
def test_uc078_experience_is_compared_to_submitted_age_without_deriving_birth_date(stage):
    p=Policy(application_id='demo',version='v1',rules=(Rule(checker_id='cross_field'),),comparisons=(FieldComparison(rule_id='declared_experience',left='/experience_years',right='/declared_age',operator='le'),))
    engine=Engine([p],default_checkers())
    for experience,flagged in [(10,False),(45,True)]:
        result=asyncio.run(engine.check(CheckRequest(application_id='demo',policy_version='v1',content=json.dumps({'experience_years':experience,'declared_age':36,'birth_year':1990}),content_format='json'),Stage(stage)))
        assert bool(result.findings) is flagged


@pytest.mark.parametrize('stage',STAGES)
@pytest.mark.parametrize('field,value',[('mode','observe'),('failure_action','allow'),('rules',[]),('input_schema',{}),('external_checks','after_local_pass')])
def test_uc092_overposting_cannot_reconfigure_the_policy(stage,field,value):
    client=schema_client({'type':'object'})
    response=client.post('/v1/checks/'+stage,headers={'Authorization':'Bearer test-key'},json={
        'application_id':'demo','policy_version':'v1','content':'{}','content_format':'json',field:value})
    assert response.status_code==422 and response.json()=={'detail':'Invalid check request'}
    meta=client.get('/v1/applications/current',headers={'Authorization':'Bearer test-key'}).json()
    assert meta['policies'][0]['mode']=='enforce'


@pytest.mark.parametrize('stage',STAGES)
def test_uc098_invalid_utf8_rejected_before_checker_invocation(stage):
    client=schema_client({'type':'object'})
    body=b'{"application_id":"demo","policy_version":"v1","content":"\xff"}'
    response=client.post('/v1/checks/'+stage,headers={'Authorization':'Bearer test-key','Content-Type':'application/json'},content=body)
    assert response.status_code==400 and 'content' not in response.json()


def test_uc054_and_uc097_limits_reject_rather_than_silently_truncate():
    observed=[]
    async def capture(request,p,stage):
        observed.append(request.content)
        return CheckerResult(checker_id='capture',checker_version='fixture',status='passed')
    p=Policy(application_id='demo',version='v1',rules=(Rule(checker_id='capture'),))
    client=TestClient(create_app(engine=Engine([p],{'capture':capture}),credentials={'test-key':'demo'}))
    for length,status in [(32000,200),(32001,422)]:
        response=client.post('/v1/checks/input',headers={'Authorization':'Bearer test-key'},json={'application_id':'demo','policy_version':'v1','content':'x'*length})
        assert response.status_code==status
    assert len(observed)==1 and len(observed[0])==32000
    response=client.post('/v1/checks/input',headers={'Authorization':'Bearer test-key','Content-Type':'application/json'},content=b'x'*262145)
    assert response.status_code==413 and len(observed)==1


def test_uc053_mixed_language_transport_is_not_a_semantic_safety_assessment():
    observed=[]
    async def capture(request,p,stage):
        observed.append(request.content);return CheckerResult(checker_id='capture',checker_version='fixture',status='passed')
    p=Policy(application_id='demo',version='v1',rules=(Rule(checker_id='capture'),))
    client=TestClient(create_app(engine=Engine([p],{'capture':capture}),credentials={'test-key':'demo'}))
    text='Hola, can you help? नमस्ते'
    response=client.post('/v1/checks/input',headers={'Authorization':'Bearer test-key'},json={'application_id':'demo','policy_version':'v1','content':text})
    assert response.status_code==200 and observed==[text]
    # This instrumented transport test does not classify the message's intent.


def test_uc062_app_quota_is_shared_across_keys_and_stages_not_a_user_behavior_model():
    policies=[Policy(application_id=a,version='v1',rules=(Rule(checker_id='text_security'),)) for a in ['a','b']]
    client=TestClient(create_app(engine=Engine(policies,default_checkers()),credentials={'key-one':'a','key-two':'a','key-three':'b'},limits=ResourceLimits(checks_per_minute=1,burst=1)))
    for key,app,stage,status in [('key-one','a','input',200),('key-two','a','output',429),('key-three','b','input',200)]:
        response=client.post('/v1/checks/'+stage,headers={'Authorization':'Bearer '+key},json={'application_id':app,'policy_version':'v1','content':'hello'})
        assert response.status_code==status
        if status==429:assert int(response.headers['Retry-After'])>=1


@pytest.mark.parametrize('stage',STAGES)
def test_uc099_phrase_rules_are_versioned_not_an_instant_external_directory(stage):
    def p(version,term):return Policy(application_id='demo',version=version,rules=(Rule(checker_id='secrets'),),confidential_terms=(ConfidentialTerm(rule_id='project',term=term),))
    engine=Engine([p('v1','PROJECT LANTERN'),p('v2','PROJECT ORCHID')],default_checkers())
    for version,text,flagged in [('v1','project lantern',True),('v1','PROJECT ORCHID',False),('v2','PROJECT ORCHID',True)]:
        result=asyncio.run(engine.check(CheckRequest(application_id='demo',policy_version=version,content=text),Stage(stage)))
        assert bool(result.findings) is flagged


@pytest.mark.parametrize('stage',STAGES)
@pytest.mark.parametrize('value,valid',[
    ({'customer_type':'business','company_name':'Example'},True),
    ({'customer_type':'business'},False),
    ({'customer_type':'individual'},True),
])
def test_uc080_deterministic_conditional_policy_requires_no_semantic_model(stage,value,valid):
    schema={'type':'object','properties':{'customer_type':{'enum':['business','individual']},'company_name':{'type':'string','minLength':1}},'required':['customer_type'],'additionalProperties':False,
        'if':{'properties':{'customer_type':{'const':'business'}},'required':['customer_type']},'then':{'required':['company_name']}}
    assert_schema(schema_client(schema),stage,value,valid)


@pytest.mark.parametrize('stage',STAGES)
def test_uc077_sql_proposal_is_rejected_by_allowlist_not_sql_sanitization(stage):
    from pathlib import Path
    from guardoath.config import load_policies
    p=load_policies(Path(__file__).resolve().parents[1]/'policies/actions-batch.json')[0]
    result=asyncio.run(Engine([p],default_checkers()).check(CheckRequest(application_id='demo',policy_version=p.version,
        content=json.dumps({'tool_calls':[{'name':'execute_sql','arguments':{'query':'SELECT 1'}}]}),content_format='json'),Stage(stage)))
    assert {finding.rule_id for finding in result.findings}=={'actions.unknown_tool'}
