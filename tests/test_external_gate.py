"""External egress is disabled by default and gated by real local results."""

import asyncio

import httpx2
import pytest

from guardoath.azure_prompt_shields import build_azure_prompt_shields
from guardoath.checkers import default_checkers
from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.models import Action, CheckerResult, CheckRequest, Finding, Policy, Rule, Stage


def request(content='Hello'):
    return CheckRequest(application_id='demo',policy_version='v1',content=content)


def fixtures(local_status='passed', simulated=False, external_required=True, gate='after_local_pass'):
    calls = []
    async def local(*args):
        calls.append('local')
        return CheckerResult(checker_id='local',checker_version='1',status=local_status,simulated=simulated,
            findings=(Finding(rule_id='local.finding',category='sensitive_data',severity='high',explanation='Fixture finding'),)
                      if local_status == 'flagged' else ())
    async def external(*args):
        calls.append('external')
        return CheckerResult(checker_id='external',checker_version='1',status='passed',external=True)
    external.external = True
    external.checker_version = '1'
    # Deliberately list external first: configuration order must not bypass the gate.
    policy = Policy(application_id='demo',version='v1',external_checks=gate,
        rules=(Rule(checker_id='external',required=external_required),Rule(checker_id='local',required=False,on_finding=Action.ALLOW)))
    return Engine([policy],{'local':local,'external':external}), calls


def test_locals_complete_before_external_even_when_external_is_listed_first():
    engine,calls = fixtures()
    result = asyncio.run(engine.check(request(),Stage.INPUT))
    assert calls == ['local','external']
    assert [r.checker_id for r in result.checker_results] == ['external','local']
    assert result.evaluation_status == 'complete'
    assert result.cost_basis == 'provider_unpriced'


@pytest.mark.parametrize('status', ['flagged','error','skipped','unsupported'])
@pytest.mark.parametrize('mode', ['observe','enforce'])
def test_any_local_nonpass_blocks_egress_regardless_of_optional_rule_or_action(status,mode):
    engine,calls = fixtures(local_status=status)
    # The mode only affects the final effective action, not permission to transfer.
    selected = next(iter(engine._policies.values())).model_copy(update={'mode':mode})
    engine = Engine([selected],engine._checkers)
    result = asyncio.run(engine.check(request(),Stage.INPUT))
    assert calls == ['local']
    assert result.checker_results[0].error_code == 'external_local_gate'
    assert result.checker_results[0].status == 'skipped'
    assert result.evaluation_status == 'incomplete'
    assert result.effective_action == ('allow' if mode == 'observe' else 'block')


def test_simulated_local_pass_cannot_authorize_egress():
    engine,calls = fixtures(simulated=True)
    result = asyncio.run(engine.check(request(),Stage.INPUT))
    assert calls == ['local']
    assert result.checker_results[0].error_code == 'external_local_gate'


def test_policy_must_explicitly_allow_external_gate():
    engine,calls = fixtures(gate='disabled')
    result = asyncio.run(engine.check(request(),Stage.INPUT))
    assert Policy(application_id='demo',version='empty',rules=()).external_checks == 'disabled'
    assert calls == ['local']
    assert result.checker_results[0].error_code == 'external_policy_disabled'
    assert engine.describe_policies('demo')[0]['external_checks'] == 'disabled'


def test_no_local_checks_is_not_a_pass():
    calls = []
    async def external(*args): calls.append('external')
    external.external = True
    policy = Policy(application_id='demo',version='v1',external_checks='after_local_pass',rules=(Rule(checker_id='external'),))
    result = asyncio.run(Engine([policy],{'external':external}).check(request(),Stage.INPUT))
    assert not calls
    assert result.checker_results[0].error_code == 'external_local_gate'


def test_missing_local_checker_prevents_egress():
    engine,calls = fixtures()
    engine = Engine(list(engine._policies.values()),{'external':engine._checkers['external']})
    result = asyncio.run(engine.check(request(),Stage.INPUT))
    assert not calls
    assert result.evaluation_status == 'incomplete'


