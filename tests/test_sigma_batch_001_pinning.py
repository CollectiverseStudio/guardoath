"""Reviewable pinning restores full upstream metadata without changing detections."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest
import yaml

from guardoath.sigma_rules import LICENSE, import_rule
from guardoath.sigma_rules.audit_originals import compare_record
from guardoath.sigma_rules.evidence_catalog import load_evidence_catalog
from guardoath.sigma_rules.routing import load_routing_table
from guardoath.sigma_rules.verify_sources import verify_batch

ROOT=Path(__file__).resolve().parents[1]/'guardoath/sigma_rules'
FIXTURES=ROOT.parents[1]/'tests/fixtures/sigma_batch_001_provenance'
CURRENT=json.loads((ROOT/'upstream_batch_001.json').read_text(encoding='utf-8'))
HISTORICAL=json.loads((FIXTURES/'imported_projection.json').read_text(encoding='utf-8'))
AUDIT=json.loads((FIXTURES/'audit.json').read_text(encoding='utf-8'))
REVISION='05d1031f537e830b76da53bae2400c5814c89165'
PAIRS=list(zip(HISTORICAL['rules'],CURRENT['rules'],AUDIT['rules']))
ALL=[r for name in sorted(ROOT.glob('upstream_batch_*.json')) for r in json.loads(name.read_text(encoding='utf-8'))['rules']]
SUPPORT={'source_path','source_sha256','source_git_blob','positive_event','negative_event'}


@pytest.mark.parametrize('old,current,audit',PAIRS,ids=[r['id'] for r in CURRENT['rules']])
def test_full_metadata_is_exact_audited_yaml_and_old_projection_is_unchanged(old,current,audit):
    raw=(ROOT/'upstream/batch_001'/Path(current['source_path']).name).read_text(encoding='utf-8').encode('utf-8')
    independent=(FIXTURES/Path(current['source_path']).name).read_text(encoding='utf-8').encode('utf-8')
    assert raw==independent
    assert hashlib.sha256(raw).hexdigest()==current['source_sha256']==audit['source_sha256']
    assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==current['source_git_blob']==audit['source_git_blob']
    source=json.loads(json.dumps(yaml.safe_load(raw),default=str))
    assert {k:v for k,v in current.items() if k not in SUPPORT}==source
    assert all(current[k]==v for k,v in old.items())
    comparison=compare_record(current,source)
    assert comparison['full_metadata_identical'] and comparison['changed_fields']==[]
    assert comparison['omitted_upstream_fields']==[]
    assert all(field in current for field in audit['omitted_upstream_fields'])


@pytest.mark.parametrize('old,current,audit',PAIRS,ids=[r['id'] for r in CURRENT['rules']])
def test_pinning_keeps_match_behavior_across_entire_existing_event_inventory(old,current,audit):
    before=import_rule(old,old['source_path'])
    after=import_rule(current,current['source_path'],source_revision=REVISION)
    assert before.detection==after.detection
    for row in ALL:
        for key in ('positive_event','negative_event'):
            assert before.matches(row[key])==after.matches(row[key])
    assert before.matches({})==after.matches({}) is False
    assert after.finding()=={**before.finding(),'source_url':f"https://github.com/SigmaHQ/sigma/blob/{REVISION}/{current['source_path']}"}


def test_all_catalog_and_routed_findings_now_carry_real_pins_without_new_rules():
    assert CURRENT['source_revision']==REVISION and 'source_branch' not in CURRENT
    assert CURRENT['license']==LICENSE
    evidence=load_evidence_catalog()
    assert len(evidence)==len({r.rule_id for r in evidence})==128
    assert all(r.source_revision==REVISION and r.snapshot_sha256 for r in evidence)
    table=load_routing_table()
    for row in CURRENT['rules']:
        actual=table.evaluate(row['positive_event'],trusted_logsource={'product':'windows','category':'process_creation'},allowed_rule_ids=[row['id']])
        assert actual==[import_rule(row,row['source_path'],source_revision=REVISION).finding()]
    assert 'source_revision' not in HISTORICAL
    assert AUDIT['runtime_metadata_changed'] is False
    assert all(not r['full_metadata_identical'] for r in AUDIT['rules'])


def test_new_snapshot_group_participates_in_full_metadata_source_verifier(tmp_path):
    # Synthetic local commit holds genuine immutable source blobs; its commit ID
    # is not presented as an upstream Sigma revision.
    def git(*args):return subprocess.check_output(['git','-C',str(tmp_path),*args],stderr=subprocess.DEVNULL)
    git('init')
    for row in CURRENT['rules']:
        target=tmp_path/row['source_path'];target.parent.mkdir(parents=True,exist_ok=True)
        raw=(ROOT/'upstream/batch_001'/Path(row['source_path']).name).read_text(encoding='utf-8').encode('utf-8')
        target.write_bytes(raw)
    git('-c','core.autocrlf=false','add','.')
    git('-c','user.name=Pinning fixture','-c','user.email=fixture@example.invalid','-c','commit.gpgsign=false','commit','-m','Synthetic source verifier fixture')
    revision=git('rev-parse','HEAD').decode().strip()
    data=deepcopy(CURRENT);data['source_revision']=revision
    assert verify_batch(tmp_path,ROOT,'batch_001',data,revision,yaml.safe_load)==3
    data['rules'][0]['description']='private-description-drift-canary'
    with pytest.raises(ValueError,match='metadata/detection'):
        verify_batch(tmp_path,ROOT,'batch_001',data,revision,yaml.safe_load)
