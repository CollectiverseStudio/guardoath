"""C16 candidates: immutable originals and source-routed staging, not adoption."""
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
DATA = json.loads((ROOT / 'candidates/batch_008.json').read_text(encoding='utf-8'))
ROWS = DATA['rules']
BASE_BATCHES = [json.loads((ROOT / name).read_text(encoding='utf-8')) for name in BATCH_NAMES[:7]]
CURRENT_ROWS = {r['id']: r for b in BASE_BATCHES for r in b['rules']}
PRIOR = json.loads((ROOT / 'candidates/batch_007.json').read_text(encoding='utf-8'))
remaining = []
for row in PRIOR['rules']:
    if row['id'] in CURRENT_ROWS:
        assert CURRENT_ROWS[row['id']] == row
    else:
        remaining.append(row)
if remaining:
    prior = deepcopy(PRIOR)
    prior['rules'] = remaining
    BASE_BATCHES.append(prior)
BASE_ROWS = [r for b in BASE_BATCHES for r in b['rules']]
ALL_BATCHES = BASE_BATCHES + [DATA]
RULES = [import_rule(r, r['source_path'], source_revision=b['source_revision']) for b in ALL_BATCHES for r in b['rules']]
NEW = RULES[len(BASE_ROWS):]
TABLE = RoutingTable(ALL_BATCHES)
DEFAULT = load_routing_table()
NAMES = {Path(r['source_path']).stem.removeprefix('proc_creation_win_'): r for r in ROWS}
PROCESS = {'product': 'windows', 'category': 'process_creation'}
SCRIPT = {'product': 'windows', 'category': 'ps_script'}


def test_frozen_153_predecessor_and_current_default_adoption_preserve_identities():
    assert DATA['integration_status'] == 'staged-not-default'
    assert DATA['reviewed_predecessor_count'] == len(BASE_ROWS) == 153
    assert len(ROWS) == len({r['id'] for r in ROWS}) == len({r['source_path'] for r in ROWS}) == 25
    assert not {r['id'] for r in ROWS} & {r['id'] for r in BASE_ROWS}
    assert len(RULES) == len(build_evidence_catalog(ALL_BATCHES)) == 178
    assert len(load_evidence_catalog()) == len(DEFAULT.rule_ids) == 228
    assert len(CURRENT_ROWS) == 153
    assert {r['id'] for r in ROWS}.issubset(DEFAULT.rule_ids)
    assert DATA['license'] == LICENSE
    assert DATA['source_revision'] == '05d1031f537e830b76da53bae2400c5814c89165'


@pytest.mark.parametrize('row', ROWS, ids=lambda r: r['id'])
def test_original_yaml_fields_detection_and_git_hashes_are_exact(row):
    raw = (ROOT / 'candidates/batch_008' / Path(row['source_path']).name).read_text(encoding='utf-8').encode()
    assert hashlib.sha256(raw).hexdigest() == row['source_sha256']
    assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == row['source_git_blob']
    original = json.loads(json.dumps(yaml.safe_load(raw), default=str))
    extras = {'source_path', 'source_sha256', 'source_git_blob', 'positive_event', 'negative_event'}
    assert {key: value for key, value in row.items() if key not in extras} == original
    rule = import_rule(row, row['source_path'], source_revision=DATA['source_revision'])
    assert rule.finding() == {'rule_id': row['id'], 'title': row['title'], 'author': row['author'],
        'license': LICENSE, 'source_url': f"https://github.com/SigmaHQ/sigma/blob/{DATA['source_revision']}/{row['source_path']}"}
    assert TABLE.source_for(row['id']) == row['logsource']
    assert row['logsource']['product'] == 'windows' and row['logsource']['category'] == 'process_creation'


@pytest.mark.parametrize('row', ROWS, ids=lambda r: r['id'])
def test_authored_positive_lookalike_case_and_field_names(row):
    rule = import_rule(row, row['source_path'])
    assert rule.matches(row['positive_event'])
    assert not rule.matches(row['negative_event'])
    assert rule.matches({key: value.swapcase() for key, value in row['positive_event'].items()})
    assert not rule.matches({})
    assert not rule.matches({'ScriptBlockText': ' '.join(row['positive_event'].values())})
    assert not rule.matches({key.lower(): value for key, value in row['positive_event'].items()})


