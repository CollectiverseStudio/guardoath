"""UC105 authorization decisions; identity must be authenticated by host."""
import pytest

from guardoath.agent_authorization import Principal, authorize


def test_read_only_cannot_escalate():
    reader = Principal("tenant-a", frozenset({"orders:read"}))
    assert authorize(reader, tenant_id="tenant-a", permission="orders:read")
    assert not authorize(reader, tenant_id="tenant-a", permission="orders:delete")
    assert not authorize(reader, tenant_id="tenant-b", permission="orders:read")


def test_explicit_writer_permission():
    writer = Principal("tenant-a", frozenset({"orders:read", "orders:write"}))
    assert authorize(writer, tenant_id="tenant-a", permission="orders:write")
    assert not authorize(writer, tenant_id="tenant-a", permission="orders:delete")


def test_missing_identity_or_permission_is_denied():
    with pytest.raises(ValueError):
        Principal("", frozenset({"orders:read"}))
    with pytest.raises(ValueError):
        Principal("tenant-a", frozenset())
    p = Principal("tenant-a", frozenset({"orders:read"}))
    assert not authorize(p, tenant_id="", permission="orders:read")
    assert not authorize(p, tenant_id="tenant-a", permission="")
