"""Fail-closed import validation is independent of event/branch evaluation."""
from copy import deepcopy

import pytest

from guardoath.sigma_rules import UnsupportedSigmaRule, import_rule

PATH = 'rules/windows/powershell/powershell_script/example.yml'


def candidate(selection):
    return {'id': 'fixture', 'title': 'fixture', 'author': 'fixture',
            'logsource': {'category': 'ps_script', 'product': 'windows'},
            'detection': {'selection': selection, 'condition': 'selection'}}


@pytest.mark.parametrize('selection', [
    {'CommandLine|contains': 'alpha', 'Image|unsupported': 'beta'},
    [{'CommandLine|contains': 'alpha'}, {'Image|unsupported': 'beta'}],
    {'CommandLine|contains': ['alpha', {'unsupported': 'nested'}]},
    {'CommandLine|contains|all': []},
    {'CommandLine|unsupported': []},
    {'CommandLine|contains': []},
    {'CommandLine|contains|all': 'alpha'},
    {'CommandLine|contains|contains': 'alpha'},
    {'CommandLine|contains|endswith': 'alpha'},
    {'CommandLine|re': '['},
    {'CommandLine|re|windash': 'alpha'},
    {'CommandLine|re|all': ['alpha']},
    {'|contains': 'alpha'},
    {'CommandLine|': 'alpha'},
    {'CommandLine': None}, {'CommandLine': True}, {'CommandLine': 1},
    {'CommandLine': {}}, {}, [], ['alpha'],
])
def test_import_rejects_every_invalid_field_and_alternative(selection):
    with pytest.raises(UnsupportedSigmaRule):
        import_rule(candidate(selection), PATH)


@pytest.mark.parametrize('selection', [
    {'ScriptBlockText|contains': 'alpha', 'Image|endswith': r'\pwsh.exe'},
    [{'ScriptBlockText|contains': 'alpha'}, {'Image|endswith': r'\pwsh.exe'}],
    {'ScriptBlockText|contains|all': ['alpha', 'beta']},
])
def test_valid_structures_have_positive_benign_and_missing_controls(selection):
    rule = import_rule(candidate(selection), PATH)
    assert rule.matches({'ScriptBlockText': 'alpha beta', 'Image': r'C:\pwsh.exe'})
    assert not rule.matches({'ScriptBlockText': 'gamma', 'Image': r'C:\notepad.exe'})
    assert not rule.matches({})


@pytest.mark.parametrize('event', [None, [], 'alpha', 42, True])
def test_malformed_event_raises_controlled_error(event):
    rule = import_rule(candidate({'ScriptBlockText|contains': 'alpha'}), PATH)
    with pytest.raises(UnsupportedSigmaRule, match='mapping'):
        rule.matches(event)


@pytest.mark.parametrize('name', ['timeframe', 42, None, ''])
def test_invalid_detection_keys_rejected(name):
    data = candidate({'ScriptBlockText|contains': 'alpha'})
    data['detection'][name] = '1m'
    with pytest.raises(UnsupportedSigmaRule):
        import_rule(data, PATH)


@pytest.mark.parametrize('path', [None, 42, 'rules/windows/../test.yml',
                                 'rules/windows/test.yml?raw=1', 'rules//test.yml'])
def test_invalid_source_path_types_and_components(path):
    with pytest.raises(UnsupportedSigmaRule):
        import_rule(candidate({'ScriptBlockText': 'alpha'}), path)


@pytest.mark.parametrize('revision', ['', '../main', 42, 'a'*39])
def test_invalid_source_revision(revision):
    with pytest.raises(UnsupportedSigmaRule):
        import_rule(candidate({'ScriptBlockText': 'alpha'}), PATH, source_revision=revision)


def test_import_copies_caller_detection_before_later_mutation():
    data = candidate({'ScriptBlockText|contains': ['alpha']})
    original = deepcopy(data)
    rule = import_rule(data, PATH)
    data['detection']['selection']['ScriptBlockText|contains'].append('benign')
    assert rule.matches({'ScriptBlockText': 'alpha'})
    assert not rule.matches({'ScriptBlockText': 'benign'})
    assert rule.detection == original['detection']


def test_nested_alternatives_are_bounded():
    selection = {'ScriptBlockText': 'alpha'}
    for _ in range(18):
        selection = [selection]
    with pytest.raises(UnsupportedSigmaRule, match='nested'):
        import_rule(candidate(selection), PATH)


def test_declared_non_drl_license_is_not_silently_overwritten():
    data = candidate({'ScriptBlockText': 'alpha'})
    data['license'] = 'some-other-license'
    with pytest.raises(UnsupportedSigmaRule, match='license'):
        import_rule(data, PATH)


@pytest.mark.parametrize('field,value', [
    ('ScriptBlockText|contains', 'Get-*'),
    ('ScriptBlockText|startswith', 'Get-?'),
    ('Image|endswith', r'\*.exe'),
    ('Image', r'C:\*\tool.exe'),
    ('CommandLine|contains|windash', '-encodedcommand'),
])
def test_incomplete_wildcard_and_windash_semantics_are_rejected(field, value):
    with pytest.raises(UnsupportedSigmaRule):
        import_rule(candidate({field: value}), PATH)


def test_sigma_regex_is_case_sensitive_unless_flags_are_explicit():
    rule = import_rule(candidate({'ScriptBlockText|re': 'Get-Process'}), PATH)
    assert rule.matches({'ScriptBlockText': 'Get-Process'})
    assert not rule.matches({'ScriptBlockText': 'get-process'})
    insensitive = import_rule(candidate({'ScriptBlockText|re': '(?i)Get-Process'}), PATH)
    assert insensitive.matches({'ScriptBlockText': 'get-process'})


@pytest.mark.parametrize('selection', [
    {'CommandLine|contains': 'alpha', 'Image|unsupported': 'beta'},
    [{'CommandLine': 'alpha'}, {'Image|unsupported': 'beta'}],
    {'CommandLine|contains|all': []},
])
def test_direct_selection_api_validates_before_short_circuiting(selection):
    from guardoath.sigma_rules.matcher import match_selection
    with pytest.raises(UnsupportedSigmaRule):
        match_selection(selection, {})


def test_selection_names_beginning_with_timeframe_are_not_silently_removed():
    data = candidate({'ScriptBlockText': 'alpha'})
    data['detection'] = {'timeframe_filter': {'ScriptBlockText': 'alpha'},
                         'condition': 'timeframe_filter'}
    assert import_rule(data, PATH).matches({'ScriptBlockText': 'alpha'})
