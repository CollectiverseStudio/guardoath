"""Offline acceptance for an explicitly trusted, non-editable installed artifact.

Unlike ZIP inspection this executes installed Sigma code and authored fixtures.
It neither installs nor downloads anything, and is not a deployment gate override.
"""
from dataclasses import asdict, dataclass
from importlib import import_module, metadata
import json
from pathlib import Path
import sys

from .budgets import SigmaEvaluationError
from . import LICENSE
from .evidence_catalog import BATCH_NAMES, MAX_BATCH_BYTES, _unique, load_evidence_catalog
from .matcher import UnsupportedSigmaRule
from .release_assets import verify_release_batch
from .routing import load_routing_table
from .verify_distribution import LICENSE_SHA256
import hashlib


class InstalledAcceptanceError(ValueError):
    """Installed acceptance failed; never report a partial pass."""


@dataclass(frozen=True)
class InstalledEvidence:
    distribution_version: str
    rule_count: int
    pinned_rule_count: int
    required_asset_count: int
    positive_cases: int
    benign_cases: int
    missing_event_cases: int
    malformed_event_rejections: int
    routing_previews: int
    source_revisions: tuple[str, ...]


_MODULES = ('__init__', 'matcher', 'conditions', 'budgets', 'bounded_regex',
            'events', 'evidence_catalog', 'release_assets', 'routing',
            'verify_distribution', 'verify_installation')


def _origins():
    return {name: Path(import_module('guardoath.sigma_rules' +
            ('' if name == '__init__' else '.'+name)).__file__) for name in _MODULES}


def verify_installation(expected_root):
    """Accept only current imports and metadata at the exact expected install root.

    The caller trusts the installed code. Root/RECORD checks establish origin and
    completeness, not signatures, provenance of the wheel or tamper resistance
    against replacing this verifier. Archive acceptance is a separate operation.
    """
    try:
        root = Path(expected_root).resolve(strict=True)
        if not root.is_dir(): raise InstalledAcceptanceError('installation root must be a directory')
        package = root/'guardoath'/'sigma_rules'
        for name, origin in _origins().items():
            expected = package/(name+'.py')
            if (origin.resolve(strict=True) != expected or expected.resolve(strict=True) != expected
                    or not expected.is_file()):
                raise InstalledAcceptanceError('Sigma imports differ from expected installation root')
        dist = metadata.distribution('guardoath')
        if Path(dist.locate_file('')).resolve(strict=True) != root:
            raise InstalledAcceptanceError('distribution metadata differs from expected installation root')
        direct = dist.read_text('direct_url.json')
        if direct and json.loads(direct).get('dir_info', {}).get('editable'):
            raise InstalledAcceptanceError('editable installations are not artifact acceptance')
        indexed = {str(p).replace('\\','/') for p in (dist.files or ())}
        if not isinstance(dist.version, str) or not dist.version.strip():
            raise InstalledAcceptanceError('installed distribution version is missing')
        modules = {'guardoath/sigma_rules/'+name+'.py' for name in _MODULES}
        if not modules.issubset(indexed):
            raise InstalledAcceptanceError('installed module records are incomplete')

        evidence = load_evidence_catalog()
        evidence_by_id = {row.rule_id:row for row in evidence}
        table = load_routing_table()
        assets = {'guardoath/sigma_rules/'+name for name in BATCH_NAMES}
        assets.add('guardoath/sigma_rules/LICENSE.Detection.Rules.md')
        records = []
        for name in BATCH_NAMES:
            raw = (package/name).read_bytes()
            if len(raw) > MAX_BATCH_BYTES: raise InstalledAcceptanceError('installed batch exceeds bounds')
            batch = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique)
            verify_release_batch(name, batch)
            directory = name.removeprefix('upstream_').removesuffix('.json')
            for row in batch['rules']:
                assets.add('guardoath/sigma_rules/upstream/'+directory+'/'+row['source_path'].rsplit('/',1)[-1])
                records.append(row)
        if not assets.issubset(indexed):
            raise InstalledAcceptanceError('installed asset records are incomplete')
        for name in assets:
            path = root/name
            if path.resolve(strict=True) != path:
                raise InstalledAcceptanceError('installed assets must remain inside the installation')
        if hashlib.sha256((package/'LICENSE.Detection.Rules.md').read_text(encoding='utf-8').encode()).hexdigest() != LICENSE_SHA256:
            raise InstalledAcceptanceError('installed Sigma license differs from reviewed license')
        if (len(records) != len(evidence) or len(records) != len(table.rule_ids)
                or {row['id'] for row in records} != set(evidence_by_id)
                or set(table.rule_ids) != set(evidence_by_id)
                or any(row.source_revision is None for row in evidence)):
            raise InstalledAcceptanceError('installed source inventories disagree')
        for row in records:
            source = {k:row['logsource'][k] for k in ('product','category')}
            args = {'trusted_logsource':source, 'allowed_rule_ids':[row['id']]}
            plan = table.plan(**args)
            if plan.selected_rule_ids != (row['id'],) or plan.excluded_rule_ids:
                raise InstalledAcceptanceError('installed routing preview differs from source')
            positive = table.evaluate(row['positive_event'], **args)
            expected = {'rule_id':row['id'], 'title':row['title'], 'author':row['author'],
                'license':LICENSE, 'source_url':'https://github.com/SigmaHQ/sigma/blob/'+
                evidence_by_id[row['id']].source_revision+'/'+row['source_path']}
            if positive != [expected] or table.evaluate(row['negative_event'], **args) or table.evaluate({}, **args):
                raise InstalledAcceptanceError('installed fixture outcomes differ from source')
            try: table.evaluate({'invalid':[]}, **args)
            except UnsupportedSigmaRule: pass
            else: raise InstalledAcceptanceError('installed malformed event was accepted')
        count = len(records)
        return InstalledEvidence(dist.version, count, sum(r.source_revision is not None for r in evidence),
            len(assets), count, count, count, count, count,
            tuple(sorted({r.source_revision for r in evidence if r.source_revision})))
    except InstalledAcceptanceError: raise
    except (OSError, ValueError, TypeError, AttributeError, RecursionError, ImportError, SigmaEvaluationError):
        raise InstalledAcceptanceError('unable to verify trusted installed Sigma artifact') from None


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] != '--expected-root':
        print('Usage: python -I -m guardoath.sigma_rules.verify_installation --expected-root INSTALL_ROOT', file=sys.stderr)
        return 2
    try: report = verify_installation(args[1])
    except InstalledAcceptanceError as error:
        print('Sigma installed acceptance failed: '+str(error), file=sys.stderr)
        return 1
    print(json.dumps(asdict(report), sort_keys=True))
    return 0


if __name__ == '__main__': raise SystemExit(main())
