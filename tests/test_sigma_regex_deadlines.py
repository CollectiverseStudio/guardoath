"""Regex fidelity and resource bounds without a regex-engine migration."""
import re
import subprocess
import sys
import time

import pytest

from guardoath.sigma_rules import (
    SigmaEvaluationError, SigmaRegexTimeout, UnsupportedSigmaRule, import_rule,
)
from guardoath.sigma_rules import bounded_regex

PATH = 'rules/windows/powershell/powershell_script/example.yml'


def candidate(pattern):
    return {'id': 'regex-fixture', 'title': 'regex fixture', 'author': 'test fixture',
            'logsource': {'category': 'ps_script', 'product': 'windows'},
            'detection': {'selection': {'ScriptBlockText|re': pattern}, 'condition': 'selection'}}


@pytest.mark.parametrize('pattern,text', [
    ('Get-Process', 'Get-Process -Name lab'),
    ('Get-Process', 'get-process -Name lab'),
    ('(?i)Get-Process', 'get-process -Name lab'),
    (r'\bGet-(?:Process|Service)\b', 'Get-Service'),
    (r'^Get-Process$', 'Get-Process\n'),
    (r'^Get-Process\Z', 'Get-Process\n'),
    (r'(?s)^a.*b$', 'a\nb'),
    (r'(?m)^Get-Process$', 'lab\nGet-Process\n'),
    (r'(Get-)\1', 'Get-Get-'),
    (r'(?<=Get-)Process', 'Get-Process'),
    (r'\w+', '\u0301'),
    (r'(?i)k', '\u212a'),
    # Fuzzy syntax in the third-party regex engine is literal syntax in re.
    (r'(cat){e<=1}', 'cat'),
    (r'(cat){e<=1}', 'cat{e<=1}'),
    (r'[a-z]+', '123'),
])
def test_bounded_search_preserves_existing_python_regex_semantics(pattern, text):
    expected = re.search(pattern, text) is not None
    rule = import_rule(candidate(pattern), PATH)
    assert rule.matches({'ScriptBlockText': text}) is expected


def test_missing_and_malformed_field_values_do_not_launch_worker(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('a non-string/missing field must not launch regex work')
    monkeypatch.setattr(bounded_regex.subprocess, 'run', forbidden)
    rule = import_rule(candidate('Get-Process'), PATH)
    for event in ({}, {'ScriptBlockText': None}, {'ScriptBlockText': []}, {'ScriptBlockText': 1}):
        assert not rule.matches(event)


@pytest.mark.parametrize('pattern', ['[', '(?V1)abc', 'a' * 513, 'a{99999999999999999999}'])
def test_invalid_or_oversized_patterns_rejected_before_worker(pattern, monkeypatch):
    monkeypatch.setattr(bounded_regex.subprocess, 'run', lambda *a, **k: pytest.fail('worker launched'))
    with pytest.raises(UnsupportedSigmaRule):
        import_rule(candidate(pattern), PATH)


def test_oversized_regex_in_unused_branch_is_rejected():
    data = candidate('Get-Process')
    data['detection']['dead'] = {'ScriptBlockText|re': 'a'*513}
    with pytest.raises(UnsupportedSigmaRule):
        import_rule(data, PATH)


def test_pattern_and_event_size_boundaries():
    rule = import_rule(candidate('a'*512), PATH)
    assert rule.matches({'ScriptBlockText': 'a'*32_000})
    with pytest.raises(SigmaEvaluationError, match='bounds'):
        rule.matches({'ScriptBlockText': 'a'*32_001})


def test_catastrophic_backtracking_raises_bounded_explicit_error():
    rule = import_rule(candidate('(a+)+$'), PATH)
    start = time.monotonic()
    with pytest.raises(SigmaRegexTimeout, match='deadline'):
        rule.matches({'ScriptBlockText': 'a'*30 + '!'} )
    # Allow OS scheduling/worker cleanup time while ruling out an uncontrolled hang.
    assert time.monotonic() - start < 5
    assert issubclass(SigmaRegexTimeout, SigmaEvaluationError)
    # A subsequent independent match still works after the timed-out worker is reaped.
    assert import_rule(candidate('^safe$'), PATH).matches({'ScriptBlockText': 'safe'})


def test_worker_protocol_uses_stdin_and_suppresses_sensitive_errors(monkeypatch):
    secret = 'customer-private-text'
    seen = {}
    def run(args, **kwargs):
        seen.update(args=args, kwargs=kwargs)
        return subprocess.CompletedProcess(args, 0, '1')
    monkeypatch.setattr(bounded_regex.subprocess, 'run', run)
    assert bounded_regex.bounded_search('private', secret)
    assert secret not in ' '.join(seen['args'])
    assert seen['args'][:3] == [sys.executable, '-I', '-c']
    assert secret in seen['kwargs']['input']
    assert seen['kwargs']['stderr'] == subprocess.DEVNULL
    # The shared event deadline can tighten, but never enlarge, this ceiling.
    assert 0 < seen['kwargs']['timeout'] <= bounded_regex.REGEX_TIMEOUT_SECONDS


@pytest.mark.parametrize('failure', ['timeout', 'oserror', 'exit', 'protocol'])
def test_worker_failure_is_never_reported_as_clean_nonmatch(failure, monkeypatch):
    secret = 'private-pattern-and-event'
    def run(args, **kwargs):
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(args, 1, output=secret, stderr=secret)
        if failure == 'oserror':
            raise OSError(secret)
        return subprocess.CompletedProcess(args, 2 if failure == 'exit' else 0, secret)
    monkeypatch.setattr(bounded_regex.subprocess, 'run', run)
    with pytest.raises(SigmaEvaluationError) as error:
        import_rule(candidate('private'), PATH).matches({'ScriptBlockText': secret})
    assert secret not in str(error.value)
