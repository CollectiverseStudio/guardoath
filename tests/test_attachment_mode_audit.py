"""Read-only tenant attachment policy inventory tests; no AWS calls."""
import json
import pytest

from tools.attachment_mode_audit import audit, classify_record


def test_classifies_raw_stored_fields_without_model_defaults():
    assert classify_record("{}") == ("missing", "missing")
    assert classify_record('{"input_attachment_csv_mode":"auto"}') == ("auto", "missing")
    assert classify_record('{"input_attachment_csv_mode":"off","output_attachment_csv_mode":"auto"}') == ("off", "auto")
    assert classify_record('{"input_attachment_csv_mode":null}') == ("invalid", "missing")


@pytest.mark.parametrize("raw", ["[]", "null", '"text"', "{"])
def test_rejects_unparseable_record(raw):
    with pytest.raises((ValueError, json.JSONDecodeError)):
        classify_record(raw)


def test_read_only_paginated_scan_and_aggregate_without_customer_data():
    class FakeClient:
        def __init__(self):
            self.calls = []

        def scan(self, **request):
            self.calls.append(request)
            assert request["ProjectionExpression"] == "#record"
            assert request["ExpressionAttributeNames"] == {"#record": "record"}
            assert request["ConsistentRead"] is True
            assert "FilterExpression" not in request
            if len(self.calls) == 1:
                assert "ExclusiveStartKey" not in request
                return {"Items": [
                    {"record": {"S": json.dumps({"input_attachment_csv_mode": "off", "key_hashes": ["SENSITIVE"]})}},
                    {"record": {"S": '{"output_attachment_csv_mode":"auto"}'}},
                ], "LastEvaluatedKey": {"tenant_id": {"S": "hidden-id"}}}
            assert request["ExclusiveStartKey"] == {"tenant_id": {"S": "hidden-id"}}
            return {"Items": [
                {"record": {"S": '{"input_attachment_csv_mode":"auto","output_attachment_csv_mode":"off"}'}},
                {"record": {"S": "invalid json"}},
            ]}
    client = FakeClient()
    report = audit(client, "guardoath-tenants")
    assert len(client.calls) == 2
    assert report == {
        "total_records": 4,
        "unparseable_records": 1,
        "stages": {
            "input": {"missing": 1, "auto": 1, "off": 1, "invalid": 0},
            "output": {"missing": 1, "auto": 1, "off": 1, "invalid": 0},
        },
        "requires_provenance_review": True,
        "mutated_records": 0,
    }
    assert "SENSITIVE" not in json.dumps(report)
    assert "hidden-id" not in json.dumps(report)


def test_empty_table_has_no_changes_and_no_off_review():
    class Client:
        def scan(self, **kwargs):
            return {"Items": []}
    result = audit(Client(), "guardoath-tenants")
    assert result["total_records"] == 0
    assert result["mutated_records"] == 0
    assert result["requires_provenance_review"] is False


def test_invalid_field_values_counted_without_exposing_values():
    class Client:
        def scan(self, **kwargs):
            return {"Items": [{"record": {"S": '{"input_attachment_csv_mode":"bogus","output_attachment_csv_mode":null}'}}]}
    report = audit(Client(), "guardoath-tenants")
    assert report["stages"]["input"]["invalid"] == 1
    assert report["stages"]["output"]["invalid"] == 1
    assert "bogus" not in json.dumps(report)
