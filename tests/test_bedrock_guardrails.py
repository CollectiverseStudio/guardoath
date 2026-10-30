"""AWS adapter fixtures: SDK stubbed locally, no live service or accuracy claims."""
import asyncio
from copy import deepcopy
import threading
import time

import pytest
botocore = pytest.importorskip('botocore')
import boto3
from botocore.exceptions import ClientError, NoCredentialsError, ReadTimeoutError
from botocore.stub import Stubber
from guardoath.bedrock_guardrails import build_bedrock_guardrails, CATEGORIES, configuration, _sdk_call
from guardoath.checkers import default_checkers, simulated_checker
from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.models import CheckRequest, Rule, Stage

SOURCE = 'private-source-value'
CONTEXT = 'private-context-not-for-provider'


def data(detected=None, action='NONE', confidence='HIGH', length=len(SOURCE)):
    detected = set(detected or ())
    return {'action': action, 'outputs': [], 'guardrailCoverage': {'textCharacters': {'guarded': length, 'total': length}}, 'assessments': [{'contentPolicy': {'filters': [
        {'type': c, 'confidence': confidence, 'filterStrength': 'HIGH', 'detected': c in detected,
         'action': 'BLOCKED' if action == 'GUARDRAIL_INTERVENED' and c in detected else 'NONE'} for c in CATEGORIES]}}]}


def policy(**updates):
    p = load_policies('policies/content-safety.json')[0]
    return p.model_copy(update={'detect_labeled_sensitive_data': False,
        'rules': tuple(r for r in p.rules if r.checker_id != 'pii'), **updates})


def adapter(call):
    raw = build_bedrock_guardrails('us-east-1', 'syntheticguardrail', '1', call=call)
    async def run(request, policy, stage):
        return (await raw(request, policy, stage)).model_copy(update={'checker_id': 'content_safety'})
    run.external = True
    run.checker_version = raw.checker_version
    return run


def evaluate(call, stage=Stage.INPUT, configured=None, content=SOURCE, fmt='text', extras=None):
    p = configured or policy()
    engine = Engine([p], {**default_checkers(), **(extras or {}), 'content_safety': adapter(call)})
    return asyncio.run(engine.check(CheckRequest(application_id='demo', policy_version=p.version,
        content=content, content_format=fmt, request_context=CONTEXT), stage))


def check(result): return result.checker_results[-1]


# 5 categories x 4 confidence values x 2 detected values x 2 stages = 80
# protocol cases. Confidence is not a local severity threshold.
@pytest.mark.parametrize('stage', list(Stage))
@pytest.mark.parametrize('kind', list(CATEGORIES))
@pytest.mark.parametrize('confidence', ['NONE', 'LOW', 'MEDIUM', 'HIGH'])
@pytest.mark.parametrize('detected', [False, True])
def test_category_contract(stage, kind, confidence, detected, caplog):
    calls = []
    def call(region, identifier, version, text, source, timeout):
        calls.append(source)
        assert (region, identifier, version, text) == ('us-east-1', 'syntheticguardrail', '1', SOURCE)
        assert timeout == 2
        return data([kind] if detected else [], confidence=confidence)
    result = evaluate(call, stage)
    assert calls == [stage]
    assert check(result).status == ('flagged' if detected else 'passed')
    expected = ('block' if stage == Stage.OUTPUT else 'flag') if detected else 'allow'
    assert result.recommended_action == expected and result.effective_action == 'allow'
    assert result.evaluation_status == 'complete' and not result.simulated
    assert result.cost_usd is None and result.cost_basis == 'provider_unpriced'
    assert [f.rule_id for f in result.findings] == (['harmful_content.bedrock_' + CATEGORIES[kind]] if detected else [])
    for private in [SOURCE, CONTEXT, 'syntheticguardrail']:
        assert private not in result.model_dump_json() and private not in caplog.text


