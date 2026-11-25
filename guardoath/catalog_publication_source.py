"""Strict packaged source provenance for owner publication authority.

The public health marker is informational. A publication request instead needs
the exact complete, bounded build manifest matching this source's contracts.
There is no browser field or environment fallback for deployed source identity.
"""
from pathlib import Path

from guardoath.catalog_metadata import strict_catalog_json
from guardoath.state_contracts import release_metadata


class PublicationSourceUnavailable(Exception):
    """No trustworthy exact packaged source can be established."""


def packaged_source_sha(path: Path | None = None) -> str:
    path = Path(__file__).with_name("_release_source.json") if path is None else Path(path)
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError()
        with path.open("rb") as source:
            raw = source.read(4097)
        if len(raw) > 4096:
            raise ValueError()
        manifest = strict_catalog_json(raw.decode("utf-8"))
        if (not isinstance(manifest, dict)
                or type(manifest.get("release_contract_version")) is not int
                or manifest != release_metadata(manifest.get("source_sha"))):
            raise ValueError()
        return manifest["source_sha"]
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        raise PublicationSourceUnavailable("Packaged source provenance unavailable") from exc
