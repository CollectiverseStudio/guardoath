"""Offline credential and redirect regressions. No network requests or real keys."""
import asyncio
import json
import logging
import urllib.request

import pytest

from guardoath.gateway_prototype.service import ProviderBinding
from guardoath.gateway_prototype import tokatlas

CANARY = 'offline-private-credential-canary'


@pytest.mark.parametrize('code', [301, 302, 303, 307, 308])
@pytest.mark.parametrize('target', [
    'https://api.tokatlas.com/other?private-query-canary',
    'https://untrusted.invalid/steal?private-query-canary',
    'http://untrusted.invalid/steal?private-query-canary',
])
def test_redirects_never_follow_or_forward_authorization(code, target, monkeypatch, caplog):
    sent = []
    def build_opener(handler):
        assert isinstance(handler, urllib.request.HTTPRedirectHandler)
        class Opener:
            def open(self, request, timeout):
                sent.append(request)
                handler.redirect_request(request, None, code, 'redirect', {}, target)
                pytest.fail('redirect was accepted')
        return Opener()
    monkeypatch.setattr(tokatlas.urllib.request, 'build_opener', build_opener)
    monkeypatch.setattr(tokatlas.urllib.request, 'urlopen', lambda *a, **k: pytest.fail('default redirect-capable opener used'))
    with pytest.raises(tokatlas.TokatlasProviderError) as error:
        tokatlas._http_post(tokatlas.ENDPOINT, {'Authorization': 'Bearer '+CANARY}, b'private-prompt-canary', 1)
    assert len(sent) == 1
    assert sent[0].full_url == tokatlas.ENDPOINT
    assert sent[0].get_header('Authorization') == 'Bearer '+CANARY
    assert not any(value in str(error.value)+caplog.text for value in [CANARY, 'private-query-canary', 'private-prompt-canary'])


@pytest.mark.parametrize('target', ['https://untrusted.invalid/', tokatlas.ENDPOINT+'?private-query-canary',
                                   'http://api.tokatlas.com/v1/chat/completions'])
def test_transport_rejects_noncanonical_endpoint_before_open(target, monkeypatch):
    monkeypatch.setattr(tokatlas.urllib.request, 'build_opener', lambda *a: pytest.fail('opener created'))
    with pytest.raises(tokatlas.TokatlasProviderError) as error:
        tokatlas._http_post(target, {'Authorization': 'Bearer '+CANARY}, b'{}', 1)
    assert CANARY not in str(error.value) and 'private-query-canary' not in str(error.value)


def test_successful_fixed_origin_request_remains_bounded(monkeypatch):
    calls = []
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, size):
            assert size == tokatlas.MAX_RESPONSE_BYTES + 1
            return b'{}'
    def build_opener(handler):
        class Opener:
            def open(self, request, timeout):
                calls.append((request.full_url, timeout))
                return Response()
        return Opener()
    monkeypatch.setattr(tokatlas.urllib.request, 'build_opener', build_opener)
    assert tokatlas._http_post(tokatlas.ENDPOINT, {'Authorization': 'Bearer '+CANARY}, b'{}', 1) == b'{}'
    assert calls == [(tokatlas.ENDPOINT, 1)]


def test_adapter_binding_representations_errors_and_bodies_hide_credential(caplog):
    bodies = []
    class CredentialBearingTransport:
        def __repr__(self): return CANARY
        def __call__(self, url, headers, body, timeout):
            assert headers['Authorization'] == 'Bearer '+CANARY
            bodies.append(body)
            raise RuntimeError(CANARY+' private-response-canary')
    adapter = tokatlas.TokatlasChatAdapter(model_limits=(('gpt-5', 128),), api_key=CANARY, transport=CredentialBearingTransport())
    binding = ProviderBinding('tenant-a', adapter)
    with caplog.at_level(logging.WARNING):
        logging.getLogger(__name__).warning('adapter=%r binding=%s', adapter, binding)
    assert CANARY not in repr(adapter)+str(adapter)+repr(binding)+str(binding)+caplog.text
    with pytest.raises(tokatlas.TokatlasProviderError) as error:
        asyncio.run(adapter.generate(message='private-prompt-canary', model='gpt-5'))
    assert CANARY not in str(error.value)
    assert 'private-response-canary' not in str(error.value)
    assert bodies and CANARY.encode() not in bodies[0]