def invalid_rows():
    cases = [None, [], {}, {'action': 'INVALID', 'assessments': []},
             {'action': 'NONE', 'assessments': []}, {'action': 'NONE', 'assessments': [{}]}]
    for key, value in [('detected', 'false'), ('confidence', 'INVALID'), ('filterStrength', 'NONE'), ('type', 'UNKNOWN')]:
        d = data(); d['assessments'][0]['contentPolicy']['filters'][0][key] = value; cases.append(d)
    return cases


# 10 malformed contracts x both stages = 20 more; 100 contract matrix cases.
@pytest.mark.parametrize('stage', list(Stage))
@pytest.mark.parametrize('payload', invalid_rows(), ids=[f'malformed-{i+1}' for i in range(10)])
def test_malformed_contract(stage, payload):
    result = evaluate(lambda *args: payload, stage, configured=policy(mode='enforce'))
    assert check(result).error_code == 'bedrock_invalid_response'
    assert result.evaluation_status == 'incomplete' and result.effective_action == 'block'


@pytest.mark.parametrize('stage', list(Stage))
def test_block_and_detect_both_flag_and_stable_category_order(stage):
    for action in ['NONE', 'GUARDRAIL_INTERVENED']:
        d = data(list(CATEGORIES), action=action)
        d['assessments'][0]['contentPolicy']['filters'].reverse()
        d['outputs'] = [{'text': SOURCE}]
        d['actionReason'] = SOURCE
        result = evaluate(lambda *args: d, stage, configured=policy(mode='enforce'))
        assert len(result.findings) == 5
        assert [f.rule_id for f in result.findings] == ['harmful_content.bedrock_' + c for c in CATEGORIES.values()]
        assert result.effective_action == ('flag' if stage == Stage.INPUT else 'block')
        assert SOURCE not in result.model_dump_json()


def test_missing_duplicate_inactive_or_unexplained_assessment():
    cases = []
    d = data(); d['assessments'][0]['contentPolicy']['filters'].pop(); cases.append(d)
    d = data(); d['assessments'][0]['contentPolicy']['filters'][0] = d['assessments'][0]['contentPolicy']['filters'][1]; cases.append(d)
    d = data(); del d['assessments'][0]['contentPolicy']['filters'][0]['detected']; cases.append(d)
    d = data(); d['assessments'][0]['contentPolicy']['filters'][0]['action'] = 'BLOCKED'; cases.append(d)
    d = data(action='GUARDRAIL_INTERVENED'); cases.append(d)
    d = data(); d['assessments'][0]['sensitiveInformationPolicy'] = {'piiEntities': [{'match': SOURCE}]}; cases.append(d)
    d = data(); d['assessments'].append(deepcopy(d['assessments'][0])); cases.append(d)
    for d in cases:
        result = evaluate(lambda *args: d)
        assert check(result).error_code == 'bedrock_invalid_response'
        assert result.evaluation_status == 'incomplete' and SOURCE not in result.model_dump_json()


@pytest.mark.parametrize('code,error', [('AccessDeniedException', 'bedrock_access_denied'),
    ('ThrottlingException', 'bedrock_rate_limited'), ('ServiceQuotaExceededException', 'bedrock_rate_limited'),
    ('ResourceNotFoundException', 'bedrock_resource_missing'), ('ValidationException', 'bedrock_request_rejected'),
    ('InternalServerException', 'bedrock_provider_error'), ('ServiceUnavailableException', 'bedrock_provider_error')])
def test_sdk_errors_are_sanitized_and_no_adapter_retry(code, error):
    calls = []
    def call(*args):
        calls.append(1)
        raise ClientError({'Error': {'Code': code, 'Message': SOURCE}}, 'ApplyGuardrail')
    result = evaluate(call, configured=policy(mode='enforce'))
    assert calls == [1] and check(result).error_code == error
    assert result.evaluation_status == 'incomplete' and result.effective_action == 'block'
    assert SOURCE not in result.model_dump_json()


@pytest.mark.parametrize('exception,code', [(NoCredentialsError(), 'bedrock_credentials_missing'),
    (ReadTimeoutError(endpoint_url=SOURCE), 'bedrock_timeout'), (RuntimeError(SOURCE), 'bedrock_provider_error')])
