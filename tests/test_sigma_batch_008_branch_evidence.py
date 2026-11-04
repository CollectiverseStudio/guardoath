"""C18: authored alternate-image evidence; no new detection definitions."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from guardoath.sigma_rules import import_rule, UnsupportedSigmaRule
from guardoath.sigma_rules.evidence_catalog import BATCH_NAMES
from guardoath.sigma_rules.routing import RoutingTable
from guardoath.sigma_rules.events import evaluate_event

ROOT = Path(__file__).resolve().parents[1] / 'guardoath/sigma_rules'
DATA = json.loads((ROOT / 'candidates/branch_008_evidence.json').read_text(encoding='utf-8'))
BATCHES = [json.loads((ROOT / n).read_text(encoding='utf-8')) for n in BATCH_NAMES]
ROWS = {r['id']: r for b in BATCHES for r in b['rules']}
RULES = [import_rule(r, r['source_path'], source_revision=b['source_revision'])
         for b in BATCHES for r in b['rules']]
TABLE = RoutingTable(BATCHES)
BASELINE = RoutingTable(BATCHES[:8])
PROCESS = {'product': 'windows', 'category': 'process_creation'}
SCRIPT = {'product': 'windows', 'category': 'ps_script'}
BRANCHES = [(g, b) for g in DATA['groups'] for b in g['branches']]
CASES = [(g, b, c) for g, b in BRANCHES for c in b['cases']]


def test_exact_uncovered_original_alternatives_and_frozen_inventory():
    assert DATA['runtime_registered'] is False
    assert DATA['reviewed_definition_count'] == 203  # Frozen C18 review.
    assert len(ROWS) == 228
    assert DATA['baseline_definition_count'] == len(BASELINE.rule_ids) == 178
    assert len(BRANCHES) == 37 and len(CASES) == 185
    assert {g['id'] for g in DATA['groups']} == {
        '67bc0e75-c0a9-4cfc-8754-84a505b63c04',
        '9f546b25-5f12-4c8d-8532-5893dcb1e4b8',
        '52cad028-0ff0-4854-8f67-d25dfcbc78b4'}
    for g in DATA['groups']:
        row = ROWS[g['id']]
        expected = {v for v in row['detection']['selection']['Image|endswith']
                    if not row['positive_event']['Image'].lower().endswith(v.lower())}
        assert {b['original_alternative'] for b in g['branches']} == expected
        assert len(g['branches']) == len(expected)
        assert len({b['branch_id'] for b in g['branches']}) == len(expected)
        assert DATA['source_revision'] == BATCHES[7]['source_revision']
        for key in ('source_path', 'source_sha256', 'source_git_blob'):
            assert g[key] == row[key]
        raw = (ROOT / 'upstream/batch_008' / Path(row['source_path']).name).read_text(encoding='utf-8').encode('utf-8')
        assert hashlib.sha256(raw).hexdigest() == g['source_sha256']
        assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == g['source_git_blob']
        for b in g['branches']:
            assert {c['case_id'] for c in b['cases']} == {
                'positive', 'image-lookalike', 'parent-lookalike', 'missing-parent', 'missing-image'}
            positive = next(c['event'] for c in b['cases'] if c['case_id'] == 'positive')
            assert positive['Image'].endswith(b['original_alternative'])


@pytest.mark.parametrize('g,b,c', CASES, ids=[g['id'][:8]+'-'+b['branch_id']+'-'+c['case_id'] for g,b,c in CASES])
def test_exact_baseline_and_current_findings_and_trusted_source(g, b, c):
    event = deepcopy(c['event'])
    row = ROWS[g['id']]
    before = deepcopy(row)
    assert import_rule(row, row['source_path']).matches(event) is (c['case_id'] == 'positive')
    assert (g['id'] in c['expected_rule_ids']) is (c['case_id'] == 'positive')
    assert len(c['expected_rule_ids']) == len(set(c['expected_rule_ids']))
    assert {r.rule_id for r in RULES if r.matches(event)} == set(c['expected_rule_ids'])
    for table in (BASELINE, TABLE):
        actual = table.evaluate(event, trusted_logsource=PROCESS, allowed_rule_ids=table.rule_ids)
        assert {f['rule_id'] for f in actual} == set(c['expected_rule_ids'])
        assert all(f == next(r.finding() for r in RULES if r.rule_id == f['rule_id']) for f in actual)
        assert table.evaluate(event, trusted_logsource=SCRIPT, allowed_rule_ids=table.rule_ids) == []
    swapped = {k: v.swapcase() for k, v in event.items()}
    assert {r.rule_id for r in RULES if r.matches(swapped)} == set(c['expected_rule_ids'])
    assert event == c['event'] and row == before


@pytest.mark.parametrize('g,b', BRANCHES, ids=[g['id'][:8]+'-'+b['branch_id'] for g,b in BRANCHES])
@pytest.mark.parametrize('field', ['ParentImage', 'Image'])
@pytest.mark.parametrize('value', [None, False, 7, [], {}, ['child.exe']])
def test_each_malformed_essential_field_fails_closed(g, b, field, value):
    event = deepcopy(next(c['event'] for c in b['cases'] if c['case_id'] == 'positive'))
    event[field] = value
    row = ROWS[g['id']]
    assert not import_rule(row, row['source_path']).matches(event)
    if isinstance(value, (list, dict)):
        with pytest.raises(UnsupportedSigmaRule):
            TABLE.evaluate(event, trusted_logsource=PROCESS, allowed_rule_ids=TABLE.rule_ids)
    else:
        assert TABLE.evaluate(event, trusted_logsource=PROCESS, allowed_rule_ids=TABLE.rule_ids) == []


def test_default_selection_caps_and_single_event_raw_limit_remain():
    assert len(TABLE.plan(trusted_logsource=PROCESS, allowed_rule_ids=TABLE.rule_ids).selected_rule_ids) == 103
    assert len(TABLE.plan(trusted_logsource=SCRIPT, allowed_rule_ids=TABLE.rule_ids).selected_rule_ids) == 125
    with pytest.raises(UnsupportedSigmaRule, match='128'):
        evaluate_event(RULES, {})
