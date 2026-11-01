"""Batch 004: compound fields, exclusions and documented upstream overlaps."""
import hashlib
import json
from pathlib import Path
import re

import pytest

from guardoath.sigma_rules import UnsupportedSigmaRule, import_rule

ROOT = Path(__file__).resolve().parents[1] / 'guardoath' / 'sigma_rules'
DATA = json.loads((ROOT / 'upstream_batch_004.json').read_text(encoding='utf-8'))
RECORDS = DATA['rules']
ALL_RECORDS = [r for p in [ROOT / f'upstream_batch_{n:03d}.json' for n in range(1, 5)]
               for r in json.loads(p.read_text(encoding='utf-8'))['rules']]
IMPORTED = [import_rule(r, r['source_path']) for r in ALL_RECORDS]
BY_NAME = {Path(r['source_path']).stem.removeprefix('posh_ps_'): r for r in RECORDS}


def test_inventory_is_25_additional_distinct_rules():
    assert len(RECORDS) == 25
    assert len(ALL_RECORDS) == len({r['id'] for r in ALL_RECORDS}) == 78
    assert len({r['source_path'] for r in ALL_RECORDS}) == 78
    assert DATA['source_revision'] == '05d1031f537e830b76da53bae2400c5814c89165'


@pytest.mark.parametrize('record', RECORDS, ids=lambda r: r['id'])
def test_original_yaml_hashes_and_pinned_finding_attribution(record):
    raw = (ROOT / 'upstream/batch_004' / Path(record['source_path']).name).read_text(encoding='utf-8').encode()
    assert hashlib.sha256(raw).hexdigest() == record['source_sha256']
    assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == record['source_git_blob']
    rule = import_rule(record, record['source_path'], source_revision=DATA['source_revision'])
    assert rule.finding() == {
        'rule_id': record['id'], 'title': record['title'], 'author': record['author'],
        'license': DATA['license'],
        'source_url': f"https://github.com/SigmaHQ/sigma/blob/{DATA['source_revision']}/{record['source_path']}",
    }


@pytest.mark.parametrize('record', RECORDS, ids=lambda r: r['id'])
def test_independent_positive_benign_and_missing_event_fields(record):
    rule = import_rule(record, record['source_path'])
    assert rule.matches(record['positive_event'])
    assert rule.matches({'ScriptBlockText': record['positive_event']['ScriptBlockText'].swapcase()})
    assert not rule.matches(record['negative_event'])
    assert not rule.matches({})
    assert not rule.matches({'CommandLine': record['positive_event']['ScriptBlockText']})
    assert not rule.matches({'scriptblocktext': record['positive_event']['ScriptBlockText']})


@pytest.mark.parametrize('record', RECORDS, ids=lambda r: r['id'])
@pytest.mark.parametrize('value', [None, False, 42, [], {}, ['Register-ScheduledTask']])
def test_malformed_event_field_types_are_not_matches(record, value):
    assert not import_rule(record, record['source_path']).matches({'ScriptBlockText': value})


@pytest.mark.parametrize('record', RECORDS, ids=lambda r: r['id'])
@pytest.mark.parametrize('event', [None, [], 'Register-ScheduledTask', 42, False])
def test_malformed_event_containers_are_explicit_errors(record, event):
    with pytest.raises(UnsupportedSigmaRule, match='mapping'):
        import_rule(record, record['source_path']).matches(event)


@pytest.mark.parametrize('record', ALL_RECORDS, ids=lambda r: r['id'])
def test_positive_fixture_cross_rule_matrix(record):
    expected = {record['id']} | POSITIVE_OVERLAPS.get(record['id'], set())
    assert {r.rule_id for r in IMPORTED if r.matches(record['positive_event'])} == expected


@pytest.mark.parametrize('record', ALL_RECORDS, ids=lambda r: r['id'])
def test_benign_fixture_cross_rule_matrix(record):
    assert {r.rule_id for r in IMPORTED if r.matches(record['negative_event'])} == NEGATIVE_OVERLAPS.get(record['id'], set())


def required_terms(selection):
    if isinstance(selection, list):
        for item in selection:
            yield from required_terms(item)
    elif isinstance(selection, dict):
        for key, values in selection.items():
            if key == 'ScriptBlockText|contains|all':
                yield from values


REQUIRED_TERMS = [(r, term) for r in RECORDS for selection in r['detection'].values()
                  for term in required_terms(selection)
                  if term.casefold() in r['positive_event']['ScriptBlockText'].casefold()]


