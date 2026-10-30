import asyncio
import base64
import json

from fastapi.testclient import TestClient
import pytest

from secret_examples import EXAMPLES, BENIGN
from guardoath.app import create_app
from guardoath.checkers import default_checkers
from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.models import CheckRequest, ConfidentialTerm, Rule, Stage
from network_guard import deny_network


@pytest.fixture(scope='module')
def profiles(): return load_policies('policies/secrets-batch.json')


def assess(policy,content,stage='input',fmt='text',checkers=None):
    return asyncio.run(Engine([policy],checkers or default_checkers()).check(CheckRequest(application_id='demo',policy_version=policy.version,
                      content=content,content_format=fmt),Stage(stage)))


@pytest.mark.parametrize('name',sorted(EXAMPLES))
@pytest.mark.parametrize('stage',['input','output'])
@pytest.mark.parametrize('enforce',[False,True])
def test_provider_candidates_across_stages_and_modes(profiles,name,stage,enforce):
    selected=profiles[2] if enforce else profiles[0]
    # Extended assignment matching can add findings; only assert the provider signal.
    result=assess(selected,EXAMPLES[name],stage)
    assert 'secrets.'+name in {f.rule_id for f in result.findings}
    assert result.recommended_action == ('flag' if stage=='input' else 'block')
    assert result.effective_action == (result.recommended_action if enforce else 'allow')
    assert EXAMPLES[name] not in result.model_dump_json()
    assert result.evaluation_status=='complete'


@pytest.mark.parametrize('sample',BENIGN)
def test_benign_lookalikes_with_all_options_enabled(profiles,sample):
    result=assess(profiles[1],sample)
    assert result.findings==()
    assert result.evaluation_status=='complete'


@pytest.mark.parametrize('name',sorted(EXAMPLES))
@pytest.mark.parametrize('wrapper',['key','value','nested'])
def test_decoded_json_scans_keys_values_and_nested_containers(profiles,name,wrapper):
    candidate=EXAMPLES[name]
    data={candidate:'safe'} if wrapper=='key' else {'item':candidate} if wrapper=='value' else {'items':[{'value':candidate}]}
    result=assess(profiles[0],json.dumps(data).replace('_','\\u005f'),fmt='json')
    assert 'secrets.'+name in {f.rule_id for f in result.findings}


@pytest.mark.parametrize('assignment',[
 'password = "synthetic-example!42"', 'db_password: "changeme"', 'client_secret = \'a\'',
 '{"password":"synthetic-example!42"}', 'api_key = "some-arbitrary-value"',
 'access_token: "value"', 'refresh_token = "value"', 'auth_token="value"',
])
def test_opt_in_assignment_heuristic(profiles,assignment):
    assert 'secrets.credential_assignment' in {f.rule_id for f in assess(profiles[1],assignment).findings}
    assert 'secrets.credential_assignment' not in {f.rule_id for f in assess(profiles[0],assignment).findings}


@pytest.mark.parametrize('value',['synthetic-example!42','changeme','password','x'])
def test_json_secret_named_field_keeps_key_value_context(profiles,value):
    result=assess(profiles[1],json.dumps({'outer':[{'db_password':value}]}),fmt='json')
    assert 'secrets.credential_assignment' in {f.rule_id for f in result.findings}


@pytest.mark.parametrize('sample',['PROJECT LANTERN','project lantern','ＰＲＯＪＥＣＴ ＬＡＮＴＥＲＮ','This is not PROJECT LANTERN.'])
def test_configured_terms_are_literal_normalized_not_semantic(profiles,sample):
    result=assess(profiles[1],sample)
    assert 'confidential.internal_project' in {f.rule_id for f in result.findings}
    assert sample not in result.model_dump_json()


def test_term_stage_scope_and_no_general_document_classification(profiles):
    assert not assess(profiles[1],'An unreleased pricing plan exists.').findings
    assert 'confidential.unreleased_plan' in {f.rule_id for f in assess(profiles[1],'An unreleased pricing plan exists.','output').findings}
    assert not assess(profiles[1],'A proprietary document with no configured terms.').findings
    assert not assess(profiles[0],'PROJECT LANTERN').findings


