"""Host integration helper: deny-before-dispatch with immutable action snapshot.

The calling host supplies authenticated Principal, authorization gate and its
actual executor. This cannot prevent a host from bypassing the wrapper.
"""
import json
from collections.abc import Callable
from guardoath.agent_activity import fingerprint, InvalidAction


class AgentExecutionDenied(Exception):
    """Never expose tool arguments in exception text."""


def execute_guarded(*, gate, principal, dispatch_request, proposed, execution_action,
                    executor: Callable):
    """Only execute a SNAPSHOT identical to the proposal and approved by host gate.

    Caller must ensure the tool adapter executes only the supplied snapshot,
    not arguments taken from mutable external state. This function performs
    no remote authentication or tenant-policy persistence.
    """
    if not callable(executor):
        raise AgentExecutionDenied('invalid_executor')
    try:
        # Snapshot prevents mutation of caller dictionaries after validation.
        copied = json.loads(json.dumps(execution_action, sort_keys=True,
                                       allow_nan=False, separators=(',', ':')))
        intended = fingerprint(proposed)
        actual = fingerprint(copied)
    except (TypeError, ValueError, RecursionError, OverflowError, InvalidAction):
        raise AgentExecutionDenied('invalid_action') from None
    if intended.digest != actual.digest:
        raise AgentExecutionDenied('action_changed')
    decision = gate.assess(principal, dispatch_request)
    if not decision.allowed:
        raise AgentExecutionDenied('dispatch_denied')
    return executor(copied)