@pytest.mark.parametrize('record,term', REQUIRED_TERMS,
                         ids=[f"{r['id']}:{i}" for i, (r, _) in enumerate(REQUIRED_TERMS)])
def test_removing_required_conjunction_term_breaks_match(record, term):
    positive = record['positive_event']['ScriptBlockText']
    mutated = re.sub(re.escape(term), '<removed>', positive, flags=re.IGNORECASE)
    assert mutated != positive
    assert not import_rule(record, record['source_path']).matches({'ScriptBlockText': mutated})


# These are upstream semantic overlaps, not duplicate-import suppression.
POSITIVE_OVERLAPS = {
    '060c3ef1-fd0a-4091-bf46-e7d625f60b73': {'846c7a87-8e14-4569-9d49-ecfd4276a01c'},
    'e6cb92b4-b470-4eb8-8a9d-d63e8583aae0': {'1f49f2ab-26bc-48b3-96cc-dcffbc93eadf'},
    'a5a30a6e-75ca-4233-8b8c-42e0f2037d3b': {'1f49f2ab-26bc-48b3-96cc-dcffbc93eadf'},
}
NEGATIVE_OVERLAPS = {
    'e6cb92b4-b470-4eb8-8a9d-d63e8583aae0': {'1f49f2ab-26bc-48b3-96cc-dcffbc93eadf'},
}
# A benign control for one rule is not necessarily globally benign: rundll32
# remains a generic suspicious-keyword signal even without the obfuscation chain.
BRANCHES = [
    ('dotnet_assembly_from_file', {'ScriptBlockText': '[Reflection.Assembly]::LoadFile("lab") SQLSERVER: { Set-Location',
     'Path': r'C:\Program Files\Microsoft SQL Server\Lab\Tools\PowerShell\Modules\SQLPS\SqlPsPostScript.ps1'}, False),
    ('dotnet_assembly_from_file', {'ScriptBlockText': '[Reflection.Assembly]::LoadFile("lab") SQLSERVER: { Set-Location',
     'Path': r'C:\lab\SqlPsPostScript.ps1'}, True),
    ('get_adcomputer', {'ScriptBlockText': 'Get-ADComputer -LDAPFilter (name=lab)'}, True),
    ('get_adcomputer', {'ScriptBlockText': 'Get-ADComputer -Filter name'}, True),
    ('hktl_defendnot_installation', {'ScriptBlockText': 'https://github.com/lab/defendnot'}, True),
    ('hktl_defendnot_installation', {'ScriptBlockText': 'https://lab.example/defendnot'}, False),
    ('install_unsigned_appx_packages', {'ScriptBlockText': 'Add-AppPackage lab.appx -AllowUnsigned'}, True),
    ('invoke_dnsexfiltration', {'ScriptBlockText': 'dns.exe -i lab -d lab -p lab -doh lab -t lab '}, True),
    ('invoke_dnsexfiltration', {'ScriptBlockText': 'dns.exe -i lab -d lab -p lab -t lab '}, False),
    ('invoke_obfuscation_via_compress', {'ScriptBlockText': 'new-object system.io.streamreader [text.encoding]::ascii readtoend'}, True),
    ('invoke_obfuscation_via_compress', {'ScriptBlockText': 'new-object system.io.streamreader [text.encoding]::ascii readtoend;'}, False),
    ('keylogging', {'ScriptBlockText': 'Get-ProcAddress user32.dll GetAsyncKeyState; Get-ProcAddress user32.dll GetForegroundWindow'}, True),
    ('keylogging', {'ScriptBlockText': 'Get-ProcAddress user32.dll GetForegroundWindow'}, False),
    ('ntfs_ads_access', {'ScriptBlockText': 'Add-Content lab.txt -Stream hidden'}, True),
]


@pytest.mark.parametrize('name,event,expected', BRANCHES)
def test_upstream_alternative_and_filter_branches(name, event, expected):
    record = BY_NAME[name]
    assert import_rule(record, record['source_path']).matches(event) is expected


@pytest.mark.parametrize('record', RECORDS, ids=lambda r: r['id'])
def test_invalid_unreferenced_selection_is_rejected_for_each_new_rule(record):
    from copy import deepcopy
    malformed = deepcopy(record)
    malformed['detection']['unused_invalid'] = [
        {'ScriptBlockText': 'not present'},
        {'ScriptBlockText|contains': 'not present', 'Image|unsupported': 'x'},
    ]
    with pytest.raises(UnsupportedSigmaRule):
        import_rule(malformed, malformed['source_path'])