@pytest.mark.parametrize('kind',['base64','hex'])
@pytest.mark.parametrize('name',['github_token','slack','private_key','basic_auth'])
def test_labeled_encodings_are_inspected_once(profiles,kind,name):
    raw=EXAMPLES[name].encode()
    token=base64.b64encode(raw).decode() if kind=='base64' else raw.hex()
    result=assess(profiles[1],kind+': '+token)
    assert 'secrets.'+name in {f.rule_id for f in result.findings}
    assert token not in result.model_dump_json()
    assert not assess(profiles[0],kind+': '+token).findings


def test_encoded_terms_and_assignments_use_same_rules(profiles):
    for text,rule in [('PROJECT LANTERN','confidential.internal_project'),('password="synthetic-value"','secrets.credential_assignment')]:
        token=base64.b64encode(text.encode()).decode()
        assert rule in {f.rule_id for f in assess(profiles[1],'base64: '+token).findings}
    nested=base64.b64encode(('base64: '+base64.b64encode(EXAMPLES['github_token'].encode()).decode()).encode()).decode()
    assert not assess(profiles[1],'base64: '+nested).findings


@pytest.mark.parametrize('sample,code',[
 ('base64: @bad','secret_encoding_invalid'),('hex: ff','secret_encoding_invalid'),('base64:','secret_encoding_invalid'),
 (' '.join(['hex: 61']*17),'secret_encoding_limit'),('base64: '+'A'*4097,'secret_encoding_limit'),
 (' '.join(['base64: '+'A'*4096]*3),'secret_encoding_limit'),('password="'+'a'*513+'"','secret_assignment_limit'),
])
def test_inspection_limits_and_invalid_payloads_are_incomplete(profiles,sample,code):
    result=assess(profiles[1],sample)
    assert result.evaluation_status=='incomplete'
    assert result.checker_results[0].error_code==code
    assert result.recommended_action=='block'
    assert sample not in result.model_dump_json()


def test_json_units_share_encoding_budget(profiles):
    assert assess(profiles[1],json.dumps(['hex: 61']*17),fmt='json').evaluation_status=='incomplete'
    assert assess(profiles[1],' '.join(['hex: 61']*16)).evaluation_status=='complete'


def test_options_require_secret_rule_and_terms_have_unique_ids_and_stages(profiles):
    for changes in [{'rules':()},{'confidential_terms':(ConfidentialTerm(rule_id='x',term='term one'),ConfidentialTerm(rule_id='x',term='term two'))},
                    {'confidential_terms':(ConfidentialTerm(rule_id='x',term='PROJECT'),ConfidentialTerm(rule_id='y',term='project'))},
                    {'rules':(Rule(checker_id='secrets',stages=(Stage.INPUT,)),)}]:
        with pytest.raises(ValueError): Engine([profiles[1].model_copy(update=changes)],default_checkers())


def test_term_literal_metacharacters_not_regex(profiles):
    selected=profiles[1].model_copy(update={'confidential_terms':(ConfidentialTerm(rule_id='x',term='a.*b'),)})
    assert not assess(selected,'axxxxxb').findings
    assert 'confidential.x' in {f.rule_id for f in assess(selected,'a.*b').findings}


def test_api_metadata_findings_and_logs_never_expose_terms_or_candidates(profiles,caplog):
    client=TestClient(create_app(engine=Engine([profiles[1]],default_checkers()),credentials={'test-key':'demo'}))
    headers={'Authorization':'Bearer test-key'}
    meta=client.get('/v1/applications/current',headers=headers)
    assert 'PROJECT LANTERN' not in meta.text
    response=client.post('/v1/checks/output',headers=headers,json={'application_id':'demo','policy_version':profiles[1].version,
                         'content':'PROJECT LANTERN '+EXAMPLES['github_token']})
    assert response.status_code==200
    for private in ['PROJECT LANTERN',EXAMPLES['github_token']]: assert private not in response.text+caplog.text
    assert response.json()['redaction']['content'] is None


