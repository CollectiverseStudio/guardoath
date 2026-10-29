"""Trusted, tenant-scoped validation pack selection (no tool authorization)."""

from dataclasses import dataclass
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

    def __init__(self, published: dict[str, tuple[Pack, ...]]):
        self._published: dict[str, dict[str, Pack]] = {}
        for app, packs in published.items():
            if not app:
                raise ValueError("Application ID is required")
            entries: dict[str, Pack] = {}
            for pack in packs:
                if not _CODE.fullmatch(pack.code) or pack.code == "CORE":
                    raise ValueError("Invalid or reserved pack code")
                if pack.code in entries or not pack.version or not pack.checker_ids:
                    raise ValueError("Duplicate or incomplete pack")
                if len(set(pack.checker_ids)) != len(pack.checker_ids):
                    raise ValueError("Duplicate checker in pack")
                entries[pack.code] = pack
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

    def optional_checker_ids(self, application_id: str) -> set[str]:
        return {checker for pack in self._published.get(application_id, {}).values() for checker in pack.checker_ids}

    @staticmethod
    def checker_union(core: tuple[str, ...], packs: tuple[Pack, ...]) -> tuple[str, ...]:
        # CORE is always included; ordering is stable and overlaps execute once.
        return tuple(dict.fromkeys((*core, *(checker for pack in packs for checker in pack.checker_ids))))