def test_cancelled_local_scan_never_starts_provider():
    async def scenario():
        began = asyncio.Event()
        called = []
        async def local(*args):
            began.set()
            await asyncio.Event().wait()
        async def external(*args): called.append('external')
        external.external = True
        policy = Policy(application_id='demo',version='v1',external_checks='after_local_pass',rules=(Rule(checker_id='local'),Rule(checker_id='external')))
        task = asyncio.create_task(Engine([policy],{'local':local,'external':external}).check(request(),Stage.INPUT))
        await asyncio.wait_for(began.wait(),1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert not called
    asyncio.run(scenario())


@pytest.mark.parametrize('content', ['Contact jane@example.com', 'Example SSN: 219-09-9999', 'Card: 4111 1111 1111 1111', 'ghp'+'_'+'A'*36])
def test_actual_local_privacy_and_secret_detectors_stop_provider(content):
    pytest.importorskip('presidio_analyzer')
    calls = []
    def provider(message):
        calls.append(message)
        return httpx2.Response(200,json={'userPromptAnalysis':{'attackDetected':False}})
    adapter = build_azure_prompt_shields('https://unit-test.cognitiveservices.azure.com','fixture-key',transport=httpx2.MockTransport(provider))
    policy = load_policies('policies/azure-prompt-shields.json')[0]
    engine = Engine([policy],{**default_checkers(enable_pii=True),'azure_prompt_shields':adapter})
    result = asyncio.run(engine.check(CheckRequest(application_id='demo',policy_version=policy.version,content=content),Stage.INPUT))
    assert not calls
    assert result.checker_results[1].error_code == 'external_local_gate'
    assert result.findings


def test_missing_pii_detector_in_full_sample_policy_prevents_transfer():
    calls = []
    async def provider(*args): calls.append('provider')
    provider.external = True
    policy = load_policies('policies/azure-prompt-shields.json')[0]
    result = asyncio.run(Engine([policy],{**default_checkers(),'azure_prompt_shields':provider}).check(
        CheckRequest(application_id='demo',policy_version=policy.version,content='Hello'),Stage.INPUT))
    assert not calls
    assert result.checker_results[1].error_code == 'external_local_gate'


@pytest.mark.parametrize('transformed,expected_calls', [('clean response',['clean response']),('ghp'+'_'+'A'*36,[])])
def test_redaction_rechecks_use_same_gate_and_never_send_original(transformed,expected_calls):
    calls = []
    async def pii(req,*args):
        if req.content == 'original':
            return CheckerResult(checker_id='pii',checker_version='fixture',status='flagged',transformed_content=transformed,
                findings=(Finding(rule_id='pii.fixture',category='sensitive_data',severity='high',explanation='Fixture'),))
        return CheckerResult(checker_id='pii',checker_version='fixture',status='passed')
    async def external(req,*args):
        calls.append(req.content)
        return CheckerResult(checker_id='external',checker_version='fixture',status='passed',external=True)
    external.external = True
    selected = Policy(application_id='demo',version='v1',mode='enforce',external_checks='after_local_pass',rules=(
        Rule(checker_id='pii',on_finding=Action.REDACT),Rule(checker_id='secrets'),Rule(checker_id='external',required=False)))
    result = asyncio.run(Engine([selected],{**default_checkers(),'pii':pii,'external':external}).check(request('original'),Stage.INPUT))
    assert calls == expected_calls
    assert 'original' not in calls
    assert result.redaction.status == ('ready' if expected_calls else 'withheld')
    assert result.redaction.content == (transformed if expected_calls else None)


def test_full_sample_allows_clean_input_after_real_local_guards():
    pytest.importorskip('presidio_analyzer')
    calls = []
    def provider(message):
        calls.append(message)
        return httpx2.Response(200,json={'userPromptAnalysis':{'attackDetected':False}})
    adapter = build_azure_prompt_shields('https://unit-test.cognitiveservices.azure.com','fixture-key',transport=httpx2.MockTransport(provider))
    selected = load_policies('policies/azure-prompt-shields.json')[0]
    result = asyncio.run(Engine([selected],{**default_checkers(enable_pii=True),'azure_prompt_shields':adapter}).check(
        CheckRequest(application_id='demo',policy_version=selected.version,content='Explain your return instructions.'),Stage.INPUT))
    assert len(calls) == 1
    assert result.evaluation_status == 'complete' and result.recommended_action == 'allow'
