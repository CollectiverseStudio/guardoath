"""Contract tests for context-aware agent action decisions."""
import pytest
from guardoath.agent_context_decisions import ActionContext, decide

BASE = dict(
    tenant_id="tenant-a", principal_tenant_id="tenant-a",
    resource_tenant_id="tenant-a", authenticated=True,
    permitted_actions=frozenset({"read", "delete", "export"}),
)

def context(**changes):
    values = dict(BASE, action="read")
    values.update(changes)
    return ActionContext(**values)

@pytest.mark.parametrize("changes,reason", [
    ({"authenticated": False}, "unauthenticated"),
    ({"principal_tenant_id": "tenant-b"}, "tenant_mismatch"),
    ({"resource_tenant_id": "tenant-b"}, "tenant_mismatch"),
    ({"action": "publish"}, "permission_denied"),
    ({"action": "delete"}, "approval_missing"),
    ({"action": "export"}, "approval_missing"),
    ({"action": "unknown"}, "unknown_action"),
    ({"tenant_id": ""}, "invalid_context"),
])
def test_fail_closed(changes, reason):
    result = decide(context(**changes))
    assert result.action == "block"
    assert reason in result.reason_codes

@pytest.mark.parametrize("action", ["read", "delete", "export"])
def test_authorized_action_allowed(action):
    assert decide(context(action=action, approved=True)).action == "allow"

def test_indicators_are_flagged_even_when_approved():
    result = decide(context(action="delete", approved=True, signals=("agent.sql_destructive",)))
    assert result.action == "flag"
    assert result.signal_ids == ("agent.sql_destructive",)

def test_indicators_do_not_override_denial():
    result = decide(context(authenticated=False, signals=("agent.sql_destructive",)))
    assert result.action == "block"
    assert "unauthenticated" in result.reason_codes

def test_reports_all_denial_reasons_without_customer_data():
    result = decide(context(authenticated=False, resource_tenant_id="tenant-b",
                            action="delete", signals=("agent.sql_destructive",)))
    assert set(result.reason_codes) == {"unauthenticated", "tenant_mismatch", "approval_missing"}
    assert "tenant-b" not in repr(result)

def test_signal_deduplication():
    result = decide(context(signals=("signal.x", "signal.x")))
    assert result.signal_ids == ("signal.x",)

def test_invalid_type():
    with pytest.raises(TypeError):
        decide(None)

def test_no_approval_required_for_read():
    assert decide(context(action="read", approved=False)).action == "allow"
