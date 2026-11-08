"""Evidence comparison is diagnostic, not proof of real execution."""
import pytest
from guardoath.agent_activity import compare_actions, fingerprint, InvalidAction


def action(command='Get-Process'):
    return {'tool': 'shell.run', 'target': 'host-42', 'arguments': {'command': command}}


def test_identical_action_does_not_imply_execution_authority():
    result = compare_actions(action(), action())
    assert result == {'status': 'matching_unverified', 'matches': True,
                      'verified_execution': False, 'finding_ids': [], 'authorization': 'not_granted'}


def test_changed_command_target_or_tool_is_detected_without_echo():
    for changed in [action('Clear-EventLog -LogName Security'),
                    {**action(), 'target': 'host-43'},
                    {**action(), 'tool': 'file.delete'}]:
        result = compare_actions(action(), changed)
        assert result['status'] == 'mismatch'
        assert 'Get-Process' not in repr(result)
        assert 'Clear-EventLog' not in repr(result)


def test_missing_observation_is_not_pass():
    assert compare_actions(action(), None)['status'] == 'no_observation'


def test_json_key_order_not_material():
    a = {'arguments': {'a': 1, 'b': 2}, 'target': 'host-42', 'tool': 'shell.run'}
    b = {'tool': 'shell.run', 'target': 'host-42', 'arguments': {'b': 2, 'a': 1}}
    assert fingerprint(a).digest == fingerprint(b).digest


@pytest.mark.parametrize('invalid', [
    {}, {'tool': 'shell.run', 'target': 'a', 'arguments': {}, 'allowed': True},
    {'tool': 'shell.run', 'target': 'a', 'arguments': {}},
    {'tool': 'shell.run', 'target': 'a', 'arguments': {'x': float('nan')}},
    {'tool': 'shell.run', 'target': 'a', 'arguments': {'x': 'x' * 9000}},
    {'tool': 'unknown.exec', 'target': 'a', 'arguments': {'x': 'y'}},
])
def test_invalid_actions_rejected(invalid):
    with pytest.raises(InvalidAction):
        fingerprint(invalid)
