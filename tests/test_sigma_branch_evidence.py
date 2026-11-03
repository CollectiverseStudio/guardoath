"""Authored branch evidence: preserve original detections and explicit overlaps."""
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
DATA = json.loads((ROOT / 'branch_evidence.json').read_text(encoding='utf-8'))
BATCHES = [json.loads((ROOT / name).read_text(encoding='utf-8')) for name in BATCH_NAMES]
ROWS = [r for b in BATCHES for r in b['rules']]
BY_ID = {r['id']: r for r in ROWS}
RULES = [import_rule(r, r['source_path'], source_revision=b.get('source_revision')) for b in BATCHES for r in b['rules']]
TABLE = RoutingTable(BATCHES)
GAPS = DATA['gaps']
CASES = [(gap, case) for gap in GAPS for case in gap['cases']]
IDS = [gap['gap_id'] + '-' + case['case_id'] for gap, case in CASES]


def test_inventory_is_ten_gaps_in_nine_existing_rules_not_new_detectors():
    assert len(GAPS) == len({g['gap_id'] for g in GAPS}) == 10
    assert len({g['rule_id'] for g in GAPS}) == 9
    assert len(CASES) == len(IDS) == len(set(IDS)) == 30
    assert DATA['rule_inventory_size'] == 128  # Frozen C5 review scope.
    assert len(RULES) == len(BY_ID) == 203
    assert DATA['runtime_registered'] is False
    assert len(DATA['prior_evidence_files']) == 5
    for gap in GAPS:
        row = BY_ID[gap['rule_id']]
        assert row['source_path'] == gap['source_path']
        batch_index = next(i for i, b in enumerate(BATCHES) if row in b['rules'])
        assert batch_index in range(1, 6)
        assert gap['source_revision'] == BATCHES[batch_index]['source_revision']
        assert gap['source_sha256'] == row['source_sha256']
        snapshot = ROOT / 'upstream' / f'batch_{batch_index+1:03}' / Path(row['source_path']).name
        assert hashlib.sha256(snapshot.read_text(encoding='utf-8').encode()).hexdigest() == gap['source_sha256']
        assert gap['branch'] and gap['prior_evidence_gap']
        kinds = {case['kind'] for case in gap['cases']}
        assert {'positive', 'lookalike'}.issubset(kinds)
        if 'filter_' in gap['branch']:
            assert 'excluded' in kinds
        for case in gap['cases']:
            assert type(case['target_matches']) is bool
            assert len(set(case['expected_rule_ids'])) == len(case['expected_rule_ids'])
            assert set(case['expected_rule_ids']).issubset(BY_ID)
            assert (gap['rule_id'] in case['expected_rule_ids']) is case['target_matches']


@pytest.mark.parametrize('gap,case', CASES, ids=IDS)
def test_target_branch_preserves_expected_match_and_lookalike_without_mutation(gap, case):
    row = BY_ID[gap['rule_id']]
    before = deepcopy(row)
    event_before = deepcopy(case['event'])
    rule = import_rule(row, row['source_path'])
    assert rule.matches(case['event']) is case['target_matches']
    assert not rule.matches({})
    assert row == before and case['event'] == event_before


@pytest.mark.parametrize('gap,case', CASES, ids=IDS)
def test_growing_catalog_source_route_keeps_reviewed_explicit_overlaps(gap, case):
    findings = TABLE.evaluate(case['event'], trusted_logsource={'product':'windows','category':'ps_script'},
                              allowed_rule_ids=TABLE.rule_ids)
    assert {r['rule_id'] for r in findings} == set(case['expected_rule_ids'])
    assert all(r == next(rule.finding() for rule in RULES if rule.rule_id == r['rule_id']) for r in findings)


@pytest.mark.parametrize('gap,case', CASES, ids=IDS)
def test_trusted_script_route_preserves_all_signals_and_process_isolation(gap, case):
    findings = TABLE.evaluate(case['event'], trusted_logsource={'product': 'windows', 'category': 'ps_script'},
        allowed_rule_ids=TABLE.rule_ids)
    assert {r['rule_id'] for r in findings} == set(case['expected_rule_ids'])
    assert TABLE.evaluate(case['event'], trusted_logsource={'product': 'windows', 'category': 'process_creation'},
        allowed_rule_ids=TABLE.rule_ids) == []


@pytest.mark.parametrize('gap,case', CASES, ids=IDS)
def test_case_insensitivity_keeps_alternatives_and_exclusions(gap, case):
    event = {key: value.swapcase() for key, value in case['event'].items()}
    assert {r['rule_id'] for r in TABLE.evaluate(event,
        trusted_logsource={'product':'windows','category':'ps_script'},
        allowed_rule_ids=TABLE.rule_ids)} == set(case['expected_rule_ids'])


def test_full_exclusions_do_not_imply_globally_clean_events():
    generic_download = '403c2cc0-7f6b-4925-9423-bfa573bed7eb'
    generic_keyword = '1f49f2ab-26bc-48b3-96cc-dcffbc93eadf'
    chocolatey = next(g for g in GAPS if g['gap_id'] == 'SB008')
    excluded = next(c for c in chocolatey['cases'] if c['kind'] == 'excluded')
    assert excluded['expected_rule_ids'] == [generic_download]
    rundll = next(g for g in GAPS if g['gap_id'] == 'SB007')
    lookalike = next(c for c in rundll['cases'] if c['kind'] == 'lookalike')
    assert lookalike['expected_rule_ids'] == [generic_keyword]
