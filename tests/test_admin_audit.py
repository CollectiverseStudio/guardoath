"""Audit receipt read security, validation, and provider failure tests."""
import pytest
from guardoath.admin_audit import AdminAuditReader, AdminAuditUnavailable

EVENT = "a"*64
SUB = "12345678-1234-1234-1234-123456789012"


def row(operation="tenant.status"):
    data = {"event_id": EVENT, "tenant_id": "usr-good", "actor_sub": SUB,
            "operation": operation, "when_utc": "2026-11-20T04:00:00Z",
            "reason": "Operator approved change", "prior_revision": 7, "new_revision": 8}
    if operation == "tenant.status":
        data.update(old_status="active", new_status="disabled")
    elif operation == "tenant.rule_action":
        data.update(rule_id="pii.email", stage="input", old_action="allow", new_action="block")
    else:
        data.update(old_limit=10000, new_limit=9000)
    return {k: {"N" if type(v) is int else "S": str(v)} for k,v in data.items()}


class Provider:
    def __init__(self, item):
        self.item, self.calls = item, []
    def get_item(self, **kw):
        self.calls.append(kw)
        return {} if self.item is None else {"Item": self.item}


def test_three_operations_strict_projection_and_no_leakage():
    for op in ("tenant.status", "tenant.rule_action", "tenant.allowance_cap"):
        item=row(op)
        item["record"]={"S":"secret"}
        item["payer_id"]={"S":"cus_private"}
        d=Provider(item)
        report=AdminAuditReader(d,"audit-table").read(EVENT)
        assert report["status"] == "ok" and report["actor_sub"] == SUB
        assert len(d.calls) == 1 and d.calls[0]["ConsistentRead"] is True
        assert "ProjectionExpression" in d.calls[0] and "record" not in str(d.calls[0])
        assert "secret" not in str(report) and "cus_private" not in str(report)


def test_invalid_ids_are_rejected_before_provider():
    d=Provider(row())
    for bad in ("", "A"*64, "a"*63, "not/an/id", None):
        with pytest.raises(ValueError): AdminAuditReader(d,"audit-table").read(bad)
    assert d.calls == []


def test_absent_row_not_fabricated():
    assert AdminAuditReader(Provider(None),"audit-table").read(EVENT) is None


@pytest.mark.parametrize("mutated", [
    {"event_id":"b"*64}, {"actor_sub":"bad"}, {"tenant_id":"wrong/path"},
    {"new_revision":9}, {"prior_revision":True}, {"operation":"tenant.refund"},
    {"old_status":"active","new_status":"active"}, {"reason":"short"},
    {"when_utc":"invalid"}])
def test_corrupt_receipts_fail_closed(mutated):
    item=row()
    item.update({k:{"N" if type(v) is int else "S":str(v)} for k,v in mutated.items()})
    with pytest.raises(AdminAuditUnavailable): AdminAuditReader(Provider(item),"audit-table").read(EVENT)


def test_provider_outage_does_not_leak_error():
    class Down:
        def get_item(self,**kw): raise RuntimeError("internal secret")
    with pytest.raises(AdminAuditUnavailable) as err:
        AdminAuditReader(Down(),"audit-table").read(EVENT)
    assert "internal secret" not in str(err.value)
