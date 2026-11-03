"""C15 branch evidence works before or after separately reviewed C14 adoption."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from guardoath.sigma_rules import import_rule
from guardoath.sigma_rules.evidence_catalog import BATCH_NAMES
from guardoath.sigma_rules.events import evaluate_event
from guardoath.sigma_rules.routing import RoutingTable

ROOT = Path(__file__).resolve().parents[1] / 'guardoath/sigma_rules'
DATA = json.loads((ROOT / 'candidates/branch_007_evidence.json').read_text(encoding='utf-8'))
CANDIDATES = json.loads((ROOT / 'candidates/batch_007.json').read_text(encoding='utf-8'))
BATCHES = [json.loads((ROOT / name).read_text(encoding='utf-8')) for name in BATCH_NAMES]
CURRENT_ROWS = {r['id']: r for b in BATCHES for r in b['rules']}
ADDITIONAL = []
for candidate in CANDIDATES['rules']:
    if candidate['id'] in CURRENT_ROWS:
        assert CURRENT_ROWS[candidate['id']] == candidate  # Adoption must retain reviewed source and fixtures.
    else:
        ADDITIONAL.append(candidate)
if ADDITIONAL:
    batch = deepcopy(CANDIDATES)
    batch['rules'] = ADDITIONAL
    BATCHES.append(batch)
ROWS = {r['id']: r for b in BATCHES for r in b['rules']}
RULES = [import_rule(r, r['source_path'], source_revision=b['source_revision']) for b in BATCHES for r in b['rules']]
TABLE = RoutingTable(BATCHES)
GROUPS = DATA['groups']
CASES = [(g, c) for g in GROUPS for c in g['cases']]
IDS = [g['gap_id'] + '-' + c['case_id'] for g, c in CASES]
PROCESS = {'product': 'windows', 'category': 'process_creation'}
SCRIPT = {'product': 'windows', 'category': 'ps_script'}


def test_sixteen_branch_groups_are_evidence_not_extra_rules_or_default_selection():
    assert len(GROUPS) == len({g['gap_id'] for g in GROUPS}) == len({g['rule_id'] for g in GROUPS}) == 16
    assert len(CASES) == len(IDS) == len(set(IDS)) == 34
    assert DATA['runtime_registered'] is False
    assert DATA['reviewed_definition_count'] == 153  # Frozen C15 review scope.
    assert len(ROWS) == len(RULES) == 178
    assert {g['rule_id'] for g in GROUPS}.issubset({r['id'] for r in CANDIDATES['rules']})
    for group in GROUPS:
        assert {'positive', 'lookalike'}.issubset({c['case_id'] for c in group['cases']})
        for case in group['cases']:
            assert type(case['target_matches']) is bool
            assert len(set(case['expected_rule_ids'])) == len(case['expected_rule_ids'])
            assert set(case['expected_rule_ids']).issubset(ROWS)
            assert (group['rule_id'] in case['expected_rule_ids']) is case['target_matches']


@pytest.mark.parametrize('group', GROUPS, ids=lambda g: g['gap_id'])
def test_original_source_hash_provenance_and_logsource_unchanged(group):
    row = ROWS[group['rule_id']]
    assert row['source_path'] == group['source_path']
    assert row['source_sha256'] == group['source_sha256']
    assert group['source_revision'] == CANDIDATES['source_revision']
    raw = (ROOT / 'candidates/batch_007' / Path(row['source_path']).name).read_text(encoding='utf-8').encode()
    assert hashlib.sha256(raw).hexdigest() == group['source_sha256']
    assert TABLE.source_for(row['id']) == row['logsource']
    assert row['logsource']['product'] == 'windows' and row['logsource']['category'] == 'process_creation'


@pytest.mark.parametrize('group,case', CASES, ids=IDS)
def test_independent_target_cases_keep_true_and_false_without_mutation(group, case):
    row = ROWS[group['rule_id']]
    before = deepcopy(row)
    event_before = deepcopy(case['event'])
    assert import_rule(row, row['source_path']).matches(case['event']) is case['target_matches']
    assert before == row and case['event'] == event_before


@pytest.mark.parametrize('group,case', CASES, ids=IDS)
def test_complete_153_definition_overlaps_are_explicit(group, case):
    actual = {r.rule_id for r in RULES if r.matches(case['event'])}
    assert actual == set(case['expected_rule_ids'])


@pytest.mark.parametrize('group,case', CASES, ids=IDS)
def test_trusted_routes_retain_every_reviewed_process_signal_and_exclude_scripts(group, case):
    findings = TABLE.evaluate(case['event'], trusted_logsource=PROCESS, allowed_rule_ids=TABLE.rule_ids)
    assert {r['rule_id'] for r in findings} == set(case['expected_rule_ids'])
    assert all(r == next(rule.finding() for rule in RULES if rule.rule_id == r['rule_id']) for r in findings)
    assert TABLE.evaluate(case['event'], trusted_logsource=SCRIPT, allowed_rule_ids=TABLE.rule_ids) == []


@pytest.mark.parametrize('group,case', CASES, ids=IDS)
def test_case_insensitive_values_preserve_positive_and_near_miss_results(group, case):
    event = {key: value.swapcase() for key, value in case['event'].items()}
    assert {r.rule_id for r in RULES if r.matches(event)} == set(case['expected_rule_ids'])


@pytest.mark.parametrize('group', GROUPS, ids=lambda g: g['gap_id'])
@pytest.mark.parametrize('value', [None, False, 7, [], {}, ['candidate']])
def test_malformed_field_values_do_not_match_alternate_branches(group, value):
    row = ROWS[group['rule_id']]
    positive = next(c['event'] for c in group['cases'] if c['case_id'] == 'positive')
    assert not import_rule(row, row['source_path']).matches({key: value for key in positive})


def source_fields(selection):
    if isinstance(selection, list):
        return {key for branch in selection for key in source_fields(branch)}
    return {key.split('|')[0] for key in selection}


MISSING = [(group, key) for group in GROUPS
    for key in sorted(source_fields(ROWS[group['rule_id']]['detection']['selection']) &
                      next(c['event'] for c in group['cases'] if c['case_id'] == 'positive').keys())]


@pytest.mark.parametrize('group,key', MISSING)
def test_missing_required_field_does_not_turn_an_alternative_into_a_match(group, key):
    row = ROWS[group['rule_id']]
    event = deepcopy(next(c['event'] for c in group['cases'] if c['case_id'] == 'positive'))
    del event[key]
    assert not import_rule(row, row['source_path']).matches(event)


def test_headless_debugging_near_misses_retain_only_expected_broader_signals():
    group = next(g for g in GROUPS if g['gap_id'] == 'CP016')
    by_case = {c['case_id']: c for c in group['cases']}
    headless = 'ef9dcfed-690c-4c5d-a9d1-482cd422225c'
    assert by_case['lookalike']['expected_rule_ids'] == []  # No --headless.
    assert by_case['missing_profile_term']['expected_rule_ids'] == [headless]
    assert by_case['missing_remote_term']['expected_rule_ids'] == [headless]
    assert len(TABLE.plan(trusted_logsource=PROCESS, allowed_rule_ids=TABLE.rule_ids).selected_rule_ids) == 53
    assert len(TABLE.plan(trusted_logsource=SCRIPT, allowed_rule_ids=TABLE.rule_ids).selected_rule_ids) == 125
    from guardoath.sigma_rules import UnsupportedSigmaRule
    with pytest.raises(UnsupportedSigmaRule, match='128'):
        evaluate_event(RULES, {})
