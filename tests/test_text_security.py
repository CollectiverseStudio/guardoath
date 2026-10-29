import asyncio
import base64
import json

import pytest
from fastapi.testclient import TestClient

from guardoath.app import create_app
from guardoath.checkers import default_checkers
from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.models import CheckRequest, Stage


@pytest.fixture
def policy():
    return load_policies('policies/text-security.json')[0]


def evaluate(policy, content, content_format='text', stage=Stage.INPUT):
    return asyncio.run(Engine([policy], default_checkers()).check(CheckRequest(
        application_id='demo', policy_version=policy.version, content=content,
        content_format=content_format), stage))


def names(result):
    return {f.rule_id.removeprefix('text_security.') for f in result.findings}


@pytest.mark.parametrize('character', ['\u200b','\u202e','\u2066','\ufeff','\u00ad','\x00','\x7f','\x85'])
def test_hidden_control_signals(policy, character):
    result = evaluate(policy, 'a'+character+'b')
    assert names(result) == {'hidden_controls'}
    assert result.effective_action == 'allow'
    assert result.recommended_action == 'flag'
    assert next(g for g in result.groups if g.group_id == 'prompt_security').status == 'findings'


@pytest.mark.parametrize('content', ['Hello world.', 'عربي', '日本語', '👩\u200d💻', 'a\u200cb', 'line\nline\tvalue', 'SGVsbG8=', 'deadbeef', 'hexadecimal: abcd'])
def test_legitimate_text_and_unlabeled_data(policy, content):
    assert not evaluate(policy, content).findings


@pytest.mark.parametrize('content', ['base64: SGVsbG8=', 'BASE64: SGVsbG8=', 'hex: 48656c6c6f', 'base64: 4piD'])
def test_labeled_encoded_text_is_review_signal_even_when_benign(policy, content):
    assert names(evaluate(policy, content)) == {'encoded_text'}


@pytest.mark.parametrize('content', ['base64:', 'base64: @bad', 'base64: /w==', 'hex: 1', 'hex: ff', 'hex: zz', 'base64: SGVsbG8'])
def test_malformed_or_non_utf8_encoded_data(policy, content):
    assert names(evaluate(policy, content)) == {'invalid_encoding'}


def test_encoded_hidden_controls_and_no_payload_echo(policy):
    payload = 'private-source-value\u202e'
    token = base64.b64encode(payload.encode()).decode()
    result = evaluate(policy, 'base64: '+token)
    assert names(result) == {'encoded_text','encoded_hidden_controls'}
    assert payload not in result.model_dump_json()
    assert token not in result.model_dump_json()
    assert 'transformed_content' not in result.model_dump_json()


def test_json_decodes_escapes_and_scans_keys_and_values(policy):
    content = json.dumps({'key\u200b': ['base64: SGVsbG8=']})
    assert names(evaluate(policy, content, 'json')) == {'hidden_controls','encoded_text'}


@pytest.mark.parametrize('content', ['{"a":1,"a":2}', '{invalid', '[NaN]'])
def test_invalid_json_is_incomplete(policy, content):
    result = evaluate(policy, content, 'json')
    assert result.evaluation_status == 'incomplete'
    assert result.checker_results[0].error_code == 'invalid_json_for_text_inspection'


@pytest.mark.parametrize('content', [
    ' '.join(['base64: YQ==']*17), 'base64: '+'A'*4097,
    ' '.join(['base64: '+'A'*4096]*3),
])
def test_budget_exhaustion_cannot_pass(policy, content):
    result = evaluate(policy, content)
    assert result.evaluation_status == 'incomplete'
    assert result.checker_results[0].error_code == 'text_inspection_budget_exceeded'
    assert result.recommended_action == 'block'
    assert not result.findings


def test_budget_limits_apply_across_json_units(policy):
    result = evaluate(policy, json.dumps(['hex: 61']*17), 'json')
    assert result.evaluation_status == 'incomplete'


def test_boundary_budgets_and_single_layer_only(policy):
    assert evaluate(policy, ' '.join(['hex: 61']*16)).evaluation_status == 'complete'
    assert evaluate(policy, ' '.join(['base64: '+'A'*4096]*2)).evaluation_status == 'complete'
    nested = base64.b64encode(b'hex: e280ae').decode()
    assert names(evaluate(policy, 'base64: '+nested)) == {'encoded_text'}


def test_output_enforcement_is_policy_controlled(policy):
    enforced = policy.model_copy(update={'mode':'enforce'})
    result = evaluate(enforced, 'a\u200bb', stage=Stage.OUTPUT)
    assert result.effective_action == 'flag'
    assert result.simulated is False


def test_api_uses_same_checker_without_echo(policy):
    client = TestClient(create_app(engine=Engine([policy],default_checkers()),credentials={'test-key':'demo'}))
    result = client.post('/v1/checks/input',headers={'Authorization':'Bearer test-key'},json={
        'application_id':'demo','policy_version':policy.version,'content':'private-source-value\u200b'})
    assert result.status_code == 200
    assert result.json()['recommended_action'] == 'flag'
    assert 'private-source-value' not in result.text
