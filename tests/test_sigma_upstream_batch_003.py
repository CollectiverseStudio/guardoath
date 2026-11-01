"""Batch 003: conjunctions, alternatives, exclusions and cross-batch behavior."""
import hashlib
import json
from pathlib import Path
import re

import pytest

from guardoath.sigma_rules import UnsupportedSigmaRule, import_rule

ROOT = Path(__file__).resolve().parents[1] / 'guardoath' / 'sigma_rules'
DATA = json.loads((ROOT / 'upstream_batch_003.json').read_text(encoding='utf-8'))
RECORDS = DATA['rules']
# Keep this historical batch contract fixed; batch 004 tests the expanded matrix.
ALL_RECORDS = [r for p in [ROOT / f'upstream_batch_{n:03d}.json' for n in range(1, 4)]
               for r in json.loads(p.read_text(encoding='utf-8'))['rules']]
IMPORTED = [import_rule(r, r['source_path']) for r in ALL_RECORDS]
BY_NAME = {Path(r['source_path']).stem.removeprefix('posh_ps_'): r for r in RECORDS}


def test_inventory_is_25_additional_distinct_rules():
    assert len(RECORDS) == 25
    assert len(ALL_RECORDS) == len({r['id'] for r in ALL_RECORDS}) == 53
    assert len({r['source_path'] for r in ALL_RECORDS}) == 53
    assert DATA['source_revision'] == '05d1031f537e830b76da53bae2400c5814c89165'


@pytest.mark.parametrize('record', RECORDS, ids=lambda r: r['id'])
def test_original_yaml_hashes_and_pinned_finding_attribution(record):
    raw = (ROOT / 'upstream/batch_003' / Path(record['source_path']).name).read_text(encoding='utf-8').encode()
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
    assert [r.rule_id for r in IMPORTED if r.matches(record['positive_event'])] == [record['id']]


@pytest.mark.parametrize('record', ALL_RECORDS, ids=lambda r: r['id'])
def test_benign_fixture_cross_rule_matrix(record):
    assert [r.rule_id for r in IMPORTED if r.matches(record['negative_event'])] == []


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


BRANCHES = [
    ('active_directory_module_dll_import', 'ipmo Microsoft.ActiveDirectory.Management.dll', True),
    ('adsi_create_user', "[ADSI]'LDAP://lab'.create('user', 'lab')", True),
    ('access_to_browser_login_data', r'Copy-Item C:\Mozilla\Firefox\Profiles -Destination C:\lab', True),
    ('clear_powershell_history', 'Set-PSReadlineOption –HistorySaveStyle SaveNothing', True),
    ('clearing_windows_console_history', 'rm ConsoleHost_history.txt', True),
    ('clearing_windows_console_history', 'rm lab.tmp', False),
    ('cmdlet_scheduled_task', r'Invoke-CimMethod -ClassName PS_ScheduledTask -NameSpace Root\Microsoft\Windows\TaskScheduler', True),
    ('cmdlet_scheduled_task', r'Register-ScheduledTask Microsoft.PowerShell.Core\Export-ModuleMember Microsoft.Management.Infrastructure.CimInstance __cmdletization_methodParameter', False),
    ('cmdlet_scheduled_task', r'Register-ScheduledTask Microsoft.PowerShell.Core\Export-ModuleMember', True),
    ('dump_password_windows_credential_manager', 'New-Object Windows.Security.Credentials.PasswordVault', True),
    ('dump_password_windows_credential_manager', 'Get-CredManCreds', True),
    ('dump_password_windows_credential_manager', 'New-Object Microsoft.CSharp.CSharpCodeProvider [System.Runtime.InteropServices.RuntimeEnvironment]::GetRuntimeDirectory()) Collections.ArrayList System.CodeDom.Compiler.CompilerParameters', True),
    ('detect_vm_env', 'gwmi MSAcpi_ThermalZoneTemperature', True),
    ('etw_trace_evasion', 'Set-EtwTraceProvider -Level 0x11', True),
    ('etw_trace_evasion', 'Set-EtwTraceProvider -Level 17', True),
    ('etw_trace_evasion', "Set-EtwTraceProvider -Level “'17'”", True),
    ('etw_trace_evasion', 'Set-EtwTraceProvider -Level ”17”', True),
    ('etw_trace_evasion', 'Set-EtwTraceProvider -Level 16', False),
    ('export_certificate', 'Export-Certificate -FilePath C:\\lab.cer', True),
    ('export_certificate', 'Export-Certificate CmdletsToExport = @(', False),
    ('export_certificate', 'CmdletsToExport = @(', False),
]


@pytest.mark.parametrize('name,text,expected', BRANCHES)
def test_upstream_alternative_and_filter_branches(name, text, expected):
    record = BY_NAME[name]
    assert import_rule(record, record['source_path']).matches({'ScriptBlockText': text}) is expected


def test_upstream_history_overlap_is_retained_rather_than_suppressed():
    event = {'ScriptBlockText': 'Remove-Item (Get-PSReadlineOption).HistorySavePath'}
    expected = {BY_NAME[name]['id'] for name in ('clear_powershell_history', 'clearing_windows_console_history')}
    assert {rule.rule_id for rule in IMPORTED if rule.matches(event)} == expected