@pytest.mark.parametrize('row', ROWS, ids=lambda r: r['id'])
@pytest.mark.parametrize('value', [None, False, 7, [], {}, ['process']])
def test_malformed_field_values_never_match(row, value):
    assert not import_rule(row, row['source_path']).matches({key: value for key in row['positive_event']})


@pytest.mark.parametrize('row', ROWS, ids=lambda r: r['id'])
@pytest.mark.parametrize('event', [None, [], 'process', 1, False])
def test_malformed_container_is_an_explicit_error(row, event):
    with pytest.raises(UnsupportedSigmaRule, match='mapping'):
        import_rule(row, row['source_path']).matches(event)


@pytest.mark.parametrize('row', ROWS, ids=lambda r: r['id'])
@pytest.mark.parametrize('which', ['positive', 'negative'])
def test_candidate_events_against_all_178_definitions_and_both_routes(row, which):
    expected = {row['id']} if which == 'positive' else set()
    event = row[which + '_event']
    assert {rule.rule_id for rule in RULES if rule.matches(event)} == expected
    actual = TABLE.evaluate(event, trusted_logsource=PROCESS, allowed_rule_ids=TABLE.rule_ids)
    assert {finding['rule_id'] for finding in actual} == expected
    assert all(finding == next(rule.finding() for rule in RULES if rule.rule_id == finding['rule_id']) for finding in actual)
    assert TABLE.evaluate(event, trusted_logsource=SCRIPT, allowed_rule_ids=TABLE.rule_ids) == []


@pytest.mark.parametrize('row', BASE_ROWS, ids=lambda r: r['id'])
@pytest.mark.parametrize('which', ['positive', 'negative'])
def test_all_153_predecessor_pairs_have_no_unreviewed_new_overlap(row, which):
    assert {rule.rule_id for rule in NEW if rule.matches(row[which + '_event'])} == set()


@pytest.mark.parametrize('row', ROWS, ids=lambda r: r['id'])
def test_default_loader_selects_exact_reviewed_adopted_id(row):
    plan = DEFAULT.plan(trusted_logsource=PROCESS, allowed_rule_ids=[row['id']])
    assert plan.selected_rule_ids == (row['id'],) and plan.excluded_rule_ids == ()


def test_178_review_pool_still_requires_one_source_specific_bounded_evaluation(monkeypatch):
    process = TABLE.plan(trusted_logsource=PROCESS, allowed_rule_ids=TABLE.rule_ids)
    script = TABLE.plan(trusted_logsource=SCRIPT, allowed_rule_ids=TABLE.rule_ids)
    assert len(process.selected_rule_ids) == 53 and len(process.excluded_rule_ids) == 125
    assert len(script.selected_rule_ids) == 125 and len(script.excluded_rule_ids) == 53
    assert set(process.selected_rule_ids).isdisjoint(script.selected_rule_ids)
    assert set(process.selected_rule_ids) | set(script.selected_rule_ids) == set(TABLE.rule_ids)
    with pytest.raises(UnsupportedSigmaRule, match='128'):
        evaluate_event(RULES, {})
    from guardoath.sigma_rules import routing, budgets
    real = routing.evaluate_event
    calls = []
    def spy(rules, event, **kwargs):
        assert budgets._CURRENT.get() is not None
        calls.append(tuple(r.rule_id for r in rules))
        return real(rules, event, **kwargs)
    monkeypatch.setattr(routing, 'evaluate_event', spy)
    TABLE.evaluate({}, trusted_logsource=PROCESS, allowed_rule_ids=TABLE.rule_ids)
    assert calls == [process.selected_rule_ids]
    TABLE.evaluate({}, trusted_logsource=SCRIPT, allowed_rule_ids=TABLE.rule_ids)
    assert calls == [process.selected_rule_ids, script.selected_rule_ids]


def source_fields(selection):
    if isinstance(selection, list):
        return {key for branch in selection for key in source_fields(branch)}
    return {key.split('|')[0] for key in selection}


MISSING = [(r, key) for r in ROWS for key in sorted(source_fields(r['detection']['selection']) & r['positive_event'].keys())]


@pytest.mark.parametrize('row,key', MISSING)
def test_missing_required_present_field_breaks_authored_positive(row, key):
    event = deepcopy(row['positive_event'])
    del event[key]
    assert not import_rule(row, row['source_path']).matches(event)


