"""Provider contract tests use local HTTP fixtures only, never Azure traffic."""

import asyncio
import json

from fastapi.testclient import TestClient
import httpx2
import pytest

from valinator.app import create_app
from valinator.azure_prompt_shields import build_azure_prompt_shields, validate_endpoint
from valinator.checkers import default_checkers
from valinator.config import load_policies
from valinator.engine import Engine
from valinator.models import CheckRequest, Stage

ENDPOINT = 'https://unit-test.cognitiveservices.azure.com'
KEY = 'synthetic-provider-key'


def policy():
    return load_policies('policies/azure-prompt-shields.json')[0]


def checker(handler):
    return build_azure_prompt_shields(ENDPOINT, KEY, transport=httpx2.MockTransport(handler))


def evaluate(adapter, *, content='private-source-value', content_format='text', stage=Stage.INPUT, configured=None):
    selected = configured or policy()
    engine = Engine([selected],{**default_checkers(),'azure_prompt_shields':adapter})
    return asyncio.run(engine.check(CheckRequest(application_id='demo',policy_version=selected.version,
        content=content,content_format=content_format,request_context='do-not-send-context'),stage))


@pytest.mark.parametrize('attack', [True,False])
def test_real_adapter_contract_and_routing_with_fixture(attack,caplog):
    called = []
    def handler(request):
        called.append(request)
        assert request.method == 'POST'
        assert request.url.host == 'unit-test.cognitiveservices.azure.com'
        assert request.url.path == '/contentsafety/text:shieldPrompt'
        assert request.url.params['api-version'] == '2024-09-01'
        assert request.headers['Ocp-Apim-Subscription-Key'] == KEY
        assert 'authorization' not in request.headers
        assert json.loads(request.content) == {'userPrompt':'private-source-value','documents':[]}
        assert 'do-not-send-context' not in request.content.decode()
        return httpx2.Response(200,json={'userPromptAnalysis':{'attackDetected':attack},'documentsAnalysis':[]})
    result = evaluate(checker(handler))
    assert len(called) == 1
    assert result.evaluation_status == 'complete'
    assert result.recommended_action == ('flag' if attack else 'allow')
    assert result.effective_action == 'allow'
    assert result.cost_usd is None and result.cost_basis == 'provider_unpriced'
    assert result.simulated is False
    assert result.checker_results[1].external is True
    for private in ['private-source-value','do-not-send-context',KEY]:
        assert private not in result.model_dump_json()
        assert private not in caplog.text


@pytest.mark.parametrize('status,error', [(401,'azure_authorization_failed'),(403,'azure_authorization_failed'),
    (429,'azure_rate_limited'),(500,'azure_provider_error'),(302,'azure_provider_error'),(204,'azure_provider_error')])
def test_errors_do_not_become_a_pass_or_echo_provider_bodies(status,error):
    called = []
    def handler(request):
        called.append(request)
        return httpx2.Response(status,headers={'Location':'https://untrusted.example/'},text='private-source-value '+KEY)
    result = evaluate(checker(handler),configured=policy().model_copy(update={'mode':'enforce'}))
    assert len(called) == 1
    assert result.checker_results[1].error_code == error
    assert result.evaluation_status == 'incomplete'
    assert result.effective_action == 'block'
    assert result.cost_usd is None
    assert KEY not in result.model_dump_json()


@pytest.mark.parametrize('payload', [
    b'not-json', b'null', b'[]', b'{}',
    b'{"userPromptAnalysis":{"attackDetected":"false"}}',
    b'{"userPromptAnalysis":{"attackDetected":0}}',
    b'{"userPromptAnalysis":{"attackDetected":false,"attackDetected":true}}',
    b'{"userPromptAnalysis":{"attackDetected":false},"documentsAnalysis":[{}]}',
    b'x'*16385,
])
def test_invalid_responses_are_incomplete(payload):
    result = evaluate(checker(lambda request: httpx2.Response(200,content=payload)))
    assert result.checker_results[1].error_code == 'azure_invalid_response'
    assert result.evaluation_status == 'incomplete'


@pytest.mark.parametrize('headers', [{'Content-Length':'99999999'},{'Content-Length':'invalid'},{'Content-Encoding':'gzip'}])
def test_response_header_limits(headers):
    result = evaluate(checker(lambda request: httpx2.Response(200,headers=headers,content=b'')))
    assert result.checker_results[1].error_code == 'azure_invalid_response'


@pytest.mark.parametrize('exception,code', [(httpx2.ReadTimeout,'azure_timeout'),(httpx2.ConnectError,'azure_connection_failed')])
def test_transport_errors_are_sanitized(exception,code):
    def handler(request): raise exception('private-source-value '+KEY,request=request)
    result = evaluate(checker(handler))
    assert result.checker_results[1].error_code == code
    assert result.evaluation_status == 'incomplete'
    assert result.cost_basis == 'provider_unpriced'
    assert KEY not in result.model_dump_json()


def test_engine_deadline_preserves_external_metadata():
    async def handler(request): await asyncio.Event().wait()
    result = evaluate(checker(handler),configured=policy().model_copy(update={'timeout_ms':10}))
    assert result.checker_results[1].error_code == 'checker_timeout'
    assert result.checker_results[1].external is True
    assert result.cost_usd is None


