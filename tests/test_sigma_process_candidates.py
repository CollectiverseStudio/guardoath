"""Staged originals, not a default inventory or per-event budget expansion."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from guardoath.sigma_rules import LICENSE, UnsupportedSigmaRule, import_rule
from guardoath.sigma_rules.evidence_catalog import BATCH_NAMES, build_evidence_catalog, load_evidence_catalog
from guardoath.sigma_rules.events import evaluate_event
from guardoath.sigma_rules.routing import RoutingTable, load_routing_table

ROOT = Path(__file__).resolve().parents[1] / 'guardoath/sigma_rules'
DATA = json.loads((ROOT / 'candidates/batch_007.json').read_text(encoding='utf-8'))
ROWS = DATA['rules']
DEFAULT_BATCHES = [json.loads((ROOT / name).read_text(encoding='utf-8')) for name in BATCH_NAMES]
DEFAULT_ROWS = [r for b in DEFAULT_BATCHES for r in b['rules']]
ALL_BATCHES = DEFAULT_BATCHES + [DATA]
RULES = [import_rule(r, r['source_path'], source_revision=b['source_revision']) for b in ALL_BATCHES for r in b['rules']]
NEW = RULES[len(DEFAULT_ROWS):]
TABLE = RoutingTable(ALL_BATCHES)
DEFAULT_TABLE = load_routing_table()
PROCESS = {'product': 'windows', 'category': 'process_creation'}
SCRIPT = {'product': 'windows', 'category': 'ps_script'}
NAMES = {Path(r['source_path']).stem.removeprefix('proc_creation_win_'): r for r in ROWS}
OVERLAPS = {
    'aspnet_compiler_susp_paths': 'aspnet_compiler_exectuion',
    'browsers_chromium_headless_debugging': 'browsers_chromium_headless_exec',
    'browsers_chromium_susp_load_extension': 'browsers_chromium_load_extension',
}


def expected_ids(row, which):
    expected = {row['id']} if which == 'positive' else set()
    name = Path(row['source_path']).stem.removeprefix('proc_creation_win_')
    if name in OVERLAPS:
        expected.add(NAMES[OVERLAPS[name]]['id'])
    return expected


def test_staged_inventory_is_distinct_and_default_release_stays_128():
    assert DATA['integration_status'] == 'staged-not-default'
    assert DATA['default_inventory_size_at_review'] == len(DEFAULT_ROWS) == 128
    assert len(ROWS) == len({r['id'] for r in ROWS}) == len({r['source_path'] for r in ROWS}) == 25
    assert not {r['id'] for r in ROWS} & {r['id'] for r in DEFAULT_ROWS}
    assert len(RULES) == len(build_evidence_catalog(ALL_BATCHES)) == 153
    assert len(DEFAULT_TABLE.rule_ids) == len(load_evidence_catalog()) == 128
    assert all(e.source_revision is not None for e in load_evidence_catalog())
    assert DATA['license'] == LICENSE
    assert DATA['source_revision'] == '05d1031f537e830b76da53bae2400c5814c89165'


@pytest.mark.parametrize('row', ROWS, ids=lambda r: r['id'])
def test_original_git_text_metadata_detection_and_attribution_are_exact(row):
    raw = (ROOT / 'candidates/batch_007' / Path(row['source_path']).name).read_text(encoding='utf-8').encode()
    assert hashlib.sha256(raw).hexdigest() == row['source_sha256']
    assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == row['source_git_blob']
    original = json.loads(json.dumps(yaml.safe_load(raw), default=str))
    extras = {'source_path', 'source_sha256', 'source_git_blob', 'positive_event', 'negative_event'}
    assert {k: v for k, v in row.items() if k not in extras} == original
    rule = import_rule(row, row['source_path'], source_revision=DATA['source_revision'])
    assert rule.finding() == {'rule_id': row['id'], 'title': row['title'], 'author': row['author'],
        'license': LICENSE, 'source_url': f"https://github.com/SigmaHQ/sigma/blob/{DATA['source_revision']}/{row['source_path']}"}
    assert TABLE.source_for(row['id']) == row['logsource']
    assert row['logsource']['category'] == 'process_creation' and row['logsource']['product'] == 'windows'


@pytest.mark.parametrize('row', ROWS, ids=lambda r: r['id'])
def test_candidate_positive_lookalike_case_and_empty_events(row):
    rule = import_rule(row, row['source_path'])
    assert rule.matches(row['positive_event'])
    assert not rule.matches(row['negative_event'])
    assert rule.matches({key: value.swapcase() for key, value in row['positive_event'].items()})
    assert not rule.matches({})
    assert not rule.matches({'ScriptBlockText': ' '.join(row['positive_event'].values())})
    assert not rule.matches({key.lower(): value for key, value in row['positive_event'].items()})


@pytest.mark.parametrize('row', ROWS, ids=lambda r: r['id'])
@pytest.mark.parametrize('value', [None, False, 7, [], {}, ['C:\\Lab\\cmd.exe']])
def test_malformed_process_field_values_never_match(row, value):
    rule = import_rule(row, row['source_path'])
    assert not rule.matches({key: value for key in row['positive_event']})


@pytest.mark.parametrize('row', ROWS, ids=lambda r: r['id'])
@pytest.mark.parametrize('event', [None, [], 'cmd.exe', 1, False])
def test_malformed_event_container_is_explicit_error(row, event):
    with pytest.raises(UnsupportedSigmaRule, match='mapping'):
        import_rule(row, row['source_path']).matches(event)


@pytest.mark.parametrize('row', ROWS, ids=lambda r: r['id'])
@pytest.mark.parametrize('which', ['positive', 'negative'])
def test_complete_153_definition_overlap_review_and_28_rule_process_route(row, which):
    event = row[which + '_event']
    expected = expected_ids(row, which)
    assert {rule.rule_id for rule in RULES if rule.matches(event)} == expected
    findings = TABLE.evaluate(event, trusted_logsource=PROCESS, allowed_rule_ids=TABLE.rule_ids)
    assert {item['rule_id'] for item in findings} == expected
    assert all(item == next(rule.finding() for rule in RULES if rule.rule_id == item['rule_id']) for item in findings)
    assert TABLE.evaluate(event, trusted_logsource=SCRIPT, allowed_rule_ids=TABLE.rule_ids) == []


@pytest.mark.parametrize('row', DEFAULT_ROWS, ids=lambda r: r['id'])
@pytest.mark.parametrize('which', ['positive', 'negative'])
def test_all_default_fixtures_have_no_unreviewed_candidate_overlap(row, which):
    assert {rule.rule_id for rule in NEW if rule.matches(row[which + '_event'])} == set()


@pytest.mark.parametrize('row', ROWS, ids=lambda r: r['id'])
def test_default_route_cannot_select_unpublished_candidate_id(row):
    with pytest.raises(UnsupportedSigmaRule, match='unknown'):
        DEFAULT_TABLE.plan(trusted_logsource=PROCESS, allowed_rule_ids=[row['id']])


def test_153_inventory_requires_source_routing_and_never_expands_event_cap(monkeypatch):
    process = TABLE.plan(trusted_logsource=PROCESS, allowed_rule_ids=TABLE.rule_ids)
    script = TABLE.plan(trusted_logsource=SCRIPT, allowed_rule_ids=TABLE.rule_ids)
    assert len(process.selected_rule_ids) == 28 and len(process.excluded_rule_ids) == 125
    assert len(script.selected_rule_ids) == 125 and len(script.excluded_rule_ids) == 28
    assert set(process.selected_rule_ids) | set(script.selected_rule_ids) == set(TABLE.rule_ids)
    assert not set(process.selected_rule_ids) & set(script.selected_rule_ids)
    with pytest.raises(UnsupportedSigmaRule, match='128'):
        evaluate_event(RULES, {})
    from guardoath.sigma_rules import routing, budgets
    real = routing.evaluate_event
    calls = []
    def spy(rules, event, **kwargs):
        assert budgets._CURRENT.get() is not None
        calls.append(tuple(rule.rule_id for rule in rules))
        return real(rules, event, **kwargs)
    monkeypatch.setattr(routing, 'evaluate_event', spy)
    TABLE.evaluate({}, trusted_logsource=PROCESS, allowed_rule_ids=TABLE.rule_ids)
    assert calls == [process.selected_rule_ids]
    TABLE.evaluate({}, trusted_logsource=SCRIPT, allowed_rule_ids=TABLE.rule_ids)
    assert calls == [process.selected_rule_ids, script.selected_rule_ids]


def fields(selection):
    if isinstance(selection, list):
        return {name for item in selection for name in fields(item)}
    return {name.split('|')[0] for name in selection}


REQUIRED_FIELDS = [(row, key) for row in ROWS for key in sorted(fields(row['detection']['selection']) & row['positive_event'].keys())]


@pytest.mark.parametrize('row,key', REQUIRED_FIELDS)
def test_removing_required_present_field_breaks_each_authored_positive(row, key):
    event = deepcopy(row['positive_event'])
    del event[key]
    assert not import_rule(row, row['source_path']).matches(event)


@pytest.mark.parametrize('row', ROWS, ids=lambda r: r['id'])
@pytest.mark.parametrize('modifier', ['windash', 'base64', 'expand'])
def test_unsupported_dead_branch_is_not_approximated(row, modifier):
    broken = deepcopy(row)
    broken['detection']['unused_invalid'] = {'CommandLine|' + modifier: 'x'}
    with pytest.raises(UnsupportedSigmaRule):
        import_rule(broken, broken['source_path'])
