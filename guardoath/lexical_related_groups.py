"""Presentation-only grouping of related lexical security findings.

Unlike lexical_finding_consolidation.consolidate, this helper NEVER rewrites,
drops, substitutes or deduplicates original finding identities. Consumers must
apply tenant rule actions to the original identifiers BEFORE display grouping.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .lexical_finding_consolidation import ALIASES


@dataclass(frozen=True)
class RelatedFindingGroup:
    """Related concept for display, with original rule identities retained."""
    concept_id: str
    rule_ids: tuple[str, ...]


def related_groups(rule_ids: Iterable[str]) -> tuple[RelatedFindingGroup, ...]:
    """Group related batch-5/6 IDs without changing finding-action authority.

    This accepts identifiers only, never content or policy objects. All original
    IDs are preserved once each in input order, including unknown rules. Rules
    not in the eight documented related pairs form independent groups. The
    returned IDs must not be used as replacements for the source findings.
    """
    if isinstance(rule_ids, (str, bytes)):
        raise TypeError("rule_ids must be an iterable of IDs, not a string")
    reverse = {canonical: alias for alias, canonical in ALIASES.items()}
    groups: dict[str, list[str]] = {}
    seen: set[str] = set()
    count = 0
    for identifier in rule_ids:
        count += 1
        if count > 4096:
            raise ValueError("too many rule identifiers")
        if not isinstance(identifier, str) or not identifier or len(identifier) > 128:
            raise ValueError("invalid rule ID")
        if identifier in seen:
            continue
        seen.add(identifier)
        # A group label is descriptive, never an enforcement rule ID.
        concept = ALIASES.get(identifier, identifier)
        if identifier in reverse:
            concept = identifier
        groups.setdefault(concept, []).append(identifier)
    return tuple(RelatedFindingGroup(concept_id=concept, rule_ids=tuple(ids))
                 for concept, ids in groups.items())