@pytest.mark.parametrize('format,content,code', [('json','{}','azure_text_only'),('text','a'*4001,'azure_input_limit')])
def test_local_rejection_makes_no_request(format,content,code):
    def handler(request): raise AssertionError('No outbound call expected')
    result = evaluate(checker(handler),content=content,content_format=format)
    assert result.checker_results[1].error_code == code
    assert result.evaluation_status == 'incomplete'


def test_output_is_not_sent_to_prompt_api():
    def handler(request): raise AssertionError('No output call expected')
    result = evaluate(checker(handler),stage=Stage.OUTPUT)
    assert [r.checker_id for r in result.checker_results] == ['text_security']
    assert result.cost_usd == 0 and result.cost_basis == 'local_only'
    raw = asyncio.run(checker(handler)(CheckRequest(application_id='demo',policy_version='v1',content='answer'),policy(),Stage.OUTPUT))
    assert raw.status == 'unsupported'


@pytest.mark.parametrize('endpoint', [
    'http://unit-test.cognitiveservices.azure.com','https://localhost', 'https://127.0.0.1',
    'https://unit-test.cognitiveservices.azure.com.evil.example',
    'https://user:pass@unit-test.cognitiveservices.azure.com',
    'https://unit-test.cognitiveservices.azure.com:444',
    'https://unit-test.cognitiveservices.azure.com/path',
    'https://unit-test.cognitiveservices.azure.com/?key=secret',
    'https://unit-test.cognitiveservices.azure.com/#fragment', 'not-an-endpoint',
])
def test_endpoint_restrictions(endpoint):
    with pytest.raises(ValueError,match='HTTPS public-Azure resource origin'):
        validate_endpoint(endpoint)


@pytest.mark.parametrize('key', ['', 'a\nb', 'a b', 'é', 'a'*513])
def test_invalid_keys_do_not_echo_value(key):
    with pytest.raises(ValueError,match='subscription key is missing or invalid'):
        build_azure_prompt_shields(ENDPOINT,key)


def test_azure_off_by_default_and_bad_flag_rejected(monkeypatch):
    monkeypatch.setenv('VALINATOR_AZURE_CONTENT_SAFETY_ENDPOINT','invalid')
    monkeypatch.setenv('VALINATOR_AZURE_CONTENT_SAFETY_KEY','ignored-when-disabled')
    assert 'azure_prompt_shields' not in default_checkers()
    assert create_app(credentials={'test-key':'demo'})
    monkeypatch.setenv('VALINATOR_ENABLE_AZURE_PROMPT_SHIELDS','yes')
    with pytest.raises(ValueError,match='must be 0 or 1'): create_app()


def test_explicit_enable_requires_configuration(monkeypatch):
    monkeypatch.setenv('VALINATOR_ENABLE_AZURE_PROMPT_SHIELDS','1')
    monkeypatch.delenv('VALINATOR_AZURE_CONTENT_SAFETY_ENDPOINT',raising=False)
    with pytest.raises(ValueError,match='HTTPS public-Azure resource origin'): create_app()


def test_enabled_startup_metadata_does_not_disclose_key_or_endpoint(monkeypatch):
    monkeypatch.setenv('VALINATOR_ENABLE_AZURE_PROMPT_SHIELDS','1')
    monkeypatch.setenv('VALINATOR_AZURE_CONTENT_SAFETY_ENDPOINT',ENDPOINT)
    monkeypatch.setenv('VALINATOR_AZURE_CONTENT_SAFETY_KEY',KEY)
    monkeypatch.setenv('VALINATOR_POLICY_FILE','policies/azure-prompt-shields.json')
    client = TestClient(create_app(credentials={'test-key':'demo'}))
    response = client.get('/v1/applications/current',headers={'Authorization':'Bearer test-key'})
    assert response.status_code == 200
    metadata = response.json()['policies'][0]['checks'][1]
    assert metadata['external'] is True and metadata['stages'] == ['input']
    assert KEY not in response.text and ENDPOINT not in response.text


def test_unavailable_provider_remains_incomplete_without_network():
    selected = policy()
    client = TestClient(create_app(engine=Engine([selected],default_checkers()),credentials={'test-key':'demo'}))
    result = client.post('/v1/checks/input',headers={'Authorization':'Bearer test-key'},json={
        'application_id':'demo','policy_version':selected.version,'content':'Hello'})
    assert result.json()['evaluation_status'] == 'incomplete'
    assert result.json()['checker_results'][1]['status'] == 'unsupported'


def test_streamed_response_limit_does_not_depend_on_content_length():
    class Chunks(httpx2.AsyncByteStream):
        async def __aiter__(self):
            yield b' ' * 8192
            yield b' ' * 8193
    result = evaluate(checker(lambda request: httpx2.Response(200,stream=Chunks())))
    assert result.checker_results[1].error_code == 'azure_invalid_response'
    assert result.evaluation_status == 'incomplete'


def test_unexpected_adapter_failure_preserves_external_cost_metadata():
    def handler(request): raise RuntimeError('private-source-value '+KEY)
    result = evaluate(checker(handler))
    assert result.checker_results[1].error_code == 'checker_failure'
    assert result.checker_results[1].external is True
    assert result.cost_basis == 'provider_unpriced' and result.cost_usd is None
    assert KEY not in result.model_dump_json()