def test_unconfigured_live_generation_budget_blocks_default_http(monkeypatch):
    monkeypatch.setattr(tokatlas.urllib.request, 'build_opener', lambda *a: pytest.fail('uncapped live request attempted'))
    adapter = tokatlas.TokatlasChatAdapter(model_limits=(('gpt-5', 128),), api_key=CANARY)
    assert CANARY not in repr(adapter)
    with pytest.raises(tokatlas.TokatlasProviderError, match='generation budgets'):
        asyncio.run(adapter.generate(message='private-prompt-canary', model='gpt-5'))

@pytest.mark.parametrize('reason', ['length', 'content_filter', 'unknown', None, 1])
@pytest.mark.parametrize('wrapped', [False, True])
def test_incomplete_or_unknown_completion_never_releases_text(reason, wrapped):
    reply = {'choices': [{'finish_reason': reason, 'message': {'content': 'private-output-canary'}}],
             'usage': {'prompt_tokens': 1, 'completion_tokens': 2}}
    if wrapped: reply = {'code': 200, 'data': reply}
    adapter = tokatlas.TokatlasChatAdapter(api_key=CANARY, model_limits=(('gpt-5', 128),),
        transport=lambda *args: json.dumps(reply).encode())
    with pytest.raises(tokatlas.TokatlasProviderError) as error:
        asyncio.run(adapter.generate(message='hello', model='gpt-5'))
    assert 'private-output-canary' not in str(error.value)

@pytest.mark.parametrize('code', [500, 401, '200', True, None])
def test_unsuccessful_application_code_is_not_a_completion(code):
    reply = {'code': code, 'data': {'choices': [{'finish_reason': 'stop',
        'message': {'content': 'private-output-canary'}}],
        'usage': {'prompt_tokens': 1, 'completion_tokens': 2}}}
    adapter = tokatlas.TokatlasChatAdapter(api_key=CANARY, model_limits=(('gpt-5', 128),),
        transport=lambda *args: json.dumps(reply).encode())
    with pytest.raises(tokatlas.TokatlasProviderError):
        asyncio.run(adapter.generate(message='hello', model='gpt-5'))

@pytest.mark.parametrize('calls', [None, []])
def test_tool_finish_reason_with_missing_proposals_still_marks_withheld(calls):
    reply = {'choices': [{'finish_reason': 'tool_calls', 'message': {'content': None, 'tool_calls': calls}}],
        'usage': {'prompt_tokens': 1, 'completion_tokens': 2}}
    adapter = tokatlas.TokatlasChatAdapter(api_key=CANARY, model_limits=(('gpt-5', 128),),
        transport=lambda *args: json.dumps(reply).encode())
    result = asyncio.run(adapter.generate(message='hello', model='gpt-5'))
    assert result.tool_calls == ('proposed_tool',)
    assert result.text == '[tool invocation withheld]'

@pytest.mark.parametrize('limits', [(('gpt-5', True),), (('gpt-5', 0),), (('gpt-5', 4097),),
    (('gpt-5', '128'),), (('gpt-5', float('nan')),), (('gpt-5', 128), ('gpt-5', 64)), {'gpt-5': 128}])
def test_invalid_trusted_limits_rejected_before_transport(limits):
    with pytest.raises(ValueError):
        tokatlas.TokatlasChatAdapter(api_key=CANARY, model_limits=limits,
            transport=lambda *args: pytest.fail('transport invoked'))

def test_unsupported_model_capabilities_fail_before_transport():
    adapter = tokatlas.TokatlasChatAdapter(api_key=CANARY, transport=lambda *args: pytest.fail('transport invoked'))
    with pytest.raises(tokatlas.TokatlasProviderError, match='not approved'):
        asyncio.run(adapter.generate(message='hello', model='gpt-5'))

