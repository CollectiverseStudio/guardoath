"""Aggregate event deadlines and launch limits never produce clean partial work."""
import json
from pathlib import Path
import subprocess

import pytest

from guardoath.sigma_rules import (
    SigmaBudgetExceeded, SigmaEvaluationError, UnsupportedSigmaRule, import_rule,
)
from guardoath.sigma_rules import bounded_regex, budgets
from guardoath.sigma_rules.events import evaluate_event
from guardoath.sigma_rules.matcher import match_detection, match_selection, match_value

PATH = 'rules/windows/powershell/powershell_script/test.yml'


def rule(detection=None, identifier='fixture'):
    return import_rule({'id': identifier, 'title': 'fixture', 'author': 'fixture',
        'logsource': {'category': 'ps_script', 'product': 'windows'},
        'detection': detection or {'selection': {'ScriptBlockText|re': 'alpha'}, 'condition': 'selection'}}, PATH)


def fake_worker(monkeypatch, *, seconds=0, answer='0', error=None):
    clock = [0.0]
    calls = []
    monkeypatch.setattr(budgets, 'monotonic', lambda: clock[0])
    def run(args, **kwargs):
        calls.append(kwargs)
        clock[0] += seconds
        if error:
            raise OSError('private worker startup detail')
        return subprocess.CompletedProcess(args, 0, answer)
    monkeypatch.setattr(bounded_regex.subprocess, 'run', run)
    return clock, calls


def test_multiple_rules_share_worker_cap_and_do_not_reset_it(monkeypatch):
    _, calls = fake_worker(monkeypatch)
    rules = [rule(identifier=str(i)) for i in range(3)]
    with pytest.raises(SigmaBudgetExceeded, match='worker budget'):
        evaluate_event(rules, {'ScriptBlockText': 'alpha'}, max_workers=2)
    assert len(calls) == 2


@pytest.mark.parametrize('api', ['value', 'selection', 'detection'])
def test_each_public_matcher_surface_shares_budget_across_regex_alternatives(api, monkeypatch):
    _, calls = fake_worker(monkeypatch)
    values = ['alpha', 'beta', 'gamma', 'delta', 'epsilon']
    with pytest.raises(SigmaBudgetExceeded, match='worker budget'):
        if api == 'value':
            match_value('safe', values, ('re',))
        elif api == 'selection':
            match_selection({'ScriptBlockText|re': values}, {'ScriptBlockText': 'safe'})
        else:
            match_detection({'selection': {'ScriptBlockText|re': values}, 'condition': 'selection'}, {'ScriptBlockText': 'safe'})
    assert len(calls) == 4


def test_multiple_selections_share_remaining_time(monkeypatch):
    _, calls = fake_worker(monkeypatch, seconds=0.4)
    r = rule({'a': {'ScriptBlockText|re': 'alpha'}, 'b': {'ScriptBlockText|re': 'beta'}, 'condition': 'a or b'})
    with pytest.raises(SigmaBudgetExceeded, match='deadline'):
        evaluate_event([r], {'ScriptBlockText': 'safe'}, timeout_seconds=0.5)
    assert len(calls) == 2
    assert calls[0]['timeout'] == pytest.approx(0.5)
    assert calls[1]['timeout'] == pytest.approx(0.1)


def test_startup_overhead_that_exceeds_deadline_is_not_a_clean_result(monkeypatch):
    _, calls = fake_worker(monkeypatch, seconds=1.5, answer='1')
    with pytest.raises(SigmaBudgetExceeded, match='deadline'):
        evaluate_event([rule()], {'ScriptBlockText': 'alpha'})
    assert len(calls) == 1


def test_repeated_independent_events_have_fresh_budgets(monkeypatch):
    _, calls = fake_worker(monkeypatch, seconds=0.4)
    for _ in range(3):
        assert evaluate_event([rule()], {'ScriptBlockText': 'safe'}, max_workers=1, timeout_seconds=0.5) == []
    assert len(calls) == 3