def test_no_network_or_verification_for_extended_group(profiles,monkeypatch):
    import requests
    def deny(*args,**kwargs): raise AssertionError('Network or credential verification attempted')
    deny_network(monkeypatch,'Network or credential verification attempted')
    monkeypatch.setattr(requests.Session,'request',deny)
    for name,candidate in EXAMPLES.items():
        assert 'secrets.'+name in {f.rule_id for f in assess(profiles[1],candidate).findings}


@pytest.mark.parametrize('name',sorted(EXAMPLES))
def test_each_provider_pattern_holds_external_transfer(profiles,name):
    calls=[]
    async def provider(*args): calls.append('provider')
    provider.external=True
    selected=profiles[1].model_copy(update={'external_checks':'after_local_pass',
        'rules':profiles[1].rules+(Rule(checker_id='provider'),)})
    result=assess(selected,EXAMPLES[name],checkers={**default_checkers(),'provider':provider})
    assert calls==[]
    assert result.checker_results[1].error_code=='external_local_gate'


@pytest.mark.parametrize('suffix',['# pragma: allowlist secret','# gitleaks:allow','# noqa'])
@pytest.mark.parametrize('name',['gitlab_token','slack','sendgrid','azure_storage','pypi'])
def test_untrusted_suppression_comments_cannot_bypass_added_formats(profiles,suffix,name):
    assert 'secrets.'+name in {f.rule_id for f in assess(profiles[0],EXAMPLES[name]+' '+suffix).findings}


def test_request_context_is_outside_scope_not_silently_reported_as_scanned(profiles):
    selected=profiles[1]
    req=CheckRequest(application_id='demo',policy_version=selected.version,content='Hello',request_context=EXAMPLES['github_token']+' PROJECT LANTERN')
    result=asyncio.run(Engine([selected],default_checkers()).check(req,Stage.INPUT))
    assert not result.findings


def test_maximum_canonical_text_is_scanned_to_the_end(profiles):
    token=EXAMPLES['github_token']
    assert assess(profiles[0],' '*(32000-len(token))+token).findings


@pytest.mark.parametrize('term',['ab','   ',' term','term ','x'*201])
def test_invalid_term_configuration_is_rejected(term):
    with pytest.raises(ValueError): ConfidentialTerm(rule_id='x',term=term)


def test_group_coverage_exposes_options_without_exposing_term_values(profiles):
    result=assess(profiles[1],'Hello')
    group=next(g for g in result.groups if g.group_id=='secrets')
    assert 'Quoted assignments: enabled' in group.limitations
    assert 'active confidential-term rules: 1' in group.limitations
    assert 'PROJECT LANTERN' not in group.limitations


@pytest.mark.parametrize('prefix',['ghp','gho','ghu','ghs','ghr'])
def test_github_classic_prefix_variants(profiles,prefix):
    assert 'secrets.github_token' in {f.rule_id for f in assess(profiles[0],prefix+'_'+'A'*36).findings}


def test_github_fine_grained_and_openai_project_candidates(profiles):
    for value,rule in [('github_pat_'+'A'*22+'_'+'B'*59,'secrets.github_token'),
                       ('sk-proj-'+'A'*40+'T3BlbkFJ'+'B'*40,'secrets.openai_token')]:
        assert rule in {f.rule_id for f in assess(profiles[0],value).findings}


@pytest.mark.parametrize('marker',['DSA PRIVATE KEY','EC PRIVATE KEY','OPENSSH PRIVATE KEY','PGP PRIVATE KEY BLOCK',
    'PRIVATE KEY','RSA PRIVATE KEY','SSH2 ENCRYPTED PRIVATE KEY'])
def test_private_key_header_variants(profiles,marker):
    assert 'secrets.private_key' in {f.rule_id for f in assess(profiles[0],'-----BEGIN '+marker+'-----').findings}


@pytest.mark.parametrize('key',['secret','token'])
def test_additional_quoted_secret_field_names(profiles,key):
    assert 'secrets.credential_assignment' in {f.rule_id for f in assess(profiles[1],json.dumps({key:'synthetic-value'}),fmt='json').findings}


