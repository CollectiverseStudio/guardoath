"""Development-only batch 001 provenance audit; no fetches or runtime mutation.

Run with a local Sigma Git checkout/object database and a full immutable commit.
PyYAML is needed only for this development command, never the event matcher.
"""
from copy import deepcopy
from importlib.resources import files
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from . import import_rule

SUPPORT_FIELDS = frozenset({'source_path', 'positive_event', 'negative_event',
                            'source_sha256', 'source_git_blob'})


def compare_record(record, upstream):
    """Compare imported fields; differences contain names rather than values."""
    if not isinstance(record, dict) or not isinstance(upstream, dict):
        raise ValueError('source records must be objects')
    path = record.get('source_path')
    import_rule(record, path)
    import_rule(upstream, path)
    projected = set(record) - SUPPORT_FIELDS
    required = {'id', 'title', 'author', 'logsource', 'detection'}
    if not required.issubset(projected):
        raise ValueError('missing imported provenance')
    changed = sorted(key for key in projected if key not in upstream or record[key] != upstream[key])
    omitted = sorted(set(upstream) - projected)
    return {
        'rule_id': record['id'], 'source_path': path,
        'identical_imported_fields': not changed,
        'changed_fields': changed, 'omitted_upstream_fields': omitted,
        'full_metadata_identical': not changed and not omitted,
    }


def audit(upstream, revision):
    """Read Git objects at an exact commit; no branch lookup, checkout or writes.

    This establishes equivalence at the supplied revision, not when the original
    import occurred, author identity beyond upstream metadata or source ownership.
    It never upgrades the runtime catalog's unpinned status.
    """
    if not isinstance(revision, str) or not re.fullmatch('[a-f0-9]{40}', revision):
        raise ValueError('full immutable upstream revision required')
    directory = str(Path(upstream).resolve())
    def git(*args):
        return subprocess.check_output(['git', '-C', directory, *args], stderr=subprocess.DEVNULL)
    if git('rev-parse', '--verify', revision + '^{commit}').decode().strip() != revision:
        raise ValueError('upstream commit identity mismatch')
    import yaml
    batch = json.loads(files('guardoath.sigma_rules').joinpath('upstream_batch_001.json').read_text(encoding='utf-8'))
    if batch.get('source_repository') != 'https://github.com/SigmaHQ/sigma':
        raise ValueError('unexpected original source repository')
    records = batch.get('rules')
    if not isinstance(records, list) or len(records) != 3:
        raise ValueError('three original source records required')
    results = []
    seen = set()
    for record in records:
        import_rule(record, record.get('source_path'))  # Validate before passing path to Git.
        if record['id'] in seen:
            raise ValueError('duplicate original source identity')
        seen.add(record['id'])
        raw = git('show', revision + ':' + record['source_path'])
        if len(raw) > 1_000_000:
            raise ValueError('upstream rule exceeds audit size limit')
        original = json.loads(json.dumps(yaml.safe_load(raw), default=str))
        result = compare_record(deepcopy(record), original)
        blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        if git('rev-parse', revision + ':' + record['source_path']).decode().strip() != blob:
            raise ValueError('upstream Git blob identity mismatch')
        result.update(source_revision=revision, source_sha256=hashlib.sha256(raw).hexdigest(),
                      source_git_blob=blob,
                      source_url=f'https://github.com/SigmaHQ/sigma/blob/{revision}/{record["source_path"]}')
        results.append(result)
    return {'source_repository': batch['source_repository'], 'source_revision': revision,
            'runtime_metadata_changed': False, 'rules': results}


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print('Usage: python -m guardoath.sigma_rules.audit_originals UPSTREAM_GIT_DIR FULL_COMMIT', file=sys.stderr)
        return 2
    try:
        report = audit(args[0], args[1])
    except Exception:
        print('Original-rule provenance audit unavailable; verify local Git objects and development YAML dependency.', file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 0 if all(row['identical_imported_fields'] for row in report['rules']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
