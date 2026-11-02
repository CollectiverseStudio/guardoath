"""Source-backed administration patterns; fixtures are not maliciousness evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

import pytest

from guardoath.sigma_rules import LICENSE, UnsupportedSigmaRule, import_rule

ROOT = Path(__file__).resolve().parents[1] / 'guardoath/sigma_rules'
DATA = json.loads((ROOT / 'upstream_batch_005.json').read_text(encoding='utf-8'))
ROWS = DATA['rules']
OLD = [r for n in range(1, 5) for r in json.loads((ROOT / f'upstream_batch_{n:03}.json').read_text(encoding='utf-8'))['rules']]
ALL = OLD + ROWS
RULES = [import_rule(r, r['source_path']) for r in ALL]
NEW_RULES = RULES[len(OLD):]
BY_NAME = {Path(r['source_path']).stem.removeprefix('posh_ps_'): r for r in ROWS}
SET_ACL = 'cae80281-ef23-44c5-873b-fd48d2666f49'
LOCAL_USER = '4fdc44df-bfe9-4fcc-b041-68f5a2d3031c'


def test_inventory_and_source_license_are_exact():
    assert len(ROWS) == 25
    assert len(ALL) == len({r['id'] for r in ALL}) == len({r['source_path'] for r in ALL}) == 103
    assert DATA['license'] == LICENSE
    assert DATA['source_revision'] == '05d1031f537e830b76da53bae2400c5814c89165'
    evidence = json.loads((ROOT / 'batch_005_evidence.json').read_text(encoding='utf-8'))
    assert {r['rule_id'] for r in evidence['rules']} == {r['id'] for r in ROWS}
    assert evidence['runtime_registered'] is False
    for entry in evidence['rules']:
        row = next(r for r in ROWS if r['id'] == entry['rule_id'])
        assert entry['positive_event'] == row['positive_event']
        assert entry['lookalike_event'] == row['negative_event']
        assert entry['maturity'] == 'offline-fixture-tested'


@pytest.mark.parametrize('row', ROWS, ids=lambda r:r['id'])
def test_immutable_yaml_and_pinned_attribution(row):
    raw = (ROOT / 'upstream/batch_005' / Path(row['source_path']).name).read_text(encoding='utf-8').encode('utf-8')
    assert hashlib.sha256(raw).hexdigest() == row['source_sha256']
    assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() == row['source_git_blob']
    rule = import_rule(row, row['source_path'], source_revision=DATA['source_revision'])
    assert rule.finding() == {'rule_id':row['id'], 'title':row['title'], 'author':row['author'],
        'license': LICENSE, 'source_url':f"https://github.com/SigmaHQ/sigma/blob/{DATA['source_revision']}/{row['source_path']}"}


@pytest.mark.parametrize('row', ROWS, ids=lambda r:r['id'])
def test_positive_lookalike_case_and_missing_fields(row):
    rule = import_rule(row, row['source_path'])
    assert rule.matches(row['positive_event'])
    assert rule.matches({'ScriptBlockText':row['positive_event']['ScriptBlockText'].swapcase()})
    assert not rule.matches(row['negative_event'])
    assert not rule.matches({})
    assert not rule.matches({'CommandLine': row['positive_event']['ScriptBlockText']})
    assert not rule.matches({'scriptblocktext': row['positive_event']['ScriptBlockText']})


@pytest.mark.parametrize('row', ROWS, ids=lambda r:r['id'])
@pytest.mark.parametrize('value', [None, False, 7, [], {}, ['Get-LocalUser']])
def test_malformed_field_values_never_match(row,value):
    assert not import_rule(row,row['source_path']).matches({'ScriptBlockText':value})


@pytest.mark.parametrize('row', ROWS, ids=lambda r:r['id'])
@pytest.mark.parametrize('event', [None, [], 'Get-LocalUser', 1, False])
def test_malformed_container_is_explicit_error(row,event):
    with pytest.raises(UnsupportedSigmaRule,match='mapping'):
        import_rule(row,row['source_path']).matches(event)


@pytest.mark.parametrize('row', ROWS, ids=lambda r:r['id'])
@pytest.mark.parametrize('which', ['positive','negative'])
def test_new_fixtures_against_all_103_rules(row,which):
    expected = {row['id']} if which == 'positive' else set()
    # Generic Set-Acl also detects the specific Windows-location rule's two fixtures.
    if row is BY_NAME['set_acl_susp_location']: expected.add(SET_ACL)
    assert {rule.rule_id for rule in RULES if rule.matches(row[which+'_event'])} == expected


@pytest.mark.parametrize('row', OLD, ids=lambda r:r['id'])
@pytest.mark.parametrize('which', ['positive','negative'])
def test_existing_fixtures_keep_explicit_new_rule_overlap(row,which):
    # The upstream broad local-user rule necessarily detects New-LocalUser too.
    expected = {LOCAL_USER} if which == 'positive' and Path(row['source_path']).stem == 'posh_ps_create_local_user' else set()
    assert {rule.rule_id for rule in NEW_RULES if rule.matches(row[which+'_event'])} == expected


def required_terms(row):
    for selection in row['detection'].values():
        if isinstance(selection,dict):
            yield from selection.get('ScriptBlockText|contains|all',[])


REQUIRED = [(r,t) for r in ROWS for t in required_terms(r)
            if t.casefold() in r['positive_event']['ScriptBlockText'].casefold()]


@pytest.mark.parametrize('row,term', REQUIRED)
def test_removing_conjunction_term_is_not_a_match(row,term):
    source = row['positive_event']['ScriptBlockText']
    changed = re.sub(re.escape(term), ' [omitted] ', source, flags=re.IGNORECASE)
    assert changed != source
    assert not import_rule(row,row['source_path']).matches({'ScriptBlockText':changed})


BRANCHES = [
    ('potential_invoke_mimikatz','DumpCreds DumpCerts',True),
    ('potential_invoke_mimikatz','DumpCreds',False),
    ('potential_invoke_mimikatz','crypto::certificates CERT_SYSTEM_STORE_LOCAL_MACHINE',True),
    ('potential_invoke_mimikatz','crypto::certificates CERT_SYSTEM_STORE_CURRENT_USER',False),
    ('root_certificate_installed',r'Move-Item lab.cer Cert:\LocalMachine\Root',True),
    ('root_certificate_installed',r'Move-Item lab.cer Cert:\CurrentUser\My',False),
    ('remotefxvgpudisablement_abuse','prefix function Get-VMRemoteFXPhysicalVideoAdapter {',False),
    ('susp_ad_group_reco','Get-ADUser -f Lab -pr DoesNotRequirePreAuth',True),
    ('susp_ad_group_reco','Get-ADUser -f Lab -pr Enabled',False),
    ('susp_clear_eventlog','Eventing.Reader.EventLogSession ClearLog',True),
    ('susp_clear_eventlog','Diagnostics.EventLog Clear',True),
    ('susp_clear_eventlog','Eventing.Reader.EventLogSession ReadLog',False),
    ('set_policies_to_unsecure_level',"Set-ExecutionPolicy Bypass (New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1')",False),
]
BRANCHES += [('localuser',word+' Lab',True) for word in ['Disable-LocalUser','Enable-LocalUser',
    'Set-LocalUser','New-LocalUser','Rename-LocalUser','Remove-LocalUser']]
BRANCHES += [('script_with_upload_capabilities',f'{cmd} -Uri https://example.invalid {flag}',True)
    for cmd in ['Invoke-RestMethod','irm ','iwr '] for flag in ['-Method "POST"','-Method PUT',"-Method 'PUT'"]]


@pytest.mark.parametrize('name,text,expected', BRANCHES)
def test_upstream_branches_and_exclusion(name,text,expected):
    row = BY_NAME[name]
    assert import_rule(row,row['source_path']).matches({'ScriptBlockText':text}) is expected


@pytest.mark.parametrize('row',ROWS,ids=lambda r:r['id'])
@pytest.mark.parametrize('unsupported', ['windash','base64','expand'])
def test_unsupported_constructs_are_rejected_even_in_dead_branch(row,unsupported):
    broken = deepcopy(row)
    broken['detection']['unused_invalid'] = {'ScriptBlockText|'+unsupported:'x'}
    with pytest.raises(UnsupportedSigmaRule):
        import_rule(broken,broken['source_path'])
