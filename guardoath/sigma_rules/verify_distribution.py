"""Verify Sigma wheel assets without importing or executing code from the archive."""
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import sys
import zipfile

from .evidence_catalog import BATCH_NAMES, build_evidence_catalog, _unique

PREFIX='guardoath/sigma_rules/'
LICENSE_SHA256='d5f4c36a0528e01cfdd9e4fc60879201d3af49b41c520200643d321fe53da5f9'
MAX_WHEEL_BYTES=64_000_000
MAX_MEMBERS=8192
MAX_ASSET_BYTES=1_000_000
MAX_REQUIRED_BYTES=16_000_000


class DistributionContractError(ValueError):
    """Artifact cannot satisfy the Sigma asset contract; never a partial pass."""


@dataclass(frozen=True)
class DistributionEvidence:
    rule_count: int
    pinned_rule_count: int
    required_asset_count: int
    source_revisions: tuple[str,...]
    license_sha256: str


def _git_text(raw):
    try:return raw.decode('utf-8').replace('\r\n','\n').replace('\r','\n').encode('utf-8')
    except UnicodeError:raise DistributionContractError('Sigma asset is not UTF-8') from None


def _constant(value):
    raise DistributionContractError('nonfinite source JSON is prohibited')


def verify_wheel(path):
    """Inspect only this artifact. Never use checkout files to fill missing assets.

    This proves asset presence/self-consistency, not upstream authenticity,
    installed runtime behavior, archive-code safety or deployment readiness.
    """
    path=Path(path)
    try:
        if path.stat().st_size>MAX_WHEEL_BYTES:
            raise DistributionContractError('wheel exceeds inspection size limit')
        with zipfile.ZipFile(path) as archive:
            infos=archive.infolist()
            if len(infos)>MAX_MEMBERS:
                raise DistributionContractError('wheel exceeds member count limit')
            names={};folded=set()
            for info in infos:
                name=info.orig_filename;parts=name.rstrip('/').split('/')
                if (name!=info.filename or '\\' in name or ':' in name or any(p in ('','.','..') for p in parts)
                        or name.rstrip('/').casefold() in folded):
                    raise DistributionContractError('ambiguous or unsafe wheel member names')
                folded.add(name.rstrip('/').casefold());names[name]=info
            actual_batches={n for n in names if n.startswith(PREFIX+'upstream_batch_') and n.endswith('.json')}
            if actual_batches-{PREFIX+n for n in BATCH_NAMES}:
                raise DistributionContractError('unrecognized Sigma source batch')
            total=0;required=set()
            def read(name):
                nonlocal total
                info=names.get(name)
                if info is None:raise DistributionContractError('missing required Sigma asset')
                if info.file_size>MAX_ASSET_BYTES or info.flag_bits&1:
                    raise DistributionContractError('unsupported or oversized Sigma asset')
                with archive.open(info) as stream:raw=stream.read(MAX_ASSET_BYTES+1)
                if len(raw)!=info.file_size or len(raw)>MAX_ASSET_BYTES:
                    raise DistributionContractError('invalid Sigma asset size')
                total+=len(raw)
                if total>MAX_REQUIRED_BYTES:
                    raise DistributionContractError('required Sigma assets exceed size limit')
                required.add(name)
                return _git_text(raw)
            batches=[]
            for filename in BATCH_NAMES:
                try:
                    batches.append(json.loads(read(PREFIX+filename),object_pairs_hook=_unique,parse_constant=_constant))
                except DistributionContractError:raise
                except (ValueError,RecursionError):
                    raise DistributionContractError('invalid Sigma evidence JSON') from None
            try:evidence=build_evidence_catalog(batches)
            except ValueError:raise DistributionContractError('invalid Sigma evidence metadata') from None
            snapshots=set()
            for filename,batch in zip(BATCH_NAMES,batches):
                if batch.get('source_revision') is None:continue
                directory=filename.removeprefix('upstream_').removesuffix('.json')
                for row in batch['rules']:
                    name=PREFIX+'upstream/'+directory+'/'+row['source_path'].rsplit('/',1)[-1]
                    raw=read(name);snapshots.add(name)
                    if (hashlib.sha256(raw).hexdigest()!=row['source_sha256'] or
                            hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=row['source_git_blob']):
                        raise DistributionContractError('Sigma snapshot digest mismatch')
            actual_snapshots={n for n in names if n.startswith(PREFIX+'upstream/') and n.endswith('.yml')}
            if actual_snapshots!=snapshots:
                raise DistributionContractError('unindexed Sigma snapshot assets')
            license_digest=hashlib.sha256(read(PREFIX+'LICENSE.Detection.Rules.md')).hexdigest()
            if license_digest!=LICENSE_SHA256:
                raise DistributionContractError('Sigma license differs from retained upstream license')
            return DistributionEvidence(len(evidence),sum(r.source_revision is not None for r in evidence),
                len(required),tuple(sorted({r.source_revision for r in evidence if r.source_revision})),license_digest)
    except DistributionContractError:raise
    except (OSError,zipfile.BadZipFile,RuntimeError,NotImplementedError):
        raise DistributionContractError('unable to inspect Sigma wheel assets') from None


def main(argv=None):
    args=sys.argv[1:] if argv is None else argv
    if len(args)!=1:
        print('Usage: python -m guardoath.sigma_rules.verify_distribution WHEEL',file=sys.stderr)
        return 2
    try:
        report=verify_wheel(args[0])
    except DistributionContractError as error:
        print('Sigma artifact verification failed: '+str(error),file=sys.stderr)
        return 1
    print(json.dumps(asdict(report),sort_keys=True))
    return 0


if __name__=='__main__':raise SystemExit(main())
