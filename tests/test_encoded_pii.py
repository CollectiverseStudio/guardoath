"""One-layer encoded identifiers, safe errors and redaction/gate boundaries."""
import asyncio
import base64
import json
from pathlib import Path

import pytest
pytest.importorskip('tldextract')
pytest.importorskip('phonenumbers')
from fastapi.testclient import TestClient
from guardoath.app import create_app
from guardoath.checkers import default_checkers
from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.evaluate import evaluate,load_suite
from guardoath.models import CheckRequest,CheckerResult,Rule,Stage
from network_guard import deny_network

ROOT=Path(__file__).resolve().parents[1]

def encode(text,kind='base64'):
    return kind+': '+(base64.b64encode(text.encode()).decode() if kind=='base64' else text.encode().hex())

@pytest.fixture(scope='module')
def adapters():return default_checkers(enable_pii=True)

def policy(redact=False):
    return next(p for p in load_policies(ROOT/'policies/encoded-privacy-batch.json') if p.version=='privacy-encoded'+('-redact' if redact else '')+'-v1')

def check(adapters,text,stage=Stage.INPUT,fmt='text',redact=False,selected=None):
    p=selected or policy(redact)
    return asyncio.run(Engine([p],adapters).check(CheckRequest(application_id='demo',policy_version=p.version,
        content=text,content_format=fmt,request_context='private-context'),stage))

def encoded(result):return next(c for c in result.checker_results if c.checker_id=='encoded_pii')

def test_full_encoded_privacy_batch(adapters):
    report=asyncio.run(evaluate(load_suite(ROOT/'evals/encoded-privacy.json'),Engine(load_policies(ROOT/'policies/encoded-privacy-batch.json'),adapters)))
    assert report['summary']=={'total':100,'matched':100,'mismatched':0,'incomplete':28,'simulated':0}
    assert 'jane@example.com' not in json.dumps(report)

@pytest.mark.parametrize('stage',list(Stage))
@pytest.mark.parametrize('kind',['base64','hex'])
def test_base_and_labeled_identifiers_are_reused(adapters,stage,kind):
    for text,entity in [('jane@example.com','email_address'),('SSN: 219-09-9999','us_ssn'),('Card: 4111 1111 1111 1111','credit_card'),('MRN: A1234','medical_record_number'),('Phone: 202-555-0147','phone_number')]:
        result=check(adapters,encode(text,kind),stage)
        assert encoded(result).status=='flagged'
        assert 'pii.encoded_'+entity in {f.rule_id for f in result.findings}
        assert result.evaluation_status=='complete' and result.cost_usd==0 and not result.simulated

@pytest.mark.parametrize('content',['base64: @bad','hex: zz','hex: ff','base64:'])
def test_invalid_or_nontext_encoding_is_incomplete(adapters,content):
    result=check(adapters,content)
    assert result.evaluation_status=='incomplete' and not encoded(result).findings
    assert encoded(result).error_code=='encoded_pii_invalid_encoding'

@pytest.mark.parametrize('count,valid',[(16,True),(17,False)])
def test_shared_count_budget(adapters,count,valid):
    result=check(adapters,' '.join(['hex: 61']*count))
    assert (encoded(result).status=='passed') is valid
    if not valid:assert encoded(result).error_code=='encoded_pii_budget_exceeded'

@pytest.mark.parametrize('text,valid',[
    ('base64: '+'YWFh'*1024,True),
    ('base64: '+'a'*4097,False),
    (' '.join(['base64: '+'YWFh'*1024]*2),True),
    (' '.join(['base64: '+'YWFh'*1024]*3),False),
])
def test_per_token_and_shared_character_budgets(adapters,text,valid):
    result=check(adapters,text)
    assert (encoded(result).status=='passed') is valid
    if not valid:assert encoded(result).error_code=='encoded_pii_budget_exceeded'


def test_budgets_span_json_keys_and_values(adapters):
    value={('hex: 61 '+str(i)):'hex: 61' for i in range(9)}
    result=check(adapters,json.dumps(value),fmt='json')
    assert encoded(result).error_code=='encoded_pii_budget_exceeded'


def test_json_keys_escapes_nested_objects_and_field_hints(adapters):
    for value,entity in [({'MRN':'A1234'},'medical_record_number'),({'jane@example.com':'value'},'email_address'),({'nested':[{'Phone':'202-555-0147'}]},'phone_number')]:
        assert 'pii.encoded_'+entity in {f.rule_id for f in check(adapters,encode(json.dumps(value))).findings}
    escaped='{"contact":"jane\\u0040example.com"}'
    assert 'pii.encoded_email_address' in {f.rule_id for f in check(adapters,encode(escaped)).findings}


