"""Staged C22 originals and explicit full-inventory event contracts."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from guardoath.sigma_rules import import_rule, UnsupportedSigmaRule
from guardoath.sigma_rules.evidence_catalog import BATCH_NAMES
from guardoath.sigma_rules.routing import RoutingTable, load_routing_table
from guardoath.sigma_rules.events import evaluate_event

ROOT = Path(__file__).resolve().parents[1] / 'guardoath/sigma_rules'
DATA = json.loads((ROOT / 'candidates/batch_010.json').read_text(encoding='utf-8'))
REVIEW = json.loads((ROOT / 'candidates/batch_010_review.json').read_text(encoding='utf-8'))
BASE = [json.loads((ROOT / n).read_text(encoding='utf-8')) for n in BATCH_NAMES[:9]]
ROWS = DATA['rules']
RULES = [import_rule(r, r['source_path'], source_revision=b['source_revision'])
         for b in BASE + [DATA] for r in b['rules']]
TABLE = RoutingTable(BASE + [DATA])
BEFORE = RoutingTable(BASE)
DEFAULT = load_routing_table()
PROCESS = {'product': 'windows', 'category': 'process_creation'}
SCRIPT = {'product': 'windows', 'category': 'ps_script'}


def test_frozen_staging_review_survives_separate_default_adoption():
    assert DATA['integration_status'] == 'staged-not-default'
    assert DATA['reviewed_predecessor_count'] == REVIEW['default_inventory_at_review'] == len(BEFORE.rule_ids) == 203
    assert REVIEW['runtime_registered'] is False
    assert len(DEFAULT.rule_ids) == 228
    adopted = json.loads((ROOT / 'upstream_batch_010.json').read_text(encoding='utf-8'))
    assert adopted['rules'] == ROWS
    assert set(r['id'] for r in ROWS).issubset(DEFAULT.rule_ids)
    assert len(ROWS) == len({r['id'] for r in ROWS}) == len({r['source_path'] for r in ROWS}) == 25
    assert set(r['id'] for r in ROWS).isdisjoint(BEFORE.rule_ids)
    assert len(TABLE.rule_ids) == REVIEW['proposed_inventory'] == 228
    assert len(TABLE.plan(trusted_logsource=PROCESS, allowed_rule_ids=TABLE.rule_ids).selected_rule_ids) == 103
    assert len(TABLE.plan(trusted_logsource=SCRIPT, allowed_rule_ids=TABLE.rule_ids).selected_rule_ids) == 125
    with pytest.raises(UnsupportedSigmaRule, match='128'):
        evaluate_event(RULES, {})


@pytest.mark.parametrize('row', ROWS, ids=lambda r:r['id'])
def test_complete_upstream_snapshot_metadata_hash_and_detection(row):
    raw = (ROOT / 'candidates/batch_010' / Path(row['source_path']).name).read_text(encoding='utf-8').encode('utf-8')
    assert hashlib.sha256(raw).hexdigest() == row['source_sha256']
    assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() == row['source_git_blob']
    original = json.loads(json.dumps(yaml.safe_load(raw), default=str))
    extras = {'source_path','source_sha256','source_git_blob','positive_event','negative_event'}
    assert set(row) == set(original) | extras
    assert {k:v for k,v in row.items() if k not in extras} == original
    assert DATA['source_revision'] == REVIEW['source_revision'] == '05d1031f537e830b76da53bae2400c5814c89165'
    assert DATA['license'] == 'Detection Rule License 1.1 (DRL 1.1)'
    assert row['source_path'].startswith('rules/windows/process_creation/')
    assert row['logsource'] == {'product':'windows','category':'process_creation'}
    assert row['detection']['condition'] == 'selection'
    assert all(set(k.split('|')[1:]) <= {'contains','all','endswith','startswith'}
               for k in row['detection']['selection'])


def check_event(event, expected):
    before = deepcopy(event)
    assert {r.rule_id for r in RULES if r.matches(event)} == set(expected)
    actual = TABLE.evaluate(event, trusted_logsource=PROCESS, allowed_rule_ids=TABLE.rule_ids)
    assert {f['rule_id'] for f in actual} == set(expected)
    assert all(f == next(r.finding() for r in RULES if r.rule_id == f['rule_id']) for f in actual)
    assert TABLE.evaluate(event, trusted_logsource=SCRIPT, allowed_rule_ids=TABLE.rule_ids) == []
    assert {r.rule_id for r in RULES if r.matches({k:v.swapcase() for k,v in event.items()})} == set(expected)
    assert event == before


@pytest.mark.parametrize('row', ROWS, ids=lambda r:r['id'])
@pytest.mark.parametrize('key', ['positive_event','negative_event'])
def test_authored_positive_and_benign_whole_inventory_outcomes(row, key):
    check_event(row[key], REVIEW['candidate_outcomes'][row['id']][key])
    assert import_rule(row,row['source_path']).matches(row[key]) is (key == 'positive_event')


@pytest.mark.parametrize('case', REVIEW['additional_cases'], ids=lambda c:c['rule_id'][:8]+'-'+c['case_id'])
def test_each_original_alternative_and_all_term_near_miss(case):
    row = next(r for r in ROWS if r['id'] == case['rule_id'])
    assert import_rule(row,row['source_path']).matches(case['event']) is case['target_matches']
    check_event(case['event'], case['expected_rule_ids'])


MISSING = [(r, field) for r in ROWS for field in r['positive_event']]
@pytest.mark.parametrize('row,field', MISSING, ids=[r['id'][:8]+'-'+f for r,f in MISSING])
def test_missing_essential_field_and_executable_lookalike(row, field):
    event = deepcopy(row['positive_event']); del event[field]
    assert not import_rule(row,row['source_path']).matches(event)
    assert row['id'] not in {f['rule_id'] for f in TABLE.evaluate(event,trusted_logsource=PROCESS,allowed_rule_ids=TABLE.rule_ids)}
    if field in ('Image','ParentImage'):
        event = deepcopy(row['positive_event']);event[field] += '.bak'
        assert not import_rule(row,row['source_path']).matches(event)


@pytest.mark.parametrize('row,field', MISSING, ids=[r['id'][:8]+'-'+f for r,f in MISSING])
@pytest.mark.parametrize('value', [None,False,7,[],{},['bad']])
def test_malformed_essential_values_preserve_boundary(row,field,value):
    event = deepcopy(row['positive_event']); event[field] = value
    assert not import_rule(row,row['source_path']).matches(event)
    if isinstance(value,(list,dict)):
        with pytest.raises(UnsupportedSigmaRule):
            TABLE.evaluate(event,trusted_logsource=PROCESS,allowed_rule_ids=TABLE.rule_ids)
    else:
        assert row['id'] not in {f['rule_id'] for f in TABLE.evaluate(event,trusted_logsource=PROCESS,allowed_rule_ids=TABLE.rule_ids)}


PRIOR = [(r,key) for b in BASE for r in b['rules'] for key in ('positive_event','negative_event')]
@pytest.mark.parametrize('row,key',PRIOR,ids=[r['id'][:8]+'-'+k for r,k in PRIOR])
def test_every_predecessor_fixture_has_explicit_new_match_review(row,key):
    source = PROCESS if row['logsource']['category'] == 'process_creation' else SCRIPT
    old = BEFORE.evaluate(row[key],trusted_logsource=source,allowed_rule_ids=BEFORE.rule_ids)
    new = TABLE.evaluate(row[key],trusted_logsource=source,allowed_rule_ids=TABLE.rule_ids)
    observed = {f['rule_id'] for f in new}-{f['rule_id'] for f in old}
    assert observed == set(REVIEW['prior_fixture_new_matches'].get(row['id']+':'+key,[]))
    assert {f['rule_id'] for f in old} <= {f['rule_id'] for f in new}
