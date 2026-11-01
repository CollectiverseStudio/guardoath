"""Source-pinned SigmaHQ batch: authored fixtures, not effectiveness claims."""
import hashlib
import json
from pathlib import Path

import pytest

from guardoath.sigma_rules import UnsupportedSigmaRule, import_rule

ROOT = Path(__file__).resolve().parents[1] / 'guardoath' / 'sigma_rules'
DATA = json.loads((ROOT / 'upstream_batch_002.json').read_text(encoding='utf-8'))
RECORDS = DATA['rules']
PREVIOUS = json.loads((ROOT / 'upstream_batch_001.json').read_text(encoding='utf-8'))['rules']
ALL_RECORDS = PREVIOUS + RECORDS
IMPORTED = [import_rule(r, r['source_path']) for r in ALL_RECORDS]


def test_inventory_is_25_additional_distinct_upstream_ids():
    assert len(RECORDS) == 25
    assert len({r['id'] for r in ALL_RECORDS}) == 28
    assert len({r['source_path'] for r in ALL_RECORDS}) == 28
    assert DATA['source_revision'] == '05d1031f537e830b76da53bae2400c5814c89165'
    assert DATA['license'] == 'Detection Rule License 1.1 (DRL 1.1)'
    assert 'synthetic' in DATA['fixture_origin']


@pytest.mark.parametrize('record', RECORDS, ids=lambda r: r['id'])
def test_source_snapshot_and_finding_attribution(record):
    # Universal-newline decoding keeps the digest portable to Windows checkout.
    raw = (ROOT / 'upstream' / 'batch_002' / Path(record['source_path']).name).read_text(encoding='utf-8').encode()
    assert hashlib.sha256(raw).hexdigest() == record['source_sha256']
    blob = b'blob ' + str(len(raw)).encode() + b'\0' + raw
    assert hashlib.sha1(blob).hexdigest() == record['source_git_blob']
    rule = import_rule(record, record['source_path'], source_revision=DATA['source_revision'])
    finding = rule.finding()
    assert finding['author'] == record['author']
    assert finding['title'] == record['title']
    assert finding['rule_id'] == record['id']
    assert finding['license'] == DATA['license']
    assert finding['source_url'] == f"https://github.com/SigmaHQ/sigma/blob/{DATA['source_revision']}/{record['source_path']}"


@pytest.mark.parametrize('record', RECORDS, ids=lambda r: r['id'])
def test_positive_benign_and_missing_field(record):
    rule = import_rule(record, record['source_path'])
    assert rule.matches(record['positive_event'])
    assert not rule.matches(record['negative_event'])
    assert not rule.matches({})
    assert not rule.matches({'CommandLine': record['positive_event']['ScriptBlockText']})
    assert not rule.matches({'scriptblocktext': record['positive_event']['ScriptBlockText']})


ALTERNATIVES = [
    (r, value) for r in RECORDS
    for value in (r['detection']['selection']['ScriptBlockText|contains']
                  if isinstance(r['detection']['selection']['ScriptBlockText|contains'], list)
                  else [r['detection']['selection']['ScriptBlockText|contains']])
]


@pytest.mark.parametrize('record,value', ALTERNATIVES, ids=[f"{r['id']}:{i}" for i, (r, _) in enumerate(ALTERNATIVES)])
def test_each_upstream_or_alternative_and_case_insensitivity(record, value):
    rule = import_rule(record, record['source_path'])
    assert rule.matches({'ScriptBlockText': f'prefix {value} suffix'})
    assert rule.matches({'ScriptBlockText': f'prefix {value.swapcase()} suffix'})
    # Break every token; unlike escaping/normalization this must not be repaired.
    assert not rule.matches({'ScriptBlockText': '|'.join(value)})


@pytest.mark.parametrize('record', RECORDS, ids=lambda r: r['id'])
@pytest.mark.parametrize('value', [None, False, 1, [], {}, ['Get-Process']])
def test_malformed_field_values_do_not_match(record, value):
    rule = import_rule(record, record['source_path'])
    assert not rule.matches({'ScriptBlockText': value})


@pytest.mark.parametrize('record', RECORDS, ids=lambda r: r['id'])
@pytest.mark.parametrize('event', [None, [], 'Get-Process', 42, True])
def test_malformed_event_is_explicitly_rejected(record, event):
    rule = import_rule(record, record['source_path'])
    with pytest.raises(UnsupportedSigmaRule, match='mapping'):
        rule.matches(event)


@pytest.mark.parametrize('record', ALL_RECORDS, ids=lambda r: r['id'])
def test_positive_has_exactly_its_expected_match_across_both_batches(record):
    assert [r.rule_id for r in IMPORTED if r.matches(record['positive_event'])] == [record['id']]


@pytest.mark.parametrize('record', ALL_RECORDS, ids=lambda r: r['id'])
def test_benign_events_match_no_rule_across_both_batches(record):
    assert [r.rule_id for r in IMPORTED if r.matches(record['negative_event'])] == []
