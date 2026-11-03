"""Offline registry handoff; no runtime selection, registration or tenant actions."""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
from importlib.resources import files
import json
import re

from . import LICENSE, import_rule

BATCH_NAMES = tuple(f'upstream_batch_{n:03d}.json' for n in range(1, 9))
MAX_BATCH_BYTES = 1_000_000
MAX_CATALOG_RULES = 4096  # Metadata bound, independent of event evaluation's 128-rule limit.
LIMITATIONS = ('Synthetic fixtures are not upstream tests or measured effectiveness. '
               'Legitimate administration may match. A look-alike is nonmatching for '
               'this rule only; other rules may flag it. Requires original Windows '
               'logsource routing and operational context; not a chat-safety control.')


class EvidenceCatalogError(ValueError):
    """Invalid handoff data; never return a partial catalog."""


@dataclass(frozen=True)
class RuleEvidence:
    rule_id: str
    title: str
    author: str
    license: str
    source_path: str
    source_url: str
    source_revision: str | None
    snapshot_sha256: str | None
    logsource: tuple[tuple[str, str], ...]
    upstream_status: str | None
    upstream_level: str | None
    use_case: str
    upstream_false_positives: tuple[str, ...] | None
    positive_event: tuple[tuple[str, str], ...]
    lookalike_event: tuple[tuple[str, str], ...]
    maturity: str = 'offline-fixture-tested'
    limitations: str = LIMITATIONS

    def as_dict(self):
        """Fresh JSON-ready copy; consumers cannot mutate the immutable evidence."""
        return {
            'rule_id': self.rule_id, 'title': self.title, 'author': self.author,
            'license': self.license, 'source_path': self.source_path,
            'source_url': self.source_url, 'source_revision': self.source_revision,
            'snapshot_sha256': self.snapshot_sha256, 'logsource': dict(self.logsource),
            'upstream_status': self.upstream_status, 'upstream_level': self.upstream_level,
            'use_case': self.use_case,
            'upstream_false_positives': (list(self.upstream_false_positives)
                                       if self.upstream_false_positives is not None else None),
            'positive_event': dict(self.positive_event), 'lookalike_event': dict(self.lookalike_event),
            'maturity': self.maturity, 'limitations': self.limitations,
        }


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 32_000:
        raise EvidenceCatalogError('invalid evidence text')
    return value


def _event(value):
    if (not isinstance(value, Mapping) or not 1 <= len(value) <= 256
            or any(not isinstance(k, str) or not k for k in value)):
        raise EvidenceCatalogError('invalid synthetic event')
    return tuple(sorted((key, _text(text)) for key, text in value.items()))


def build_evidence_catalog(batches):
    """Validate source metadata and fixtures, returning an atomic immutable handoff.

    Does not execute fixture detections; unit tests establish their local match
    contracts. Does not choose what a tenant runs or imply a hosted control exists.
    """
    if (not isinstance(batches, Sequence) or isinstance(batches, (str, bytes))
            or not 1 <= len(batches) <= 64):
        raise EvidenceCatalogError('one to 64 source batches required')
    result, ids, paths = [], set(), set()
    for batch in batches:
        if not isinstance(batch, Mapping) or batch.get('license') != LICENSE:
            raise EvidenceCatalogError('invalid source batch license')
        if batch.get('source_repository') != 'https://github.com/SigmaHQ/sigma':
            raise EvidenceCatalogError('invalid source repository')
        rows = batch.get('rules')
        if not isinstance(rows, list) or not rows or len(result) + len(rows) > MAX_CATALOG_RULES:
            raise EvidenceCatalogError('invalid evidence inventory size')
        revision = batch.get('source_revision')
        if 'source_revision' in batch and (not isinstance(revision, str) or not re.fullmatch('[a-f0-9]{40}', revision)):
            raise EvidenceCatalogError('invalid source revision')
        for row in rows:
            if not isinstance(row, Mapping):
                raise EvidenceCatalogError('invalid source rule')
            try:
                rule = import_rule(row, row.get('source_path'), source_revision=revision)
            except Exception:
                raise EvidenceCatalogError('incompatible source rule') from None
            if rule.rule_id in ids or row['source_path'] in paths:
                raise EvidenceCatalogError('duplicate source identity')
            _text(rule.title)
            _text(rule.author)
            digest = row.get('source_sha256')
            blob = row.get('source_git_blob')
            if revision is not None:
                if (not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest)
                        or not isinstance(blob, str) or not re.fullmatch('[a-f0-9]{40}', blob)):
                    raise EvidenceCatalogError('missing pinned source digests')
            elif digest is not None or blob is not None:
                raise EvidenceCatalogError('snapshot digests require pinned revision')
            source = row['logsource']
            if any(not isinstance(k, str) or not isinstance(v, str) for k, v in source.items()):
                raise EvidenceCatalogError('invalid source descriptor')
            false_positives = row.get('falsepositives')
            if 'falsepositives' in row and not isinstance(false_positives, list):
                raise EvidenceCatalogError('invalid upstream false positives')
            description = row.get('description')
            # Batch 001 has no description. Do not fabricate upstream prose.
            use_case = _text(description) if description is not None else 'Review events matching upstream rule: '+rule.title
            for name in ('status', 'level'):
                if name in row: _text(row[name])
            result.append(RuleEvidence(
                rule.rule_id, rule.title, rule.author, rule.license, row['source_path'],
                rule.source_url, revision, digest, tuple(sorted(source.items())),
                row.get('status'), row.get('level'), use_case,
                tuple(_text(x) for x in false_positives) if false_positives is not None else None,
                _event(row.get('positive_event')), _event(row.get('negative_event')),
            ))
            ids.add(rule.rule_id)
            paths.add(row['source_path'])
    return tuple(result)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceCatalogError('duplicate source JSON key')
        result[key] = value
    return result


def load_evidence_catalog():
    """Load fixed shipped assets and verify pinned Git-text snapshots offline.

    All shipped rules now have immutable snapshots. Generic unpinned input to
    the builder still retains null provenance rather than a fabricated claim.
    """
    from .release_assets import verify_release_batch
    root = files('guardoath.sigma_rules')
    batches = []
    for name in BATCH_NAMES:
        raw = root.joinpath(name).read_bytes()
        if len(raw) > MAX_BATCH_BYTES:
            raise EvidenceCatalogError('source batch exceeds size limit')
        try:
            batch = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique)
            verify_release_batch(name,batch)
        except (UnicodeError, ValueError, RecursionError):
            raise EvidenceCatalogError('invalid source JSON') from None
        batches.append(batch)
    evidence = build_evidence_catalog(batches)
    for name, batch in zip(BATCH_NAMES, batches):
        if batch.get('source_revision') is None:
            continue
        directory = name.removeprefix('upstream_').removesuffix('.json')
        for row in batch['rules']:
            # Canonical Git text, independent of Windows checkout line endings.
            raw = root.joinpath('upstream', directory, row['source_path'].rsplit('/', 1)[-1]).read_text(encoding='utf-8').encode('utf-8')
            if (hashlib.sha256(raw).hexdigest() != row['source_sha256'] or
                    hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() != row['source_git_blob']):
                raise EvidenceCatalogError('source snapshot digest mismatch')
    return evidence


if __name__ == '__main__':
    print(json.dumps({'format_version': 1, 'domain': 'security_events',
        'runtime_registered': False, 'rules': [row.as_dict() for row in load_evidence_catalog()]},
        indent=2, ensure_ascii=False))