def test_nested_encodings_and_malformed_decoded_structures_are_not_clean(adapters):
    for text,code in [(encode(encode('jane@example.com')),'encoded_pii_nested_payload'),(encode('{bad'),'encoded_pii_invalid_decoded_json')]:
        assert encoded(check(adapters,text)).error_code==code


def test_partial_findings_discarded_on_later_failure(adapters):
    result=check(adapters,encode('jane@example.com')+' base64: @bad')
    assert result.evaluation_status=='incomplete' and not encoded(result).findings


def test_field_budget_failure_stays_incomplete(adapters):
    result=check(adapters,encode('\n'.join(['Phone: 202-555-0147']*65)))
    assert encoded(result).error_code=='encoded_pii_scan_incomplete' and not encoded(result).findings


def test_no_unlabeled_or_recursive_decode_claim(adapters):
    unlabeled=base64.b64encode(b'jane@example.com').decode()
    assert not check(adapters,unlabeled).findings
    assert not check(adapters,encode(unlabeled)).findings


def test_decoded_values_and_payloads_never_return_or_log(adapters,caplog):
    p=policy();client=TestClient(create_app(engine=Engine([p],adapters),credentials={'test-key':'demo'}))
    payload=encode('Email: jane@example.com')
    for stage in Stage:
        response=client.post('/v1/checks/'+stage,headers={'Authorization':'Bearer test-key'},json={'application_id':'demo','policy_version':p.version,'content':payload})
        assert response.status_code==200 and response.json()['findings']
        for private in ['jane@example.com',payload]:assert private not in response.text+caplog.text


def test_encoded_values_are_not_rewritten_or_released_by_raw_redaction(adapters):
    result=check(adapters,'Contact jane@example.com '+encode('SSN: 219-09-9999'),redact=True)
    assert result.redaction.status=='withheld' and result.redaction.content is None
    assert result.recommended_action=='block'
    assert encoded(result).transformed_content is None


def test_local_findings_stop_external_calls_even_with_allow_action(adapters):
    called=[]
    async def remote(request,p,stage):
        called.append(1);return CheckerResult(checker_id='remote',checker_version='fixture',status='passed',external=True)
    remote.external=True
    p=policy().model_copy(update={'rules':(Rule(checker_id='pii'),Rule(checker_id='encoded_pii',on_finding='allow'),Rule(checker_id='remote')),'external_checks':'after_local_pass'})
    result=check({**adapters,'remote':remote},encode('jane@example.com'),selected=p)
    assert not called and result.checker_results[-1].error_code=='external_local_gate'


def test_no_network(adapters,monkeypatch):
    deny_network(monkeypatch,'Network attempted')
    assert encoded(check(adapters,encode('jane@example.com'))).status=='flagged'


def test_raw_redaction_not_needed_is_not_an_overall_privacy_pass(adapters):
    result=check(adapters,encode('jane@example.com'),redact=True)
    assert result.redaction.status=='not_needed' and result.redaction.content is None
    assert 'raw PII checker' in result.redaction.explanation and 'overall decision' in result.redaction.explanation
    assert result.recommended_action=='block' and result.findings


@pytest.mark.parametrize('kind',['wrong-id','empty-flag','hidden-finding','external','simulated'])
def test_inconsistent_inner_results_cannot_become_a_pass(kind):
    from guardoath.encoded_pii import build_encoded_pii_checker
    from guardoath.models import Finding
    async def inner(request,p,stage):
        finding=Finding(rule_id='pii.email_address',category='privacy',severity='medium',explanation='Synthetic private result')
        return CheckerResult(checker_id='wrong' if kind=='wrong-id' else 'pii',checker_version='fixture',
            status='flagged' if kind=='empty-flag' else 'passed',findings=(finding,) if kind=='hidden-finding' else (),
            external=kind=='external',simulated=kind=='simulated')
    request=CheckRequest(application_id='demo',policy_version=policy().version,content=encode('jane@example.com'))
    result=asyncio.run(build_encoded_pii_checker(inner)(request,policy(),Stage.INPUT))
    assert result.status=='error' and result.error_code=='encoded_pii_scan_incomplete' and not result.findings


def test_policy_deadline_is_preserved_for_an_unfinished_inner_scan(adapters):
    from guardoath.encoded_pii import build_encoded_pii_checker
    async def inner(request,p,stage):await asyncio.Event().wait()
    result=check({**adapters,'encoded_pii':build_encoded_pii_checker(inner)},encode('jane@example.com'),selected=policy().model_copy(update={'timeout_ms':10}))
    assert result.evaluation_status=='incomplete' and encoded(result).error_code=='checker_timeout'
