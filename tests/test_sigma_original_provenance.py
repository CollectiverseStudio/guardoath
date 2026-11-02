"""Original-three audit evidence: frozen upstream snapshots and offline Git only."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest
import yaml

from guardoath.sigma_rules import import_rule
from guardoath.sigma_rules import audit_originals
from guardoath.sigma_rules.evidence_catalog import load_evidence_catalog

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/sigma_batch_001_provenance'
BATCH = json.loads((ROOT / 'guardoath/sigma_rules/upstream_batch_001.json').read_text(encoding='utf-8'))
REPORT = json.loads((FIXTURES / 'audit.json').read_text(encoding='utf-8'))
ROWS = BATCH['rules']


def original(row):
    raw = (FIXTURES / Path(row['source_path']).name).read_text(encoding='utf-8').encode()
    return raw, json.loads(json.dumps(yaml.safe_load(raw), default=str))


@pytest.mark.parametrize('record,evidence', list(zip(ROWS, REPORT['rules'])), ids=[r['id'] for r in ROWS])
def test_exact_source_blobs_and_imported_projection_match(record, evidence):
    raw, source = original(record)
    assert hashlib.sha256(raw).hexdigest() == evidence['source_sha256']
    assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == evidence['source_git_blob']
    result = audit_originals.compare_record(record, source)
    assert result == {k: evidence[k] for k in result}
    assert result['identical_imported_fields'] is True
    assert result['full_metadata_identical'] is False
    assert {'description', 'references', 'falsepositives'}.issubset(result['omitted_upstream_fields'])
    assert import_rule(source, record['source_path']).matches(record['positive_event'])
    assert not import_rule(source, record['source_path']).matches(record['negative_event'])


@pytest.mark.parametrize('record', ROWS, ids=lambda r: r['id'])
@pytest.mark.parametrize('field', ['id', 'author', 'title', 'status', 'level'])
def test_changed_provenance_is_reported_by_field_without_echo(record, field):
    _, source = original(record)
    changed = deepcopy(record)
    changed[field] = 'private-audit-canary'
    result = audit_originals.compare_record(changed, source)
    assert result['identical_imported_fields'] is False
    assert result['changed_fields'] == [field]
    assert 'private-audit-canary' not in str(result['changed_fields'])
    assert record[field] != 'private-audit-canary'


@pytest.mark.parametrize('record', ROWS, ids=lambda r: r['id'])
def test_detection_drift_is_not_called_an_original_match(record):
    _, source = original(record)
    changed = deepcopy(record)
    changed['detection'] = {'selection': {'CommandLine|contains': 'private-audit-canary'}, 'condition': 'selection'}
    result = audit_originals.compare_record(changed, source)
    assert result['changed_fields'] == ['detection']
    assert not result['identical_imported_fields']
    assert 'private-audit-canary' not in json.dumps(result)


@pytest.mark.parametrize('record', ROWS, ids=lambda r: r['id'])
def test_added_or_altered_descriptive_fields_are_not_ignored(record):
    _, source = original(record)
    restored = deepcopy(record)
    restored.update({key: source[key] for key in set(source) - set(record)})
    assert audit_originals.compare_record(restored, source)['full_metadata_identical'] is True
    restored['description'] = 'private-description-canary'
    result = audit_originals.compare_record(restored, source)
    assert result['changed_fields'] == ['description']
    assert 'private-description-canary' not in json.dumps(result)


def test_audit_evidence_does_not_upgrade_runtime_provenance_or_change_inventory():
    assert REPORT['runtime_metadata_changed'] is False
    assert REPORT['source_revision'] == '05d1031f537e830b76da53bae2400c5814c89165'
    assert 'source_revision' not in BATCH
    evidence = load_evidence_catalog()
    assert len(evidence) == 128
    assert sum(r.source_revision is not None for r in evidence) == 125
    originals = [r for r in evidence if r.rule_id in {row['id'] for row in ROWS}]
    assert len(originals) == 3
    assert all(r.source_revision is None and r.snapshot_sha256 is None for r in originals)


@pytest.mark.parametrize('revision', ['master', 'HEAD', '', '0'*39, '0'*41, 'z'*40, 'a'*40+';anything', None])
def test_only_full_immutable_commit_is_accepted_before_git(revision, monkeypatch):
    monkeypatch.setattr(audit_originals.subprocess, 'check_output', lambda *a, **k: pytest.fail('Git invoked'))
    with pytest.raises(ValueError, match='immutable'):
        audit_originals.audit('.', revision)


@pytest.fixture
def offline_git(tmp_path):
    # A synthetic local history containing genuine frozen rule files, not a
    # claim that this test commit belongs to the upstream Sigma repository.
    def git(*args, data=None):
        return subprocess.check_output(['git', '-C', str(tmp_path), *args], input=data, stderr=subprocess.DEVNULL)
    git('init')
    for row in ROWS:
        target = tmp_path / row['source_path']
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(original(row)[0])
    git('-c', 'core.autocrlf=false', 'add', '.')
    git('-c', 'user.name=Offline audit fixture', '-c', 'user.email=fixture@example.invalid',
        '-c', 'commit.gpgsign=false', 'commit', '-m', 'Synthetic provenance fixture')
    return tmp_path, git('rev-parse', 'HEAD').decode().strip(), git


def test_audit_reads_exact_git_objects_with_dirty_worktree_and_no_source_mutation(offline_git):
    path, revision, git = offline_git
    target = path / ROWS[0]['source_path']
    target.write_text('dirty worktree is not audited', encoding='utf-8')
    report = audit_originals.audit(path, revision)
    assert report['source_revision'] == revision
    assert len(report['rules']) == 3
    assert all(r['identical_imported_fields'] for r in report['rules'])
    assert [r['source_git_blob'] for r in report['rules']] == [r['source_git_blob'] for r in REPORT['rules']]
    assert 'dirty worktree' in target.read_text(encoding='utf-8')


def test_missing_git_objects_cannot_produce_a_partial_success(offline_git, capsys):
    path, _, _ = offline_git
    assert audit_originals.main([str(path), '0'*40]) == 2
    output = capsys.readouterr()
    assert not output.out and 'unavailable' in output.err


def test_cli_emits_all_three_results_and_uses_documented_exit_status(offline_git, capsys):
    path, revision, _ = offline_git
    assert audit_originals.main([str(path), revision]) == 0
    report = json.loads(capsys.readouterr().out)
    assert len(report['rules']) == 3
    assert all(r['identical_imported_fields'] for r in report['rules'])
    assert audit_originals.main([]) == 2
    assert 'Usage:' in capsys.readouterr().err


def test_cli_reports_upstream_author_drift_with_nonzero_status(offline_git, capsys):
    path, _, git = offline_git
    target = path / ROWS[0]['source_path']
    text = target.read_text(encoding='utf-8')
    assert 'author: frack113' in text
    target.write_text(text.replace('author: frack113', 'author: Offline changed author'), encoding='utf-8')
    git('-c', 'core.autocrlf=false', 'add', '.')
    git('-c', 'user.name=Offline audit fixture', '-c', 'user.email=fixture@example.invalid',
        '-c', 'commit.gpgsign=false', 'commit', '-m', 'Synthetic changed attribution')
    revision = git('rev-parse', 'HEAD').decode().strip()
    assert audit_originals.main([str(path), revision]) == 1
    report = json.loads(capsys.readouterr().out)
    assert len(report['rules']) == 3
    assert report['rules'][0]['changed_fields'] == ['author']
    assert report['rules'][0]['identical_imported_fields'] is False
    assert all(r['identical_imported_fields'] for r in report['rules'][1:])
