"""Offline evidence export is source-backed, immutable and never policy authority."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import shutil

import pytest

from guardoath.sigma_rules import import_rule, LICENSE
from guardoath.sigma_rules import evidence_catalog as catalog

ROOT = Path(__file__).resolve().parents[1] / 'guardoath/sigma_rules'
BATCHES = [json.loads((ROOT/name).read_text(encoding='utf-8')) for name in catalog.BATCH_NAMES]
RECORDS = [(batch, row) for batch in BATCHES for row in batch['rules']]
EVIDENCE = catalog.load_evidence_catalog()
BY_ID = {row.rule_id: row for row in EVIDENCE}


def test_complete_inventory_counts_sources_without_claiming_runtime_registration():
    assert len(EVIDENCE) == len(BY_ID) == 178
    assert sum(row.source_revision is not None for row in EVIDENCE) == 178
    assert len({row.source_path for row in EVIDENCE}) == 178
    assert all(row.maturity == 'offline-fixture-tested' for row in EVIDENCE)
    assert all('other rules may flag it' in row.limitations for row in EVIDENCE)
    assert all('chat-safety control' in row.limitations for row in EVIDENCE)


@pytest.mark.parametrize('batch,source',RECORDS,ids=[r['id'] for _,r in RECORDS])
def test_original_provenance_logsource_and_use_case_are_preserved(batch,source):
    row = BY_ID[source['id']]
    expected = import_rule(source,source['source_path'],source_revision=batch.get('source_revision'))
    assert (row.rule_id,row.title,row.author,row.license,row.source_url) == (
        source['id'],source['title'],source['author'],LICENSE,expected.source_url)
    assert dict(row.logsource) == source['logsource']
    assert row.source_revision == batch.get('source_revision')
    assert row.snapshot_sha256 == source.get('source_sha256')
    assert row.upstream_status == source.get('status')
    assert row.upstream_level == source.get('level')
    assert row.upstream_false_positives == (tuple(source['falsepositives']) if 'falsepositives' in source else None)
    if 'description' in source: assert row.use_case == source['description']
    else: assert row.use_case == 'Review events matching upstream rule: '+source['title']


@pytest.mark.parametrize('batch,source',RECORDS,ids=[r['id'] for _,r in RECORDS])
def test_exported_positive_and_lookalike_execute_against_original_rule(batch,source):
    row = BY_ID[source['id']]
    rule = import_rule(source,source['source_path'])
    assert dict(row.positive_event) == source['positive_event']
    assert dict(row.lookalike_event) == source['negative_event']
    assert rule.matches(dict(row.positive_event))
    assert not rule.matches(dict(row.lookalike_event))
    assert not rule.matches({})
    assert not rule.matches({key:None for key,_ in row.positive_event})


def test_original_three_are_pinned_only_after_exact_source_audit():
    rows=[BY_ID[r['id']] for r in BATCHES[0]['rules']]
    assert all(r.source_revision == BATCHES[0]['source_revision'] and r.snapshot_sha256 for r in rows)
    assert all(r.upstream_false_positives is not None for r in rows)
    assert all('/blob/'+BATCHES[0]['source_revision']+'/' in r.source_url for r in rows)


def test_historical_unpinned_projection_still_cannot_fabricate_provenance():
    old=json.loads((ROOT.parents[1]/'tests/fixtures/sigma_batch_001_provenance/imported_projection.json').read_text(encoding='utf-8'))
    rows=catalog.build_evidence_catalog([old])
    assert len(rows)==3
    assert all(r.source_revision is None and r.snapshot_sha256 is None for r in rows)
    assert all(r.upstream_false_positives is None and '/blob/master/' in r.source_url for r in rows)


def test_evidence_is_immutable_and_json_exports_are_independent():
    row=EVIDENCE[-1]
    with pytest.raises(FrozenInstanceError): row.title='changed'
    original=row.as_dict();changed=row.as_dict()
    changed['logsource']['category']='chat'
    changed['positive_event']['ScriptBlockText']='unrelated'
    changed['upstream_false_positives'].append('invented')
    assert row.as_dict()==original
    assert json.loads(json.dumps(original))==original
    assert not {'action','enabled','tenant_id','policy_version','runtime_registered'}.intersection(original)


def test_builder_never_mutates_input_or_executes_matches(monkeypatch):
    from guardoath.sigma_rules import ImportedRule
    batches=deepcopy(BATCHES);before=deepcopy(batches)
    monkeypatch.setattr(ImportedRule,'matches',lambda *args:pytest.fail('catalog executed a rule'))
    assert catalog.build_evidence_catalog(batches)==EVIDENCE
    assert batches==before


@pytest.mark.parametrize('value',[None,{},'source',[],[{}],[{'rules':[]}],False])
def test_malformed_batch_envelopes_fail(value):
    with pytest.raises(catalog.EvidenceCatalogError): catalog.build_evidence_catalog(value)


@pytest.mark.parametrize('change',[
    {'license':'unverified'}, {'source_repository':'https://example.invalid'},
    {'source_revision':None},{'source_revision':'main'}, {'rules':[]}, {'rules':'wrong'},
])
def test_invalid_batch_metadata_fails(change):
    batch=deepcopy(BATCHES[1]);batch.update(change)
    with pytest.raises(catalog.EvidenceCatalogError):catalog.build_evidence_catalog([batch])


@pytest.mark.parametrize('field,value',[
    ('positive_event',None),('negative_event',{}),('positive_event',{'ScriptBlockText':False}),
    ('positive_event',{'ScriptBlockText':[]}),('positive_event',{1:'text'}),
    ('negative_event',{'ScriptBlockText':''}),('source_sha256','bad'),('source_git_blob',None),
    ('falsepositives','unknown'),('falsepositives',[False]),('description',[]),('level',None),
    ('logsource',{'product':'windows','category':'ps_script','definition':True}),
    ('author',''),('detection',{'x':{'ScriptBlockText|windash':'x'},'condition':'x'}),
])
def test_malformed_rule_metadata_and_fixtures_fail_closed(field,value):
    batch=deepcopy(BATCHES[1]);batch['rules'][0][field]=value
    with pytest.raises(catalog.EvidenceCatalogError):catalog.build_evidence_catalog([batch])


@pytest.mark.parametrize('field',['id','source_path'])
def test_duplicate_identity_fails_atomically(field):
    batch=deepcopy(BATCHES[1]);batch['rules'][1][field]=batch['rules'][0][field]
    with pytest.raises(catalog.EvidenceCatalogError,match='duplicate'):
        catalog.build_evidence_catalog([batch])


def test_unpinned_digest_and_oversized_inventory_fail():
    batch=deepcopy(BATCHES[0]);batch.pop('source_revision');batch['rules'][0]['source_sha256']='a'*64
    with pytest.raises(catalog.EvidenceCatalogError,match='pinned'):
        catalog.build_evidence_catalog([batch])
    batch=deepcopy(BATCHES[0]);batch['rules']=[batch['rules'][0]]*4097
    with pytest.raises(catalog.EvidenceCatalogError,match='size'):
        catalog.build_evidence_catalog([batch])


@pytest.fixture
def assets(tmp_path,monkeypatch):
    for name in catalog.BATCH_NAMES:shutil.copyfile(ROOT/name,tmp_path/name)
    shutil.copytree(ROOT/'upstream',tmp_path/'upstream')
    monkeypatch.setattr(catalog,'files',lambda package:tmp_path)
    return tmp_path


def test_missing_asset_cannot_return_partial_catalog(assets):
    (assets/catalog.BATCH_NAMES[-1]).unlink()
    with pytest.raises(FileNotFoundError):catalog.load_evidence_catalog()


def test_corrupt_last_snapshot_cannot_return_partial_catalog(assets):
    source=BATCHES[-1]['rules'][-1]['source_path'].rsplit('/',1)[-1]
    (assets/'upstream'/catalog.BATCH_NAMES[-1].removeprefix('upstream_').removesuffix('.json')/source).write_text('changed',encoding='utf-8')
    with pytest.raises(catalog.EvidenceCatalogError,match='digest'):
        catalog.load_evidence_catalog()


def test_windows_text_checkout_preserves_git_snapshot_verification(assets):
    for path in (assets/'upstream').rglob('*.yml'):
        raw=path.read_text(encoding='utf-8').encode('utf-8')
        path.write_bytes(raw.replace(b'\n',b'\r\n'))
    assert catalog.load_evidence_catalog()==EVIDENCE


@pytest.mark.parametrize('raw',[b'{bad',b'{"license":"first","license":"second"}',b'\xff'])
def test_bad_utf8_json_and_duplicate_keys_fail(assets,raw):
    (assets/catalog.BATCH_NAMES[0]).write_bytes(raw)
    with pytest.raises(catalog.EvidenceCatalogError,match='JSON'):
        catalog.load_evidence_catalog()


def test_oversized_asset_is_explicit_error(assets):
    (assets/catalog.BATCH_NAMES[0]).write_bytes(b' '*(catalog.MAX_BATCH_BYTES+1))
    with pytest.raises(catalog.EvidenceCatalogError,match='size'):
        catalog.load_evidence_catalog()