def test_credentials_timeout_unexpected_failure(exception, code):
    def call(*args): raise exception
    result = evaluate(call)
    assert check(result).error_code == code and result.evaluation_status == 'incomplete'
    assert SOURCE not in result.model_dump_json()


@pytest.mark.parametrize('stage', list(Stage))
@pytest.mark.parametrize('text', ['hidden\u200btext', 'password = "synthetic-example!42"', 'hex: 61'])
def test_local_gate_both_stages(stage, text):
    calls = []
    def call(*args): calls.append(1); return data()
    result = evaluate(call, stage, content=text)
    assert calls == [] and check(result).error_code == 'external_local_gate'
    assert result.evaluation_status == 'incomplete'


def test_policy_disabled_no_local_or_simulated_gate():
    calls = []
    def call(*args): calls.append(1); return data()
    for p, extras, error in [
        (policy(external_checks='disabled'), {}, 'external_policy_disabled'),
        (policy(rules=(Rule(checker_id='content_safety'),), detect_secret_assignments=False, inspect_encoded_secrets=False), {}, 'external_local_gate'),
        (policy(rules=(Rule(checker_id='safety'), Rule(checker_id='content_safety')), detect_secret_assignments=False, inspect_encoded_secrets=False), {'safety': simulated_checker('safety')}, 'external_local_gate')]:
        result = evaluate(call, configured=p, extras=extras)
        assert check(result).error_code == error
    assert calls == []


def test_privacy_gate_and_missing_pii_never_send():
    pytest.importorskip('tldextract')
    pytest.importorskip('phonenumbers')
    p = load_policies('policies/content-safety.json')[0]
    calls = []
    def call(*args): calls.append(1); return data()
    for stage in Stage:
        for text in ['Email jane@example.com', 'MRN: A1234']:
            result = evaluate(call, stage, configured=p, content=text, extras=default_checkers(enable_pii=True))
            assert check(result).error_code == 'external_local_gate'
        result = evaluate(call, stage, configured=p)
        assert check(result).error_code == 'external_local_gate'
    assert not calls


@pytest.mark.parametrize('text,fmt,code', [('a'*10001, 'text', 'bedrock_input_limit'), ('{}', 'json', 'bedrock_text_only')])
def test_local_format_and_size_rejection(text, fmt, code):
    calls = []
    def call(*args): calls.append(1); return data()
    result = evaluate(call, content=text, fmt=fmt)
    assert not calls and check(result).error_code == code


def test_full_unicode_limit_no_truncation():
    text = '🌿' * 10000
    def call(*args): assert args[3] == text; return data(length=len(text))
    assert check(evaluate(call, content=text)).status == 'passed'


def test_timed_out_workers_keep_their_slots():
    started = threading.Event(); release = threading.Event(); count = []
    def call(*args):
        count.append(1)
        if len(count) == 2: started.set()
        assert release.wait(3)
        return data()
    async def scenario():
        # Long enough for the local checks that gate the external call on a slow runner; the
        # provider call below blocks for up to 3 s, so it still reaches the deadline.
        p = policy(timeout_ms=1000)
        engine = Engine([p], {**default_checkers(), 'content_safety': adapter(call)})
        request = CheckRequest(application_id='demo', policy_version=p.version, content=SOURCE)
        try:
            first = await asyncio.gather(engine.check(request, Stage.INPUT), engine.check(request, Stage.OUTPUT))
            assert started.is_set()
            assert all(c.status == 'passed' for r in first for c in r.checker_results if not c.external)
            assert all(check(r).error_code == 'checker_timeout' for r in first)
            third = await engine.check(request, Stage.INPUT)
            assert check(third).error_code == 'bedrock_busy' and len(count) == 2
        finally: release.set()
        # Wait via yields for worker callbacks to release, without assuming timing.
        for _ in range(100):
            last = await engine.check(request, Stage.INPUT)
            if check(last).status == 'passed': break
            await asyncio.sleep(.01)
        assert check(last).status == 'passed'
    asyncio.run(scenario())


