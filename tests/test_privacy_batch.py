"""Supported privacy scope, transformations and safe failure paths."""
import asyncio
import json
from pathlib import Path
import socket
import sys

import pytest
from fastapi.testclient import TestClient

from valinator.app import create_app
from valinator.checkers import default_checkers
from valinator.config import load_policies
from valinator.engine import Engine
from valinator.evaluate import evaluate, load_suite
from valinator.models import CheckRequest, CheckerResult, Policy, Rule, Stage

pytestmark = pytest.mark.skipif(sys.version_info >= (3,15), reason='PII adapter supports Python below 3.15')
pytest.importorskip('presidio_analyzer')
ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture(scope='module')
def checkers():
    return default_checkers(enable_pii=True)


def assess(checkers, content, *, fmt='text', redact=False, schema=None, context=None, enabled=True):
    rules=[Rule(checker_id='pii',on_finding='redact' if redact else 'block')]
    if schema is not None:
        rules.append(Rule(checker_id='schema',on_finding='block'))
    policy=Policy(application_id='demo',version='v1',mode='enforce',timeout_ms=500,
                  rules=tuple(rules),detect_labeled_sensitive_data=enabled,input_schema=schema)
    request=CheckRequest(application_id='demo',policy_version='v1',content=content,content_format=fmt,request_context=context)
    return asyncio.run(Engine([policy],checkers).check(request,Stage.INPUT))


@pytest.mark.parametrize('fmt,content',[
    ('text','Phone: "202-555-0147"\nDOB: 1980-02-29\nMRN: A1234\nmember_id: M000123\nbank_account_number: 76543210'),
    ('json',json.dumps({'nested':[{'phone_number':'202-555-0147','date_of_birth':'1980-02-29','mrn':'A1234','member_id':'M000123','bank_account_number':76543210}]})),
])
def test_all_new_fields_are_rechecked_and_original_values_are_withheld(checkers,fmt,content):
    result=assess(checkers,content,fmt=fmt,redact=True)
    assert result.redaction.status=='ready'
    assert len(result.findings)==5
    assert all(c.status=='passed' for c in result.redaction.validation_results)
    for entity in ['PHONE_NUMBER','DATE_OF_BIRTH','MEDICAL_RECORD_NUMBER','HEALTH_PLAN_ID','BANK_ACCOUNT']:
        assert '[REDACTED_'+entity+']' in result.redaction.content
    for value in ['202-555-0147','1980-02-29','A1234','M000123','76543210']:
        assert value not in result.model_dump_json()
    if fmt=='json':
        assert json.loads(result.redaction.content)['nested'][0]['bank_account_number']=='[REDACTED_BANK_ACCOUNT]'


def test_redaction_preserves_quotes_indentation_and_unrelated_text(checkers):
    result=assess(checkers,'  Phone = "202-555-0147"  \nSummary: Keep this text.',redact=True)
    assert result.redaction.content=='  Phone = "[REDACTED_PHONE_NUMBER]"  \nSummary: Keep this text.'


def test_json_key_detection_still_withholds_transformed_content(checkers):
    result=assess(checkers,'{"DOB: 1980-02-29":"ordinary value"}',fmt='json',redact=True)
    assert result.redaction.status=='withheld'
    assert result.redaction.content is None
    assert result.effective_action=='block'


def test_replacement_that_breaks_schema_is_withheld(checkers):
    result=assess(checkers,'{"bank_account_number":76543210}',fmt='json',redact=True,
                   schema={'type':'object','properties':{'bank_account_number':{'type':'integer'}}})
    assert result.redaction.status=='withheld'
    assert result.redaction.content is None
    assert result.effective_action=='block'


@pytest.mark.parametrize('content,fmt',[
 ('MRN: '+'A'*129,'text'),
 ('\n'.join(['MRN: A1234']*65),'text'),
 (json.dumps([{'MRN':'A1234'}]*65),'json'),
])
def test_shared_field_limits_fail_without_partial_success(checkers,content,fmt):
    result=assess(checkers,content,fmt=fmt)
    assert result.evaluation_status=='incomplete'
    assert result.effective_action=='block'
    assert result.checker_results[0].error_code=='pii_field_limit'
    assert not result.findings


