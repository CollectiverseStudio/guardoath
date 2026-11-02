"""Pinned batch 006 contracts; synthetic events are not effectiveness evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

import pytest
import yaml

from guardoath.sigma_rules import LICENSE, UnsupportedSigmaRule, import_rule
from guardoath.sigma_rules.evidence_catalog import build_evidence_catalog
from guardoath.sigma_rules.events import evaluate_event
from guardoath.sigma_rules.routing import RoutingTable

ROOT = Path(__file__).resolve().parents[1] / 'guardoath/sigma_rules'
BATCHES = [json.loads((ROOT / f'upstream_batch_{n:03}.json').read_text(encoding='utf-8')) for n in range(1, 7)]
DATA = BATCHES[-1]
ROWS = DATA['rules']
OLD = [r for batch in BATCHES[:-1] for r in batch['rules']]
ALL = OLD + ROWS
RULES = [import_rule(r, r['source_path'], source_revision=b.get('source_revision')) for b in BATCHES for r in b['rules']]
NEW = RULES[len(OLD):]
TABLE = RoutingTable(BATCHES)
BY_NAME = {Path(r['source_path']).stem.removeprefix('posh_ps_'): r for r in ROWS}
PROCESS_DISCOVERY = 'af4c87ce-bdda-4215-b998-15220772e993'
LOCAL_USER = '4fdc44df-bfe9-4fcc-b041-68f5a2d3031c'
LOCAL_GROUP = 'fa6a5a45-3ee2-4529-aa14-ee5edc9e29cb'
MOUNT = '29e1c216-6408-489d-8a06-ee9d151ef819'
SMB = '95f0643a-ed40-467c-806b-aac9542ec5ab'


def test_exact_inventory_and_full_six_batch_evidence():
    assert len(ROWS) == 25
    assert len(ALL) == len({r['id'] for r in ALL}) == len({r['source_path'] for r in ALL}) == 128
    assert DATA['license'] == LICENSE
    assert DATA['source_revision'] == '05d1031f537e830b76da53bae2400c5814c89165'
    catalog = build_evidence_catalog(BATCHES)
    assert len(catalog) == 128
    assert sum(r.source_revision is not None for r in catalog) == 125
    handoff = json.loads((ROOT / 'batch_006_evidence.json').read_text(encoding='utf-8'))
    assert handoff['runtime_registered'] is False
    assert handoff['rules'] == [r.as_dict() for r in build_evidence_catalog([DATA])]


@pytest.mark.parametrize('row', ROWS, ids=lambda r: r['id'])
def test_yaml_integrity_exact_semantics_and_attribution(row):
    raw = (ROOT / 'upstream/batch_006' / Path(row['source_path']).name).read_text(encoding='utf-8').encode('utf-8')
    assert hashlib.sha256(raw).hexdigest() == row['source_sha256']
    assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == row['source_git_blob']
    original = json.loads(json.dumps(yaml.safe_load(raw), default=str))
    extras = {'source_path', 'source_sha256', 'source_git_blob', 'positive_event', 'negative_event'}
    assert {k: v for k, v in row.items() if k not in extras} == original
    rule = import_rule(row, row['source_path'], source_revision=DATA['source_revision'])
    assert rule.finding() == {'rule_id': row['id'], 'title': row['title'], 'author': row['author'],
        'license': LICENSE, 'source_url': f"https://github.com/SigmaHQ/sigma/blob/{DATA['source_revision']}/{row['source_path']}"}
    assert TABLE.source_for(row['id']) == original['logsource']


@pytest.mark.parametrize('row', ROWS, ids=lambda r: r['id'])
def test_positive_lookalike_case_and_required_field(row):
    rule = import_rule(row, row['source_path'])
    assert rule.matches(row['positive_event'])
    assert rule.matches({'ScriptBlockText': row['positive_event']['ScriptBlockText'].swapcase()})
    assert not rule.matches(row['negative_event'])
    for event in [{}, {'CommandLine': row['positive_event']['ScriptBlockText']},
                  {'scriptblocktext': row['positive_event']['ScriptBlockText']}]:
        assert not rule.matches(event)


@pytest.mark.parametrize('row', ROWS, ids=lambda r: r['id'])
@pytest.mark.parametrize('value', [None, False, 7, [], {}, ['Get-Process lsass']])
def test_malformed_field_values_do_not_match(row, value):
    assert not import_rule(row, row['source_path']).matches({'ScriptBlockText': value})


@pytest.mark.parametrize('row', ROWS, ids=lambda r: r['id'])
@pytest.mark.parametrize('event', [None, [], 'Get-Process lsass', 1, False])
def test_malformed_containers_raise_explicitly(row, event):
    with pytest.raises(UnsupportedSigmaRule, match='mapping'):
        import_rule(row, row['source_path']).matches(event)


@pytest.mark.parametrize('row', ROWS, ids=lambda r: r['id'])
@pytest.mark.parametrize('which', ['positive', 'negative'])
def test_new_fixtures_against_all_128_rules_with_explicit_overlaps(row, which):
    expected = {row['id']} if which == 'positive' else set()
    # Broad existing process discovery detects both lsass and spoolsv lookups.
    if row is BY_NAME['susp_getprocess_lsass']:
        expected.add(PROCESS_DISCOVERY)
    # Look-alikes are nonmatching only for their own rule, not globally clean.
    if which == 'negative' and row is BY_NAME['susp_local_group_reco']:
        expected.add(LOCAL_USER)
    if which == 'negative' and row is BY_NAME['susp_mounted_share_deletion']:
        expected.add(SMB)
    assert {rule.rule_id for rule in RULES if rule.matches(row[which + '_event'])} == expected


@pytest.mark.parametrize('row', OLD, ids=lambda r: r['id'])
@pytest.mark.parametrize('which', ['positive', 'negative'])
def test_existing_fixtures_against_new_rules_have_only_reviewed_overlaps(row, which):
    stem = Path(row['source_path']).stem
    expected = set()
    if which == 'negative' and stem in ('posh_ps_create_local_user', 'posh_ps_localuser'):
        expected.add(LOCAL_GROUP)
    if which == 'positive' and stem == 'posh_ps_run_from_mount_diskimage':
        expected.add(MOUNT)
    assert {rule.rule_id for rule in NEW if rule.matches(row[which + '_event'])} == expected


@pytest.mark.parametrize('row', ROWS, ids=lambda r: r['id'])
def test_host_routing_preserves_single_rule_provenance_and_excludes_wrong_source(row):
    source = {'product': 'windows', 'category': 'ps_script'}
    findings = TABLE.evaluate(row['positive_event'], trusted_logsource=source, allowed_rule_ids=[row['id']])
    assert findings == [import_rule(row, row['source_path'], source_revision=DATA['source_revision']).finding()]
    assert TABLE.evaluate(row['negative_event'], trusted_logsource=source, allowed_rule_ids=[row['id']]) == []
    with pytest.raises(UnsupportedSigmaRule, match='no configured'):
        TABLE.evaluate(row['positive_event'], trusted_logsource={'product': 'windows', 'category': 'process_creation'}, allowed_rule_ids=[row['id']])


def test_real_128_rule_boundary_is_one_evaluation_without_truncation(monkeypatch):
    assert len(RULES) == 128
    row = BY_NAME['susp_getprocess_lsass']
    assert {r['rule_id'] for r in evaluate_event(RULES, row['positive_event'])} == {row['id'], PROCESS_DISCOVERY}
    with pytest.raises(UnsupportedSigmaRule, match='128'):
        evaluate_event(RULES + [RULES[0]], row['positive_event'])
    from guardoath.sigma_rules import routing, budgets
    real = routing.evaluate_event
    calls = []
    def spy(rules, event, **kwargs):
        assert budgets._CURRENT.get() is not None
        calls.append(tuple(r.rule_id for r in rules))
        return real(rules, event, **kwargs)
    monkeypatch.setattr(routing, 'evaluate_event', spy)
    TABLE.evaluate({}, trusted_logsource={'product': 'windows', 'category': 'ps_script'}, allowed_rule_ids=TABLE.rule_ids)
    assert len(calls) == 1
    assert len(calls[0]) == 125  # Three process_creation sources stay isolated.
    assert set(r['id'] for r in ROWS).issubset(calls[0])


def required_terms(row):
    for selection in row['detection'].values():
        if isinstance(selection, dict):
            yield from selection.get('ScriptBlockText|contains|all', [])


REQUIRED = [(r, term) for r in ROWS for term in required_terms(r)
            if term.casefold() in r['positive_event']['ScriptBlockText'].casefold()]


@pytest.mark.parametrize('row,term', REQUIRED)
def test_required_conjunction_terms_cannot_be_omitted(row, term):
    original = row['positive_event']['ScriptBlockText']
    changed = re.sub(re.escape(term), ' [omitted] ', original, flags=re.IGNORECASE)
    assert changed != original
    assert not import_rule(row, row['source_path']).matches({'ScriptBlockText': changed})


@pytest.mark.parametrize('row', ROWS, ids=lambda r: r['id'])
@pytest.mark.parametrize('modifier', ['windash', 'base64', 'expand'])
def test_unsupported_constructs_rejected_even_in_unselected_branch(row, modifier):
    broken = deepcopy(row)
    broken['detection']['unused_invalid'] = {'ScriptBlockText|' + modifier: 'x'}
    with pytest.raises(UnsupportedSigmaRule):
        import_rule(broken, broken['source_path'])


BRANCHES = [
    ('susp_local_group_reco', 'Get-LocalGroupMember -Group Administrators', True),
    ('susp_local_group_reco', 'Get-WmiObject Win32_Group', True),
    ('susp_local_group_reco', 'gwmi Win32_Group', True),
    ('susp_local_group_reco', 'Get-CimInstance Win32_Group', True),
    ('susp_local_group_reco', 'gcim Win32_Group', True),
    ('susp_local_group_reco', 'Get-WmiObject Win32_OperatingSystem', False),
    ('susp_mounted_share_deletion', 'Remove-FileShare -Name Lab', True),
    ('susp_start_process', 'saps -FilePath lab.exe -PassThru lab', True),
    ('susp_start_process', 'saps -FilePath lab.exe', False),
    ('susp_start_process', 'saps -PassThru lab', False),
    ('susp_gettypefromclsid', '[type]::GetTypeFromCLSID($lab)', False),
    ('susp_gettypefromclsid', '$lab.ShellExecute("lab")', False),
    ('windows_firewall_profile_disabled', 'Set-NetFirewallProfile -Profile Public -Enabled False', True),
    ('windows_firewall_profile_disabled', 'Set-NetFirewallProfile -Profile Domain -Enabled False', True),
    ('windows_firewall_profile_disabled', 'Set-NetFirewallProfile -Profile Private -Enabled False', True),
    ('winlogon_helper_dll', 'New-Item HKLM:\\CurrentVersion\\Winlogon Lab', True),
    ('wmi_persistence', 'New-CimInstance -Namespace root/subscription -ClassName CommandLineEventConsumer -Property @{Name="Lab"}', True),
    ('wmi_persistence', 'New-CimInstance -Namespace root/cimv2 -ClassName __EventFilter -Property @{Name="Lab"}', False),
    ('susp_invocation_generic', 'powershell -enc LAB -noni lab', False),
    ('susp_invocation_generic', 'powershell -w hidden -noni lab', False),
    ('susp_invocation_generic', 'powershell -enc LAB -w hidden lab', False),
    ('test_netconnection', 'Test-NetConnection -ComputerName lab -port 80 ', False),
    ('test_netconnection', 'Test-NetConnection -ComputerName lab -port 443 ', False),
    # Preserve the upstream literal whitespace, even at the end-of-string edge.
    ('test_netconnection', 'Test-NetConnection -ComputerName lab -port 443', True),
]
BRANCHES += [('susp_invocation_generic', f'powershell {encoded} LAB {hidden} {quiet} lab', True)
    for encoded in ['-enc', '-EncodedCommand', '-ec']
    for hidden in ['-w hidden', '-window hidden', '-windowstyle hidden', '-w 1']
    for quiet in ['-noni', '-noninteractive']]
BRANCHES += [('susp_invoke_webrequest_useragent', f' {cmd} -UserAgent LabAgent', True)
    for cmd in ['Invoke-WebRequest', 'Invoke-RestMethod', 'irm', 'iwr']]
BRANCHES += [('susp_win32_shadowcopy_deletion', f'{getter} Win32_ShadowCopy | {deleter}', True)
    for getter in ['Get-WmiObject', 'gwmi', 'Get-CimInstance', 'gcim']
    for deleter in ['.Delete()', 'Remove-WmiObject', 'rwmi', 'Remove-CimInstance', 'rcim']]
BRANCHES += [('tamper_windows_defender_rem_mp', f'Remove-MpPreference {flag} Lab', True)
    for flag in ['-ControlledFolderAccessProtectedFolders', '-AttackSurfaceReductionRules_Ids',
                 '-AttackSurfaceReductionRules_Actions', '-CheckForSignaturesBeforeRunningScan']]
BRANCHES += [('user_profile_tampering', f'Add-Content $profile -Value {quote}{cmd} Lab{quote}', True)
    for quote in ['"', "'"] for cmd in ['IEX', 'Invoke-Expression', 'Invoke-WebRequest', 'Start-Process']]
BRANCHES += [('timestomp', text, True) for text in ['.CreationTime = $time', '.LastWriteTime = $time',
    '.LastAccessTime = $time', '[IO.File]::SetCreationTime($lab,$time)',
    '[IO.File]::SetLastAccessTime($lab,$time)', '[IO.File]::SetLastWriteTime($lab,$time)']]
BRANCHES += [('susp_mail_acces', text, True) for text in ['Get-Inbox.ps1',
    'Microsoft.Office.Interop.Outlook', 'Microsoft.Office.Interop.Outlook.olDefaultFolders']]


@pytest.mark.parametrize('name,text,expected', BRANCHES)
def test_upstream_alternatives_and_literal_edge_cases(name, text, expected):
    row = BY_NAME[name]
    assert import_rule(row, row['source_path']).matches({'ScriptBlockText': text}) is expected


def test_share_module_load_exclusion_requires_all_original_markers():
    row = BY_NAME['susp_mounted_share_deletion']
    rule = import_rule(row, row['source_path'])
    markers = row['detection']['filter_main_module_load']['ScriptBlockText|contains|all']
    assert not rule.matches({'ScriptBlockText': 'Remove-SmbShare ' + ' '.join(markers)})
    for omitted in markers:
        event = 'Remove-SmbShare ' + ' '.join(m for m in markers if m != omitted)
        assert rule.matches({'ScriptBlockText': event})