def test_configuration_rejects_urls_arns_draft_and_missing_values():
    configuration('us-east-1', 'syntheticguardrail', '1')
    for values in [('', 'id', '1'), ('https://evil.example', 'id', '1'), ('us-gov-west-1', 'id', '1'),
        ('us-east-1', 'arn:aws:bedrock:us-east-1:123456789012:guardrail/id', '1'),
        ('us-east-1', '', '1'), ('us-east-1', 'id', 'DRAFT'), ('us-east-1', 'id', '0')]:
        with pytest.raises(ValueError): configuration(*values)


def test_selected_adapter_is_lazy_and_keeps_normalized_id(monkeypatch):
    from guardoath.content_safety import build_content_safety
    import guardoath.bedrock_guardrails as module
    calls = []
    def call(*args): calls.append(args); return data()
    monkeypatch.setattr(module, '_sdk_call', call)
    monkeypatch.setenv('GUARDOATH_AWS_REGION', 'us-east-1')
    monkeypatch.setenv('GUARDOATH_BEDROCK_GUARDRAIL_ID', 'syntheticguardrail')
    monkeypatch.setenv('GUARDOATH_BEDROCK_GUARDRAIL_VERSION', '1')
    selected = build_content_safety('bedrock')
    assert not calls
    p = policy()
    result = asyncio.run(selected(CheckRequest(application_id='demo', policy_version=p.version, content=SOURCE), p, Stage.OUTPUT))
    assert result.checker_id == 'content_safety' and result.external and not result.simulated
    assert selected.provider == 'bedrock' and len(calls) == 1


@pytest.mark.parametrize('stage', list(Stage))
def test_real_sdk_contract_with_stubber_no_network(monkeypatch, stage):
    client = boto3.client('bedrock-runtime', region_name='us-east-1', aws_access_key_id='synthetic', aws_secret_access_key='synthetic')
    expected = {'guardrailIdentifier': 'syntheticguardrail', 'guardrailVersion': '1',
        'source': stage.value.upper(), 'content': [{'text': {'text': SOURCE}}], 'outputScope': 'FULL'}
    payload = {**data(), 'usage': {'topicPolicyUnits': 0, 'contentPolicyUnits': 1, 'wordPolicyUnits': 0,
        'sensitiveInformationPolicyUnits': 0, 'sensitiveInformationPolicyFreeUnits': 0,
        'contextualGroundingPolicyUnits': 0}, 'guardrailCoverage': {'textCharacters': {'guarded': len(SOURCE), 'total': len(SOURCE)}}}
    stub = Stubber(client); stub.add_response('apply_guardrail', payload, expected)
    class Session:
        def __init__(self, **kwargs): assert kwargs == {'region_name': 'us-east-1'}
        def client(self, name, **kwargs):
            assert name == 'bedrock-runtime'
            assert kwargs['endpoint_url'] == 'https://bedrock-runtime.us-east-1.amazonaws.com'
            assert kwargs['verify'] is True
            cfg = kwargs['config']
            assert cfg.retries['total_max_attempts'] == 1 and cfg.proxies == {}
            assert cfg.ignore_configured_endpoint_urls and cfg.connect_timeout == 1 and cfg.read_timeout == 2
            return client
    monkeypatch.setattr(boto3, 'Session', Session)
    with stub:
        assert _sdk_call('us-east-1', 'syntheticguardrail', '1', SOURCE, stage, 2)['action'] == 'NONE'
        stub.assert_no_pending_responses()


@pytest.mark.parametrize('coverage', [None, {}, {'textCharacters': {}},
    {'textCharacters': {'guarded': 1, 'total': len(SOURCE)}},
    {'textCharacters': {'guarded': len(SOURCE), 'total': len(SOURCE) + 1}},
    {'textCharacters': {'guarded': True, 'total': len(SOURCE)}},
    {'textCharacters': {'guarded': len(SOURCE), 'total': str(len(SOURCE))}}])
def test_missing_or_partial_coverage_is_not_a_pass(coverage):
    payload = data(); payload['guardrailCoverage'] = coverage
    result = evaluate(lambda *args: payload)
    assert check(result).error_code == 'bedrock_invalid_response'
    assert result.evaluation_status == 'incomplete' and not result.findings
