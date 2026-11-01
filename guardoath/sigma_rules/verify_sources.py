"""Development-only source fidelity check (requires PyYAML, no runtime fetch).

Run: python -m guardoath.sigma_rules.verify_sources /path/to/SigmaHQ/sigma
The upstream checkout must be at the batch's pinned revision. No rules are edited.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys


def verify(upstream: Path) -> int:
    import yaml  # Optional development tool, not required by the matcher/tests.

    root = Path(__file__).resolve().parent
    batch = json.loads((root / 'upstream_batch_002.json').read_text(encoding='utf-8'))
    revision = subprocess.check_output(['git', '-C', str(upstream), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != batch['source_revision']:
        raise ValueError('upstream revision differs from pinned batch')
    for record in batch['rules']:
        path = record['source_path']
        raw = subprocess.check_output(['git', '-C', str(upstream), 'show', f'{revision}:{path}'])
        vendored = root / 'upstream' / 'batch_002' / Path(path).name
        if vendored.read_text(encoding='utf-8').encode() != raw:
            raise ValueError(f'source snapshot differs: {path}')
        if hashlib.sha256(raw).hexdigest() != record['source_sha256']:
            raise ValueError(f'source digest differs: {path}')
        # Normalize YAML dates to ISO strings using the same JSON representation.
        original = json.loads(json.dumps(yaml.safe_load(raw), default=str))
        metadata = {key: value for key, value in record.items() if key not in {
            'source_path', 'source_sha256', 'source_git_blob', 'positive_event', 'negative_event'}}
        if metadata != original:
            raise ValueError(f'upstream metadata/detection differs: {path}')
    return len(batch['rules'])


if __name__ == '__main__':
    print(f'{verify(Path(sys.argv[1]))} rules verified against pinned upstream YAML')