def test_user_guide_covers_every_group_case():
    from pathlib import Path
    suite=json.loads(Path('evals/secrets.json').read_text(encoding='utf-8'))
    guide=Path('docs/user-guide.md').read_text(encoding='utf-8')
    for case in suite['cases']:
        assert '`'+case['case_id']+'`' in guide


def test_putty_key_marker(profiles):
    assert 'secrets.private_key' in {f.rule_id for f in assess(profiles[0],'PuTTY-User-Key-File-2: ssh-rsa').findings}


@pytest.mark.parametrize('prefix',['glpat','gldt','glft','glsoat','glrt'])
def test_gitlab_common_prefixes(profiles,prefix):
    assert 'secrets.gitlab_token' in {f.rule_id for f in assess(profiles[0],prefix+'-'+'a'*20).findings}


@pytest.mark.parametrize('sample', ['GR1348941'+'a'*20,'glcbt-aa_'+'a'*20,'glimt-'+'a'*25,
    'glptt-'+'a'*40,'glagent-'+'a'*50,'glagent-'+'a'*1024,'gloas-'+'a'*64])
def test_gitlab_additional_shapes(profiles,sample):
    assert 'secrets.gitlab_token' in {f.rule_id for f in assess(profiles[0],sample).findings}


@pytest.mark.parametrize('prefix',['xoxa','xoxb','xoxp','xoxo','xoxs','xoxr'])
def test_slack_prefixes(profiles,prefix):
    assert 'secrets.slack' in {f.rule_id for f in assess(profiles[0],prefix+'-1234567890-1234567890-'+'a'*24).findings}


def test_slack_webhook_and_test_pypi_shape(profiles):
    for value,rule in [('https://hooks.slack.com/services/T000000/B000000/'+'A'*24,'secrets.slack'),
        ('pypi-AgENdGVzdC5weXBpLm9yZw'+'A'*70,'secrets.pypi')]:
        assert rule in {f.rule_id for f in assess(profiles[0],value).findings}


@pytest.mark.parametrize('prefix',['sk','rk'])
def test_stripe_live_prefixes(profiles,prefix):
    assert 'secrets.stripe_key' in {f.rule_id for f in assess(profiles[0],prefix+'_live_'+'A'*24).findings}


@pytest.mark.parametrize('scheme',['https','postgresql','mongodb','redis'])
def test_embedded_url_credentials_across_schemes(profiles,scheme):
    assert 'secrets.basic_auth' in {f.rule_id for f in assess(profiles[0],scheme+'://example-user:synthetic-pass@example.invalid/path').findings}


def test_same_confidential_term_can_have_distinct_nonoverlapping_stage_rules(profiles):
    selected=profiles[1].model_copy(update={'confidential_terms':(
        ConfidentialTerm(rule_id='incoming',term='PRIVATE PROJECT',stages=(Stage.INPUT,)),
        ConfidentialTerm(rule_id='outgoing',term='private project',stages=(Stage.OUTPUT,)))})
    assert {f.rule_id for f in assess(selected,'Private project').findings}=={'confidential.incoming'}
    assert {f.rule_id for f in assess(selected,'Private project','output').findings}=={'confidential.outgoing'}


def test_named_synthetic_fixtures_are_explicit_and_not_arbitrary_execution():
    from guardoath.evaluate import Suite
    from guardoath.synthetic_secrets import build_fixture
    for name in ['unknown','../../file','__import__','https://external.invalid/']:
        with pytest.raises(ValueError): build_fixture(name)
    raw={'format_version':'1','suite_id':'x','cases':[{'case_id':'x','group':'secrets','stage':'input',
        'request':{'application_id':'demo','policy_version':'secrets-v1','content':'ambiguous user content'},
        'synthetic_fixture':'github_token','expected':{'evaluation_status':'complete','recommended_action':'allow','effective_action':'allow','checker_statuses':{'secrets':'passed'}}}]}
    with pytest.raises(ValueError): Suite.model_validate(raw)