@pytest.mark.parametrize('limit', [1, 4096])
def test_trusted_generation_bounds_are_sent_and_overreported_usage_rejected(limit):
    captured = []
    def transport(url, headers, body, timeout):
        captured.append(json.loads(body))
        return json.dumps({'code': 200, 'data': {'choices': [{'finish_reason': 'stop',
            'message': {'content': 'safe'}}], 'usage': {'prompt_tokens': 1, 'completion_tokens': limit+1}}}).encode()
    adapter = tokatlas.TokatlasChatAdapter(api_key=CANARY, model_limits=(('gpt-5', limit),), transport=transport)
    with pytest.raises(tokatlas.TokatlasProviderError):
        asyncio.run(adapter.generate(message='hello', model='gpt-5'))
    assert captured[0]['max_completion_tokens'] == limit
    assert captured[0]['n'] == 1 and captured[0]['store'] is False


def _raw_adapter(raw):
    return tokatlas.TokatlasChatAdapter(
        api_key=CANARY, model_limits=(('gpt-5', 128),),
        transport=lambda *args: raw.encode())


_VALID_CHOICE = '{"finish_reason":"stop","message":{"content":"safe"}}'
_VALID_USAGE = '{"prompt_tokens":1,"completion_tokens":2}'
_VALID_COMPLETION = '{"choices":[' + _VALID_CHOICE + '],"usage":' + _VALID_USAGE + '}'


@pytest.mark.parametrize('raw', [
    '{"code":500,"code":200,"data":' + _VALID_COMPLETION + '}',
    '{"code":200,"code":200,"data":' + _VALID_COMPLETION + '}',
    '{"choices":[],"choices":[' + _VALID_CHOICE + '],"usage":' + _VALID_USAGE + '}',
    '{"choices":[{"finish_reason":"length","finish_reason":"stop",'
    '"message":{"content":"safe"}}],"usage":' + _VALID_USAGE + '}',
    '{"choices":[{"finish_reason":"stop","message":{'
    '"tool_calls":[{}],"tool_calls":[],"content":"safe"}}],"usage":' + _VALID_USAGE + '}',
    '{"choices":[' + _VALID_CHOICE + '],"usage":{'
    '"prompt_tokens":1,"completion_tokens":129,"completion_tokens":2}}',
    '{"code":200,"data":{},"d\\u0061ta":' + _VALID_COMPLETION + '}',
    _VALID_COMPLETION[:-1] + ',"private-output-canary":1,"private-output-canary":2}',
])
def test_duplicate_response_members_fail_closed_at_every_depth(raw, caplog):
    with pytest.raises(tokatlas.TokatlasProviderError) as error:
        asyncio.run(_raw_adapter(raw).generate(message='private-prompt-canary', model='gpt-5'))
    for value in [CANARY, 'private-output-canary', 'private-prompt-canary']:
        assert value not in str(error.value) + caplog.text


@pytest.mark.parametrize('constant', ['NaN', 'Infinity', '-Infinity'])
def test_non_json_constants_rejected_even_in_extra_metadata(constant):
    raw = _VALID_COMPLETION[:-1] + ',"metadata":' + constant + '}'
    with pytest.raises(tokatlas.TokatlasProviderError):
        asyncio.run(_raw_adapter(raw).generate(message='hello', model='gpt-5'))


@pytest.mark.parametrize('second', [_VALID_CHOICE,
    '{"finish_reason":"tool_calls","message":{"content":null,"tool_calls":[{}]}}'])
def test_multiple_completions_cannot_hide_unexamined_choices(second):
    raw = '{"choices":[' + _VALID_CHOICE + ',' + second + '],"usage":' + _VALID_USAGE + '}'
    with pytest.raises(tokatlas.TokatlasProviderError):
        asyncio.run(_raw_adapter(raw).generate(message='hello', model='gpt-5'))


@pytest.mark.parametrize('wrapped', [False, True])
def test_unique_json_members_allow_same_names_in_separate_objects(wrapped):
    # Object-level uniqueness must not reject ordinary repeated field names
    # in independent metadata objects, or literal JSON-looking response text.
    reply = json.loads(_VALID_COMPLETION)
    reply['metadata'] = [{'name': 'one'}, {'name': 'two'}]
    reply['choices'][0]['message']['content'] = '{"code":500,"code":200}'
    if wrapped:
        reply = {'code': 200, 'data': reply}
    result = asyncio.run(_raw_adapter(json.dumps(reply)).generate(message='hello', model='gpt-5'))
    assert result.text == '{"code":500,"code":200}'
    assert result.output_tokens == 2
