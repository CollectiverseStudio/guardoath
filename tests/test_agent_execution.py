"""Side-effect containment for the host-side action dispatch adapter."""
import pytest
from guardoath.agent_execution import AgentExecutionDenied, execute_guarded


class Gate:
    def __init__(self, allowed):
        self.allowed = allowed
        self.calls = 0

    def assess(self, principal, request):
        self.calls += 1
        return type('Decision', (), {'allowed': self.allowed})()


def action(cmd='Get-Process'):
    return {'tool': 'shell.run', 'target': 'host1', 'arguments': {'command': cmd}}


def test_permitted_exact_action_is_forwarded_as_snapshot():
    seen = []
    proposed = action()
    current = action()
    gate = Gate(True)
    assert execute_guarded(
        gate=gate, principal='p', dispatch_request='r', proposed=proposed,
        execution_action=current, executor=lambda data: seen.append(data) or 'ok'
    ) == 'ok'
    current['arguments']['command'] = 'changed'
    assert seen == [action()] and gate.calls == 1


def test_denial_and_mismatch_cannot_trigger_side_effect():
    seen = []
    for gate, current in [(Gate(False), action()), (Gate(True), action('Clear-EventLog'))]:
        with pytest.raises(AgentExecutionDenied):
            execute_guarded(
                gate=gate, principal='p', dispatch_request='r', proposed=action(),
                execution_action=current, executor=lambda data: seen.append(data)
            )
    assert not seen


def test_malformed_action_cannot_trigger_side_effect():
    seen = []
    with pytest.raises(AgentExecutionDenied):
        execute_guarded(
            gate=Gate(True), principal='p', dispatch_request='r', proposed=action(),
            execution_action={**action(), 'trusted': True},
            executor=lambda data: seen.append(data)
        )
    assert not seen
