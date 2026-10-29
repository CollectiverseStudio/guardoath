"""Deterministic workflow transition guard for proposed agent actions.

The host must supply authoritative state and perform atomic state updates.
This module does not execute tools or provide a distributed transaction.
"""
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class TransitionPolicy:
    transitions: Mapping[str, tuple[str, ...]]

    def __post_init__(self):
        if not self.transitions:
            raise ValueError("At least one workflow state is required")
        states = set(self.transitions)
        if any(not isinstance(s, str) or not s for s in states):
            raise ValueError("Invalid workflow state")
        for state, destinations in self.transitions.items():
            if (not isinstance(destinations, tuple)
                    or len(destinations) != len(set(destinations))
                    or any(dest not in states for dest in destinations)):
                raise ValueError("Invalid workflow transition")


@dataclass(frozen=True)
class TransitionDecision:
    allowed: bool
    reason: str


def validate_transition(policy: TransitionPolicy, current: str, target: str) -> TransitionDecision:
    if current not in policy.transitions:
        return TransitionDecision(False, "unknown_state")
    if target not in policy.transitions[current]:
        return TransitionDecision(False, "transition_not_allowed")
    return TransitionDecision(True, "allowed")
