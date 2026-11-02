"""Reviewed release consistency rejects self-consistent but altered source JSON."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from guardoath.sigma_rules import evidence_catalog, routing, verify_distribution
from guardoath.sigma_rules.release_assets import BATCH_FINGERPRINTS, verify_release_batch

ROOT = Path(__file__).resolve().parents[1] / 'guardoath/sigma_rules'
NAMES = evidence_catalog.BATCH_NAMES
BATCHES = {name: json.loads((ROOT/name).read_text(encoding='utf-8')) for name in NAMES}
ASSETS = {str(p.relative_to(ROOT)).replace('\\','/'): p.read_bytes()
          for p in (ROOT/'upstream').rglob('*.yml')}
ASSETS.update({name:(ROOT/name).read_bytes() for name in NAMES})
ASSETS['LICENSE.Detection.Rules.md'] = (ROOT/'LICENSE.Detection.Rules.md').read_bytes()


def wheel(tmp_path, overrides):
    path = tmp_path/'fixture.whl'
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in (ASSETS | overrides).items():
            archive.writestr(verify_distribution.PREFIX+name, raw)
        archive.writestr(verify_distribution.PREFIX+'release_assets.py',
                         'raise AssertionError("untrusted archive verifier executed")')
    return path


class View:
    def __init__(self, overrides): self.overrides = overrides
    def joinpath(self, *parts):
        name = '/'.join(parts)
        if name not in self.overrides: return ROOT.joinpath(*parts)
        raw = self.overrides[name]
        class Leaf:
            def read_bytes(self): return raw
        return Leaf()


def assert_default_loaders_reject(monkeypatch, overrides):
    view = View(overrides)
    monkeypatch.setattr(evidence_catalog, 'files', lambda _: view)
    monkeypatch.setattr(routing, 'files', lambda _: view)
    with pytest.raises(evidence_catalog.EvidenceCatalogError): evidence_catalog.load_evidence_catalog()
    with pytest.raises(evidence_catalog.EvidenceCatalogError): routing.load_routing_table()


@pytest.mark.parametrize('name', NAMES)
@pytest.mark.parametrize('field', ['detection','author','source_path','source_revision',
                                  'positive_event','negative_event','description','license'])
def test_semantic_changes_cannot_borrow_original_snapshot_claims(tmp_path, monkeypatch, name, field):
    batch = deepcopy(BATCHES[name]); row = batch['rules'][0]
    if field == 'detection':
        row[field] = {'selection':{'CommandLine|contains':'offline-drift-canary'},'condition':'selection'}
    elif field in ('positive_event','negative_event'):
        row[field] = {next(iter(row[field])):'offline-drift-canary'}
    elif field == 'source_revision': batch[field] = '1'*40
    elif field == 'license': batch[field] = 'altered-license'
    else: row[field] = 'offline-drift-canary'
    changed = {name:json.dumps(batch).encode()}
    with pytest.raises(verify_distribution.DistributionContractError):
        verify_distribution.verify_wheel(wheel(tmp_path, changed))
    assert_default_loaders_reject(monkeypatch, changed)


@pytest.mark.parametrize('name', NAMES)
def test_self_rehashed_snapshot_and_metadata_do_not_redefine_release(tmp_path, monkeypatch, name):
    batch = deepcopy(BATCHES[name]); row = batch['rules'][0]
    directory = name.removeprefix('upstream_').removesuffix('.json')
    snapshot = 'upstream/'+directory+'/'+row['source_path'].rsplit('/',1)[-1]
    raw = ASSETS[snapshot]+b'\n# changed but internally consistent snapshot\n'
    row['source_sha256'] = hashlib.sha256(raw).hexdigest()
    row['source_git_blob'] = hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
    changed = {name:json.dumps(batch).encode(),snapshot:raw}
    with pytest.raises(verify_distribution.DistributionContractError):
        verify_distribution.verify_wheel(wheel(tmp_path, changed))
    assert_default_loaders_reject(monkeypatch, changed)


@pytest.mark.parametrize('name', NAMES)
def test_formatting_key_order_unicode_escaping_and_crlf_preserve_release(tmp_path, monkeypatch, name):
    # Reorder every object recursively via sorted serialization; arrays retain order.
    raw = json.dumps(BATCHES[name], sort_keys=True, ensure_ascii=True, indent=4).replace('\n','\r\n').encode()
    changed = {name:raw}
    assert verify_distribution.verify_wheel(wheel(tmp_path,changed)).rule_count == 128
    view = View(changed)
    monkeypatch.setattr(evidence_catalog,'files',lambda _:view)
    monkeypatch.setattr(routing,'files',lambda _:view)
    assert len(evidence_catalog.load_evidence_catalog()) == 128
    assert len(routing.load_routing_table().rule_ids) == 128


@pytest.mark.parametrize('name,index', [(name,i) for name,b in BATCHES.items() for i in range(len(b['rules']))])
def test_every_shipped_rule_detection_is_covered_by_fixed_anchor(name,index):
    batch = deepcopy(BATCHES[name]); batch['rules'][index]['detection']['condition'] = 'selection_impossible'
    with pytest.raises(ValueError,match='reviewed release'): verify_release_batch(name,batch)


def test_anchor_inventory_is_immutable_and_generic_builders_remain_generic():
    assert tuple(BATCH_FINGERPRINTS) == tuple(NAMES)
    with pytest.raises(TypeError): BATCH_FINGERPRINTS[NAMES[0]] = '0'*64
    batch = deepcopy(BATCHES[NAMES[0]])
    batch['rules'][0]['title'] = 'Caller-owned description'
    assert len(evidence_catalog.build_evidence_catalog([batch])) == 3
    assert len(routing.RoutingTable([batch]).rule_ids) == 3
    with pytest.raises(ValueError): verify_release_batch('unrecognized.json',batch)


@pytest.mark.parametrize('value', [float('nan'),float('inf'),{'x':object()},'\ud800'])
def test_noncanonical_inputs_are_sanitized(value):
    with pytest.raises(ValueError) as error: verify_release_batch(NAMES[0],value)
    assert 'object at' not in str(error.value)


def test_routing_rechecks_actual_second_read_instead_of_trusting_prior_catalog(monkeypatch):
    batch = deepcopy(BATCHES[NAMES[0]])
    batch['rules'][0]['detection'] = {'selection':{'CommandLine|contains':'canary'},'condition':'selection'}
    # A prior successful catalog pass cannot authorize a different routing read.
    monkeypatch.setattr(evidence_catalog,'load_evidence_catalog',lambda:())
    monkeypatch.setattr(routing,'files',lambda _:View({NAMES[0]:json.dumps(batch).encode()}))
    with pytest.raises(ValueError,match='reviewed release'): routing.load_routing_table()
