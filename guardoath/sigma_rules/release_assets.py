"""Release consistency anchors; not upstream authentication or policy authority.

The first six digests were frozen after the 128-rule immutable Git-source audit
(C6/C7); batch007 follows the independent 153-rule C14 audit at the same revision
05d1031f537e830b76da53bae2400c5814c89165. Batch008 follows the independent 178-rule
C17 audit at that same revision; batch009 follows the 203-rule C20 audit; batch010 follows the 228-rule C23 immutable-source audit. Update only with a
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
    'upstream_batch_007.json': '63839db9ceee4a414a19cd23edc208b80861875a7a29810aa4a7ed5fbd02b4f0',
    'upstream_batch_008.json': '129ecd233dd5988ed41a8d2b3681fae258a07de46d24a86b4117f692f50f3b51',
    'upstream_batch_009.json': 'bd1e35aa18db69bca77a368d5abee684298bc9bfdd2ac7be34b7f34874c6848f',
    'upstream_batch_010.json': '0f008e6d5cb5cc84aa7319d30d4cabff134513e2bd74f9b51c1040bf28e2d298',
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