def test_limit_boundary_allows_64_fields(checkers):
    result=assess(checkers,json.dumps([{'MRN':'A1234'}]*64),fmt='json')
    assert result.evaluation_status=='complete'
    assert {f.rule_id for f in result.findings}=={'pii.medical_record_number'}


@pytest.mark.parametrize('content,fmt',[
 ('My birth date is 1980-02-29.','text'),
 ('{"mrn":true,"phone_number":202.5}','json'),
 ('{"MRN_custom":"A1234"}','json'),
 ('Date of birth: 29/02/1980','text'),
 ('Phone: ２０２５５５０１４７','text'),
 ('MRN: A1234; member_id: M000123','text'),
 ('Diagnosis: Example condition\nName: Synthetic Person','text'),
])
def test_explicitly_unsupported_formats_do_not_become_semantic_claims(checkers,content,fmt):
    result=assess(checkers,content,fmt=fmt)
    assert not result.findings


def test_context_is_not_scanned_and_opt_in_remains_required(checkers):
    assert not assess(checkers,'Ordinary message',context='MRN: A1234').findings
    assert not assess(checkers,'MRN: A1234',enabled=False).findings


def test_new_fields_do_not_use_network_or_echo_values(checkers,monkeypatch,caplog):
    def deny(*args,**kwargs):
        raise AssertionError('Network attempted')
    monkeypatch.setattr(socket.socket,'connect',deny)
    engine=Engine(load_policies(ROOT/'policies/privacy-batch.json'),checkers)
    client=TestClient(create_app(engine=engine,credentials={'test-key':'demo'}))
    response=client.post('/v1/checks/output',headers={'Authorization':'Bearer test-key'},json={
        'application_id':'demo','policy_version':'pii-extended-v1','content':'MRN: A1234'})
    assert response.status_code==200
    assert response.json()['findings'][0]['rule_id']=='pii.medical_record_number'
    assert 'A1234' not in response.text+caplog.text


def test_labeled_identifier_withholds_external_adapter(checkers):
    calls=[]
    async def external(*args):
        calls.append('external')
        return CheckerResult(checker_id='external',checker_version='1',status='passed',external=True)
    external.external=True
    policy=Policy(application_id='demo',version='v1',detect_labeled_sensitive_data=True,
                  external_checks='after_local_pass',rules=(Rule(checker_id='external'),Rule(checker_id='pii')))
    result=asyncio.run(Engine([policy],{**checkers,'external':external}).check(
        CheckRequest(application_id='demo',policy_version='v1',content='MRN: A1234'),Stage.INPUT))
    assert calls==[]
    assert result.checker_results[0].error_code=='external_local_gate'


def test_privacy_group_batch_has_exact_expectations_and_private_reports(checkers):
    report=asyncio.run(evaluate(load_suite(ROOT/'evals/privacy.json'),
                               Engine(load_policies(ROOT/'policies/privacy-batch.json'),checkers)))
    assert report['summary']=={'total':100,'matched':100,'mismatched':0,'incomplete':12,'simulated':0}
    for value in ['A1234','M000123','jane@example.com','1980-02-29','76543210']:
        assert value not in json.dumps(report)


def test_windows_line_endings_and_escaped_json_labels(checkers):
    result=assess(checkers,'MRN: A1234\r\nDOB: 1980-02-29\r\n',redact=True)
    assert result.redaction.status=='ready'
    assert result.redaction.content=='MRN: [REDACTED_MEDICAL_RECORD_NUMBER]\r\nDOB: [REDACTED_DATE_OF_BIRTH]\r\n'
    result=assess(checkers,r'{"m\u0072n":["A1234","B5678"]}',fmt='json')
    assert {f.rule_id for f in result.findings}=={'pii.medical_record_number'}
