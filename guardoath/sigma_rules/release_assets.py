"""Release consistency anchors; not upstream authentication or policy authority.

These semantic JSON digests were frozen after the 128-rule immutable Git-source
audit at 05d1031f537e830b76da53bae2400c5814c89165 (C6/C7). Update only with a
reviewed source/fixture change and a fresh full-source audit. Never derive the
expected digest from the artifact being inspected.
"""
import hashlib
import json
from types import MappingProxyType


BATCH_FINGERPRINTS = MappingProxyType({
    'upstream_batch_001.json': 'fd6f77e994885b7ea57d667bcf408f25182761061e86a96b086aec1248b58175',
    'upstream_batch_002.json': '40c1d27af4e6e32dc0a6392e3ca10e80fb138a6a6a81605625e6f46d6453d18c',
    'upstream_batch_003.json': '1acd68c192becb63d30a4cfb7adcf4c70f3a419f31c34408a80bd18eabe28ead',
    'upstream_batch_004.json': '8ae32efc49b9db9facbd500907255a6debd4a8fa59b5d74cebcff451a2a7006d',
    'upstream_batch_005.json': 'ef582fb36112bc76e3753fbf5c6556a153086b4301f41c475a9e6d946202b436',
    'upstream_batch_006.json': '466eaf0d4eb6fe6aff3989399ba70f6e83b09a31b5ef1d09d1bf075cc8a274dc',
})


def verify_release_batch(name, batch):
    """Verify an already strictly parsed, bounded shipped JSON batch.

    Object key order, whitespace and Unicode escaping are immaterial. Array
    order, detections, source metadata and authored fixtures remain significant.
    Generic caller-owned batches use their existing separate builder APIs.
    """
    try:
        digest = hashlib.sha256(json.dumps(batch, sort_keys=True,
            separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise ValueError('invalid release source JSON') from None
    if name not in BATCH_FINGERPRINTS or digest != BATCH_FINGERPRINTS[name]:
        raise ValueError('Sigma batch differs from reviewed release source')
