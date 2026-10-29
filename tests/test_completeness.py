import asyncio

import pytest
from fastapi.testclient import TestClient

from guardoath.app import create_app
from guardoath.checkers import default_checkers
from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.models import CheckRequest, Policy, RequiredSection, Stage


@pytest.fixture
def policy():
    return load_policies('policies/completeness.json')[0]


def evaluate(policy, content, stage=Stage.OUTPUT, content_format='text'):
    return asyncio.run(Engine([policy], default_checkers()).check(CheckRequest(
        application_id='demo', policy_version=policy.version, content=content,
        content_format=content_format), stage))


@pytest.mark.parametrize('content', [
    'Summary: Done.\nNext steps: Contact support.',
    'SUMMARY: Done.\nnext STEPS: Contact support.',
    'Summary:\nDone.\nNext steps:\nContact support.',
    'Summary: Done.\r\nNext steps: abcde',
])
def test_labeled_sections_pass(policy, content):
    result = evaluate(policy, content)
    assert not result.findings
    assert result.evaluation_status == 'complete'
    assert next(g for g in result.groups if g.group_id == 'business').status == 'passed'


@pytest.mark.parametrize('content', [
    'Summary: Done.', 'Summary: Done.\nNext steps:',
    'Summary: Done.\nNext steps: a b c d',
    'Summary: Done.\nNext steps: \nOther: Enough unrelated text.',
    'Summary: Done.\nNext steps: Contact support.\nNext steps: A duplicate.',
    'The Summary: Done. The Next steps: Contact support.',
    'Summary: Done.\n# Next steps\nContact support.',
    'Summary: Done.\n Next steps: Contact support.',
])
def test_missing_short_or_ambiguous_sections_flag(policy, content):
    result = evaluate(policy, content)
    assert 'completeness.next_steps' in {f.rule_id for f in result.findings}
    assert result.recommended_action == 'block'
    assert result.effective_action == 'allow'
    assert content not in result.model_dump_json()


def test_stage_specific_rules_and_enforcement(policy):
    assert evaluate(policy, 'Summary: Return request.', Stage.INPUT).recommended_action == 'allow'
    assert evaluate(policy, 'No summary.', Stage.INPUT).recommended_action == 'flag'
    result = evaluate(policy.model_copy(update={'mode':'enforce'}), 'Summary: Done.')
    assert result.effective_action == 'block'


def test_json_is_not_silently_certified(policy):
    result = evaluate(policy, '{"Summary":"Done"}', content_format='json')
    assert result.checker_results[0].status == 'skipped'
    assert result.evaluation_status == 'incomplete'
    assert result.recommended_action == 'block'


def test_labels_are_literal_not_semantic(policy):
    # Explicitly document the presence check's limits: bad advice can still pass.
    result = evaluate(policy, 'Summary: Incorrect facts.\nNext steps: Do nothing.')
    assert not result.findings


@pytest.mark.parametrize('change,match', [
    ({'required_sections': (RequiredSection(rule_id='a',label='Summary'),RequiredSection(rule_id='a',label='Other'))}, 'Duplicate completeness rule'),
    ({'required_sections': (RequiredSection(rule_id='a',label='Summary'),RequiredSection(rule_id='b',label='SUMMARY'))}, 'Duplicate completeness label'),
    ({'rules': ()}, 'require a completeness checker'),
])
def test_invalid_configuration_fails_startup(policy, change, match):
    with pytest.raises(ValueError, match=match):
        Engine([policy.model_copy(update=change)], default_checkers())


@pytest.mark.parametrize('label', [' name', 'name ', '.*', 'a\nb', 'name:', 'étiquette'])
def test_labels_have_bounded_literal_syntax(label):
    with pytest.raises(ValueError):
        RequiredSection(rule_id='a',label=label)


def test_api_uses_same_checker_and_does_not_echo_payload(policy):
    client = TestClient(create_app(engine=Engine([policy],default_checkers()), credentials={'test-key':'demo'}))
    response = client.post('/v1/checks/output',headers={'Authorization':'Bearer test-key'},json={
        'application_id':'demo','policy_version':policy.version,'content':'Summary: private-source-value'})
    assert response.status_code == 200
    assert response.json()['recommended_action'] == 'block'
    assert 'private-source-value' not in response.text