@pytest.mark.parametrize('row', ROWS, ids=lambda r: r['id'])
@pytest.mark.parametrize('modifier', ['windash', 'base64', 'expand'])
def test_unsupported_dead_selection_is_rejected(row, modifier):
    broken = deepcopy(row)
    broken['detection']['unused_invalid'] = {'CommandLine|' + modifier: 'x'}
    with pytest.raises(UnsupportedSigmaRule):
        import_rule(broken, broken['source_path'])


ALTERNATIVES = [
    ('dirlister_execution', {'OriginalFileName': 'DirLister.exe'}, {'OriginalFileName': 'DirLister2.exe'}),
    ('finger_execution', {'OriginalFileName': 'finger.exe'}, {'OriginalFileName': 'finder.exe'}),
    ('fsi_fsharp_code_execution', {'Image': r'C:\Lab\fsianycpu.exe'}, {'Image': r'C:\Lab\fsc.exe'}),
    ('fsi_fsharp_code_execution', {'OriginalFileName': 'fsianycpu.exe'}, {'OriginalFileName': 'fsc.exe'}),
    ('hktl_createminidump', {'Hashes': 'IMPHASH=4a07f944a83e8a7c2525efa35dd30e2f'}, {'Hashes': 'IMPHASH=' + '0'*32}),
    ('hktl_defendnot_execution', {'Hashes': 'IMPHASH=1FAAED26690A9455A795E2215661726F'}, {'Hashes': 'IMPHASH=' + '0'*32}),
    ('hktl_doppelganger', {'Hashes': 'IMPHASH=65F0EA61156EE0C2A35421926F0C7F78'}, {'Hashes': 'IMPHASH=' + '0'*32}),
    ('hktl_dumpert', {'Hashes': 'IMPHASH=09D278F9DE118EF09163C6140255C690'}, {'Hashes': 'IMPHASH=' + '0'*32}),
    ('hktl_edrsilencer', {'OriginalFileName': 'EDRSilencer.exe'}, {'OriginalFileName': 'LabSilencer.exe'}),
    ('hktl_edrsilencer', {'Description': 'Offline EDRSilencer fixture'}, {'Description': 'Offline EDR reporter'}),
    ('dns_exfiltration_tools_execution', {'Image': r'C:\Lab\dnscat2-client.exe'}, {'Image': r'C:\Lab\dns-client2.exe'}),
    ('hktl_empire_powershell_launch', {'CommandLine': 'powershell.exe -enc  SQB-OFFLINE'}, {'CommandLine': 'powershell.exe -enc SQB-OFFLINE'}),
    ('dfsvc_suspicious_child_processes', {'ParentImage': r'C:\Lab\AppData\Local\Apps\2.0\fixture.exe', 'Image': r'C:\Windows\System32\calc.exe'}, {'ParentImage': r'C:\Trusted\fixture.exe', 'Image': r'C:\Windows\System32\calc.exe'}),
    ('diskshadow_child_process_susp', {'ParentImage': r'C:\Windows\System32\diskshadow.exe', 'Image': r'C:\Windows\System32\certutil.exe'}, {'ParentImage': r'C:\Windows\System32\diskshadow.exe', 'Image': r'C:\Windows\System32\notepad.exe'}),
    ('hh_html_help_susp_child_process', {'ParentImage': r'C:\Windows\hh.exe', 'Image': r'C:\Windows\System32\mshta.exe'}, {'ParentImage': r'C:\Windows\hh.exe', 'Image': r'C:\Windows\System32\notepad.exe'}),
    ('gpresult_execution', {'Image': r'C:\Windows\System32\gpresult.exe', 'CommandLine': 'gpresult /z'}, {'Image': r'C:\Windows\System32\gpresult.exe', 'CommandLine': 'gpresult /r'}),
]


@pytest.mark.parametrize('name,positive,negative', ALTERNATIVES)
def test_metadata_hash_alias_and_literal_whitespace_branches_keep_full_reviewed_overlaps(name, positive, negative):
    row = NAMES[name]
    for event, expected in [(positive, {row['id']}), (negative, set())]:
        assert {rule.rule_id for rule in RULES if rule.matches(event)} == expected
        findings = TABLE.evaluate(event, trusted_logsource=PROCESS, allowed_rule_ids=TABLE.rule_ids)
        assert {r['rule_id'] for r in findings} == expected
        assert TABLE.evaluate(event, trusted_logsource=SCRIPT, allowed_rule_ids=TABLE.rule_ids) == []
