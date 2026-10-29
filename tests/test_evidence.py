"""Citation consistency is distinct from truth/entailment of an answer."""
import asyncio
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
from guardoath.models import CheckRequest, EvidencePolicy, EvidenceSource, Policy, Rule, Stage

ROOT=Path(__file__).resolve().parents[1]
QUOTE='Unused items may be returned within 30 days.'


def policy(mode='observe'):
    return next(p for p in load_policies(ROOT/'policies/evidence-batch.json') if p.mode==mode)


def document(answer='Return details [[returns]].',quote=QUOTE,source='returns'):
    return {'answer':answer,'citations':[{'source_id':source,'quote':quote}]}


def check(value,stage=Stage.INPUT,mode='observe',configured=None,fmt='json'):
    p=configured or policy(mode)
    return asyncio.run(Engine([p],default_checkers()).check(CheckRequest(application_id='demo',policy_version=p.version,
        content=value if isinstance(value,str) else json.dumps(value),content_format=fmt),stage))


def test_complete_evidence_batch():
    report=asyncio.run(evaluate(load_suite(ROOT/'evals/evidence.json'),Engine(load_policies(ROOT/'policies/evidence-batch.json'),default_checkers())))
    assert report['summary']=={'total':100,'matched':100,'mismatched':0,'incomplete':36,'simulated':0}
    assert QUOTE not in json.dumps(report) and 'forged source' not in json.dumps(report)


@pytest.mark.parametrize('stage',list(Stage))
@pytest.mark.parametrize('mode',['observe','enforce'])
def test_stage_and_mode_decisions(stage,mode):
    result=check(document(quote='This invented quotation is not in the configured source.'),stage,mode)
    action='flag' if stage==Stage.INPUT else 'block'
    assert result.evaluation_status=='complete' and result.recommended_action==action
    assert result.effective_action==(action if mode=='enforce' else 'allow')
    assert [f.rule_id for f in result.findings]==['evidence.quote_mismatch']
    assert next(g for g in result.groups if g.group_id=='accuracy').status=='findings'
    assert not result.simulated and result.cost_usd==0


@pytest.mark.parametrize('answer,entries,expected',[
    ('No markers.',[],{'missing_citation'}),
    ('[[returns]]',[],{'missing_citation','missing_reference'}),
    ('No marker.',[{'source_id':'returns','quote':QUOTE}],{'missing_citation','unused_citation'}),
    ('[[unknown]]',[{'source_id':'unknown','quote':QUOTE}],{'unknown_source'}),
    ('[[returns]]',[{'source_id':'returns','quote':'Unused'}],{'quote_too_short'}),
    ('[[returns]]',[{'source_id':'returns','quote':QUOTE}]*2,{'duplicate_citation'}),
])
def test_each_citation_relationship(answer,entries,expected):
    result=check({'answer':answer,'citations':entries})
    assert {f.rule_id for f in result.findings}=={'evidence.'+x for x in expected}


def test_quotation_matching_is_exact_and_case_sensitive():
    assert not check(document()).findings
    for changed in [QUOTE.upper(),QUOTE.replace(' ','\n',1)]:
        assert {f.rule_id for f in check(document(quote=changed)).findings}=={'evidence.quote_mismatch'}


def test_quote_length_uses_nonpadding_characters():
    assert len(QUOTE[1:21].strip())==20 and not check(document(quote=QUOTE[1:21])).findings
    assert {f.rule_id for f in check(document(quote=QUOTE[2:21])).findings}=={'evidence.quote_too_short'}
    assert 'evidence.quote_too_short' in {f.rule_id for f in check(document(quote=' '*30+'Unused')).findings}


def test_valid_citation_does_not_establish_claim_entailment():
    result=check(document(answer='The Moon is made of cheese [[returns]].'))
    assert result.evaluation_status=='complete' and not result.findings
    # Explicit coverage gap: a matching quotation does not verify an unrelated claim.


def test_caller_cannot_substitute_the_trusted_catalog():
    value=document(quote='Invented quotation from attacker supplied source.')
    value['sources']=[{'source_id':'returns','text':value['citations'][0]['quote']}]
    result=check(value)
    assert result.evaluation_status=='incomplete' and result.checker_results[0].error_code=='evidence_invalid_document'


@pytest.mark.parametrize('answer',['[[return policy]]','[[returns','returns]]'])
def test_malformed_marker_is_a_review_finding(answer):
    assert 'evidence.invalid_marker' in {f.rule_id for f in check(document(answer=answer)).findings}


def test_marker_limit_is_shared_and_repeated_references_need_one_entry():
    assert not check(document(answer=' '.join(['[[returns]]']*20))).findings
    result=check(document(answer=' '.join(['[[returns]]']*21)))
    assert result.evaluation_status=='incomplete' and result.checker_results[0].error_code=='evidence_reference_limit'


@pytest.mark.parametrize('bad',['{bad','{"answer":"a","answer":"b","citations":[]}','{"answer":NaN,"citations":[]}','[]'])
def test_json_guards(bad):
    assert check(bad).checker_results[0].error_code=='evidence_invalid_document'


@pytest.mark.parametrize('field,value',[('answer',None),('answer',True),('answer',' '*10),('citations',None),('citations',[{}])])
def test_invalid_document_fields_are_incomplete(field,value):
    data=document();data[field]=value
    assert check(data).evaluation_status=='incomplete'


def test_text_does_not_pass_as_a_complete_accuracy_assessment():
    result=check('This answer seems factual.',fmt='text')
    assert result.evaluation_status=='incomplete' and result.checker_results[0].error_code=='evidence_json_required'


def test_policy_requires_catalog_and_checker_together():
    with pytest.raises(ValueError,match='configuration and checker'):
        Engine([Policy(application_id='demo',version='v1',rules=(Rule(checker_id='evidence'),))],default_checkers())
    with pytest.raises(ValueError,match='configuration and checker'):
        Engine([policy().model_copy(update={'rules':()})],default_checkers())
    with pytest.raises(ValidationError):EvidencePolicy(sources=())
    with pytest.raises(ValidationError):EvidencePolicy(sources=(EvidenceSource(source_id='x',text='a'),)*2)
    with pytest.raises(ValidationError):EvidenceSource(source_id='x',text='   ')
    with pytest.raises(ValidationError):EvidencePolicy(sources=(EvidenceSource(source_id='x',text='a'),),min_quote_characters=True)


def test_catalog_is_a_snapshot_and_is_not_disclosed_in_metadata_or_findings(caplog):
    p=policy();engine=Engine([p],default_checkers())
    client=TestClient(create_app(engine=engine,credentials={'test-key':'demo'}))
    headers={'Authorization':'Bearer test-key'}
    meta=client.get('/v1/applications/current',headers=headers)
    assert QUOTE not in meta.text and 'shipping' not in meta.text
    value=document(quote='private unsupported quotation with a secret value')
    for stage in Stage:
        response=client.post('/v1/checks/'+stage,headers=headers,json={'application_id':'demo','policy_version':p.version,
            'content':json.dumps(value),'content_format':'json'})
        assert response.status_code==200 and response.json()['findings']
        assert value['citations'][0]['quote'] not in response.text+caplog.text
        assert QUOTE not in response.text+caplog.text


def test_no_network_or_reference_lookup(monkeypatch):
    def deny(*args,**kwargs):raise AssertionError('Network attempted')
    monkeypatch.setattr(socket.socket,'connect',deny)
    result=check(document(source='unknown',answer='An unknown source [[unknown]].'))
    assert {f.rule_id for f in result.findings}=={'evidence.unknown_source'}
