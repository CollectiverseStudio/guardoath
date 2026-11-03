"""Lossless presentation grouping of related lexical security findings.

This is not policy selection, tenant authorization, enforcement, or auditing.
Apply tenant actions to the original findings before using these visual groups.
Never submit concept_id as a tenant rule ID.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re

from .models import RULE_ID_PATTERN

from .lexical_finding_consolidation import ALIASES


@dataclass(frozen=True)
class RelatedFindingGroup:
    """Immutable display group retaining individual IDs and original positions."""
    concept_id: str
    rule_ids: tuple[str, ...]
    source_positions: tuple[int, ...]


def related_groups(rule_ids: Iterable[str]) -> tuple[RelatedFindingGroup, ...]:
    """Group related batch-5/6 identities without dropping repeated findings.

    The returned groups retain one entry per input occurrence and its source
    position. A descriptive 'display:' concept cannot be mistaken for a valid
    tenant rule_actions ID. Callers must not use this helper to enforce policy.
    """
    if isinstance(rule_ids, (str, bytes)):
        raise TypeError("rule_ids must be an iterable of IDs, not a string")
    groups: dict[str, list[tuple[int, str]]] = {}
    for position, identifier in enumerate(rule_ids):
        if position >= 4096:
            raise ValueError("too many rule identifiers")
        if (not isinstance(identifier, str) or len(identifier) > 100
                or re.fullmatch(RULE_ID_PATTERN, identifier) is None):
            raise ValueError("invalid tenant rule ID")
        canonical = ALIASES.get(identifier, identifier)
        # The colon is intentionally outside valid tenant rule-ID syntax.
        concept = "display:" + canonical
        groups.setdefault(concept, []).append((position, identifier))
    return tuple(RelatedFindingGroup(
        concept_id=concept,
        rule_ids=tuple(identifier for _, identifier in entries),
        source_positions=tuple(position for position, _ in entries),
    ) for concept, entries in groups.items())