def test_nested_budget_cannot_enlarge_or_reset_outer_limit(monkeypatch):
    _, calls = fake_worker(monkeypatch)
    with budgets.evaluation_budget(max_workers=1, timeout_seconds=0.25):
        assert bounded_regex.bounded_search('a', 'safe') is False
        with budgets.evaluation_budget(max_workers=16, timeout_seconds=5):
            with pytest.raises(SigmaBudgetExceeded, match='worker budget'):
                bounded_regex.bounded_search('b', 'safe')
    assert len(calls) == 1
    assert calls[0]['timeout'] == pytest.approx(0.25)


def test_failed_worker_consumes_launch_budget_and_context_recovers(monkeypatch):
    _, calls = fake_worker(monkeypatch, error=True)
    with budgets.evaluation_budget(max_workers=1):
        with pytest.raises(SigmaEvaluationError):
            bounded_regex.bounded_search('a', 'safe')
        with pytest.raises(SigmaBudgetExceeded):
            bounded_regex.bounded_search('b', 'safe')
    assert len(calls) == 1
    fake_worker(monkeypatch, answer='1')
    assert len(evaluate_event([rule()], {'ScriptBlockText': 'alpha'}, max_workers=1)) == 1


def test_no_partial_finding_list_is_returned_on_later_failure(monkeypatch):
    fake_worker(monkeypatch, error=True)
    literal = rule({'selection': {'ScriptBlockText|contains': 'alpha'}, 'condition': 'selection'}, 'literal')
    with pytest.raises(SigmaEvaluationError):
        evaluate_event([literal, rule()], {'ScriptBlockText': 'alpha'})


@pytest.mark.parametrize('kwargs', [
    {'max_workers': 0}, {'max_workers': True}, {'max_workers': 17},
    {'timeout_seconds': 0}, {'timeout_seconds': True}, {'timeout_seconds': 6},
    {'timeout_seconds': float('nan')}, {'timeout_seconds': float('inf')},
])
def test_invalid_trusted_budget_settings_are_rejected(kwargs):
    with pytest.raises(ValueError):
        evaluate_event([rule()], {}, **kwargs)


@pytest.mark.parametrize('event', [None, [], 'alpha', {1: 'alpha'}, {'X': []},
    {'X': {}}, {'X': float('nan')}, {'X': 'a'*32001}, {str(i): 'x' for i in range(257)}])
def test_event_bounds_rejected_before_worker_launch(event, monkeypatch):
    _, calls = fake_worker(monkeypatch)
    with pytest.raises(UnsupportedSigmaRule):
        evaluate_event([rule()], event)
    assert calls == []


@pytest.mark.parametrize('rules', [[], 'rules', [None], tuple(range(129))])
def test_rule_set_shape_is_bounded(rules):
    with pytest.raises(UnsupportedSigmaRule):
        evaluate_event(rules, {})


def test_growing_inventory_requires_host_source_routing_and_keeps_real_overlap():
    root = Path(__file__).resolve().parents[1] / 'guardoath/sigma_rules'
    records = [r for path in sorted(root.glob('upstream_batch_*.json')) for r in json.loads(path.read_text(encoding='utf-8'))['rules']]
    imported = [import_rule(r, r['source_path']) for r in records]
    assert len(imported) == 203
    with pytest.raises(UnsupportedSigmaRule, match='128'):
        evaluate_event(imported, {})
    from guardoath.sigma_rules.routing import load_routing_table
    table = load_routing_table()
    args = {'trusted_logsource':{'product':'windows','category':'ps_script'}, 'allowed_rule_ids':table.rule_ids}
    assert len(table.plan(**args).selected_rule_ids) == 125
    assert table.evaluate({'ScriptBlockText': 'Write-Output lab'}, **args) == []
    findings = table.evaluate({'ScriptBlockText': 'Get-ADReplAccount -All -Server lab '}, **args)
    assert {f['rule_id'] for f in findings} == {'846c7a87-8e14-4569-9d49-ecfd4276a01c', '060c3ef1-fd0a-4091-bf46-e7d625f60b73'}
    assert all(f['author'] and f['license'] and f['source_url'] for f in findings)
