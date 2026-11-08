"""Bounded, privacy-safe comparison of proposed and reported AI agent actions.

This does not authenticate telemetry, authorize dispatch, execute actions, or
persist receipts. Source trust must be established by a separate authenticated
host/collector; caller-provided text cannot prove what happened on a machine.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

MAX_BYTES = 8192
ALLOWED_TOOLS = frozenset({'shell.run', 'http.request', 'file.write', 'file.delete'})


class InvalidAction(ValueError):
    pass


@dataclass(frozen=True)
class ActionFingerprint:
    digest: str
    tool: str


def fingerprint(action: Mapping) -> ActionFingerprint:
    if not isinstance(action, Mapping) or set(action) != {'tool', 'target', 'arguments'}:
        raise InvalidAction('Action requires tool, target and arguments')
    tool, target, args = action['tool'], action['target'], action['arguments']
    if not isinstance(tool, str) or tool not in ALLOWED_TOOLS:
        raise InvalidAction('Unsupported action tool')
    if not isinstance(target, str) or not 1 <= len(target) <= 256:
        raise InvalidAction('Invalid resource target')
    if not isinstance(args, dict) or not args or len(args) > 24 or any(
            not isinstance(k, str) or not 1 <= len(k) <= 64 for k in args):
        raise InvalidAction('Invalid action arguments')
    try:
        encoded = json.dumps(action, sort_keys=True, ensure_ascii=True, separators=(',', ':'),
                             allow_nan=False).encode('ascii')
    except (TypeError, ValueError, RecursionError, OverflowError):
        raise InvalidAction('Non-JSON action arguments') from None
    if len(encoded) > MAX_BYTES:
        raise InvalidAction('Action exceeds size limit')
    return ActionFingerprint(hashlib.sha256(encoded).hexdigest(), tool)


def compare_actions(proposed: Mapping, observed: Mapping | None) -> dict:
    """Returns only safe metadata; matching is NOT independent attestation."""
    intended = fingerprint(proposed)
    if observed is None:
        return {'status': 'no_observation', 'matches': False,
                'verified_execution': False, 'finding_ids': ['agent_activity.missing_observation'],
                'authorization': 'not_granted'}
    actual = fingerprint(observed)
    same = intended.digest == actual.digest
    return {'status': 'matching_unverified' if same else 'mismatch', 'matches': same,
            'verified_execution': False,
            'finding_ids': [] if same else ['agent_activity.action_mismatch'],
            'authorization': 'not_granted'}
