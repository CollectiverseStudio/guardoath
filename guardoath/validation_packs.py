"""Trusted, tenant-scoped validation pack selection (no tool authorization)."""

from dataclasses import dataclass
import json
from pathlib import Path
import re

_CODE = re.compile(r"[A-Z][A-Z0-9_]{1,31}\Z")
MAX_CODES = 12


class PackSelectionError(ValueError):
    """An invalid or unauthorized pack selection."""


@dataclass(frozen=True)
class Pack:
    code: str
    version: str
    checker_ids: tuple[str, ...]


class PackRegistry:
    """Immutable per-application registry; never constructed from user text."""

    def __init__(self, published: dict[str, tuple[Pack, ...]], core: dict[str, tuple[str, ...]] | None = None):
        self._published: dict[str, dict[str, Pack]] = {}
        self._core = {app: tuple(ids) for app, ids in (core or {}).items()}
        for app, ids in self._core.items():
            if app not in published or not ids or len(set(ids)) != len(ids) or not all(re.fullmatch(r"[a-z][a-z0-9_]{0,49}", x) for x in ids):
                raise ValueError("Invalid mandatory CORE configuration")
        for app, packs in published.items():
            if not app:
                raise ValueError("Application ID is required")
            entries: dict[str, Pack] = {}
            for pack in packs:
                if not _CODE.fullmatch(pack.code) or pack.code == "CORE":
                    raise ValueError("Invalid or reserved pack code")
                if pack.code in entries or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}", pack.version) or not pack.checker_ids:
                    raise ValueError("Duplicate or incomplete pack")
                if len(set(pack.checker_ids)) != len(pack.checker_ids):
                    raise ValueError("Duplicate checker in pack")
                entries[pack.code] = Pack(pack.code, pack.version, tuple(pack.checker_ids))
            if app in self._core and set(self._core[app]) & {i for p in packs for i in p.checker_ids}:
                raise ValueError("CORE checker cannot be optional")
            self._published[app] = entries

    def resolve(self, application_id: str, requested: tuple[str, ...] = ()) -> tuple[Pack, ...]:
        if len(requested) > MAX_CODES or len(set(requested)) != len(requested):
            raise PackSelectionError("Invalid validation pack selection")
        allowed = self._published.get(application_id, {})
        result = []
        for code in requested:
            if not isinstance(code, str) or not _CODE.fullmatch(code) or code not in allowed:
                raise PackSelectionError("Unknown or unauthorized validation pack")
            result.append(allowed[code])
        return tuple(result)

    def describe(self, application_id: str) -> list[dict]:
        """Expose only this application's available selectors and versions."""
        return [{"code": pack.code, "version": pack.version}
                for pack in self._published.get(application_id, {}).values()]

    def core_checker_ids(self, application_id: str) -> tuple[str, ...] | None:
        return self._core.get(application_id)

    def optional_checker_ids(self, application_id: str) -> set[str]:
        return frozenset(checker for pack in self._published.get(application_id, {}).values() for checker in pack.checker_ids)

    @staticmethod
    def checker_union(core: tuple[str, ...], packs: tuple[Pack, ...]) -> tuple[str, ...]:
        # CORE is always included; ordering is stable and overlaps execute once.
        return tuple(dict.fromkeys((*core, *(checker for pack in packs for checker in pack.checker_ids))))


def load_pack_registry(path: str | Path) -> PackRegistry:
    """Load a bounded, strict, locally published registry at startup."""
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(262_145)
        if len(raw) > 262_144:
            raise ValueError("Pack registry too large")
        def unique_pairs(pairs):
            obj = {}
            for key, value in pairs:
                if key in obj:
                    raise ValueError("Duplicate registry key")
                obj[key] = value
            return obj
        data = json.loads(raw, object_pairs_hook=unique_pairs)
        if not isinstance(data, dict) or set(data) != {"format_version", "applications"}:
            raise ValueError("Invalid registry envelope")
        if data["format_version"] != "1" or not isinstance(data["applications"], dict):
            raise ValueError("Invalid registry version")
        if len(data["applications"]) > 100:
            raise ValueError("Too many applications")
        published = {}
        core = {}
        for app, rows in data["applications"].items():
            if not isinstance(app, str) or not isinstance(rows, dict) or set(rows) != {"core_checker_ids", "packs"}:
                raise ValueError("Invalid application packs")
            core_ids = rows["core_checker_ids"]
            if (not isinstance(core_ids, list) or not core_ids or len(core_ids) > 30
                    or not all(isinstance(x, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,49}", x) for x in core_ids)):
                raise ValueError("Invalid CORE identifiers")
            rows = rows["packs"]
            if not isinstance(rows, list) or len(rows) > 30:
                raise ValueError("Invalid application packs")
            core[app] = tuple(core_ids)
            packs = []
            for row in rows:
                if not isinstance(row, dict) or set(row) != {"code", "version", "checker_ids"}:
                    raise ValueError("Invalid pack definition")
                ids = row["checker_ids"]
                if (not isinstance(ids, list) or not 1 <= len(ids) <= 30
                        or not all(isinstance(x, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,49}", x) for x in ids)
                        or not isinstance(row["code"], str) or not isinstance(row["version"], str)):
                    raise ValueError("Invalid pack identifiers")
                packs.append(Pack(row["code"], row["version"], tuple(ids)))
            published[app] = tuple(packs)
        return PackRegistry(published, core=core)
    except Exception:
        raise ValueError("Unable to load validation pack configuration") from None
