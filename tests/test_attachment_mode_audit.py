"""Read-only tenant attachment policy inventory tests; no AWS calls."""
import json
import pytest

from tools.attachment_mode_audit import audit, classify_record, main


def test_classifies_raw_stored_fields_without_model_defaults():
    assert classify_record("{}") == ("missing", "missing")
    assert classify_record('{"input_attachment_csv_mode":"auto"}') == ("auto", "missing")
    assert classify_record('{"input_attachment_csv_mode":"off","output_attachment_csv_mode":"auto"}') == ("off", "auto")
    assert classify_record('{"input_attachment_csv_mode":null}') == ("invalid", "missing")


@pytest.mark.parametrize("raw", ["[]", "null", '"text"', "{"])
def test_rejects_unparseable_record(raw):
    with pytest.raises((ValueError, json.JSONDecodeError)):
        classify_record(raw)


@pytest.mark.parametrize("raw", [
    '{"input_attachment_csv_mode":"off","input_attachment_csv_mode":"auto"}',
    '{"input_attachment_csv_mode":"auto","input_attachment_csv_mode":"off"}',
    '{"output_attachment_csv_mode":"off","output_attachment_csv_mode":"auto"}',
    '{"output_attachment_csv_mode":"auto","output_attachment_csv_mode":"off"}',
    '{"other":{"key":1,"key":2},"input_attachment_csv_mode":"auto"}',
])
def test_duplicate_json_members_never_hide_saved_policy_or_corrupt_record(raw):
    with pytest.raises(ValueError, match="Duplicate JSON object key"):
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
        "requires_data_quality_review": True,
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
    assert result["requires_data_quality_review"] is False


def test_invalid_field_values_counted_without_exposing_values():
    class Client:
        def scan(self, **kwargs):
            return {"Items": [{"record": {"S": '{"input_attachment_csv_mode":"bogus","output_attachment_csv_mode":null}'}}]}
    report = audit(Client(), "guardoath-tenants")
    assert report["stages"]["input"]["invalid"] == 1
    assert report["stages"]["output"]["invalid"] == 1
    assert "bogus" not in json.dumps(report)
    assert report["requires_data_quality_review"] is True
    assert report["requires_provenance_review"] is False


@pytest.mark.parametrize("stored_record, expected_exit, expected_quality, expected_provenance", [
    ('{"input_attachment_csv_mode":"auto"}', 0, False, False),
    ('{"input_attachment_csv_mode":"off"}', 0, False, True),
    ('{"input_attachment_csv_mode":"bogus"}', 2, True, False),
    ('{"input_attachment_csv_mode":"off","output_attachment_csv_mode":null}', 2, True, True),
    ('{unparseable', 2, True, False),
    ('{"input_attachment_csv_mode":"off","input_attachment_csv_mode":"auto"}', 2, True, False),
    ('{"output_attachment_csv_mode":"auto","output_attachment_csv_mode":"off"}', 2, True, False),
])
def test_cli_never_reports_invalid_stored_modes_as_clean(
    monkeypatch, capsys, stored_record, expected_exit, expected_quality, expected_provenance,
):
    """Synthetic-only AWS doubles exercise the actual main() exit and no-write path."""
    import sys
    import types

    class ReadOnlyClient:
        def scan(self, **kwargs):
            assert kwargs["ProjectionExpression"] == "#record"
            assert kwargs["ConsistentRead"] is True
            assert "UpdateExpression" not in kwargs
            return {"Items": [{"record": {"S": stored_record}}]}

    class Session:
        def __init__(self, profile_name, region_name):
            assert profile_name == "synthetic-read-only"
            assert region_name == "us-east-1"

        def client(self, service, config):
            assert service == "dynamodb"
            return ReadOnlyClient()

    boto3_module = types.ModuleType("boto3")
    boto3_module.Session = Session
    botocore_module = types.ModuleType("botocore")
    botocore_module.__path__ = []
    config_module = types.ModuleType("botocore.config")
    config_module.Config = lambda **kwargs: kwargs
    monkeypatch.setitem(sys.modules, "boto3", boto3_module)
    monkeypatch.setitem(sys.modules, "botocore", botocore_module)
    monkeypatch.setitem(sys.modules, "botocore.config", config_module)

    assert main(["--profile", "synthetic-read-only", "--table", "test-tenants"]) == expected_exit
    report = json.loads(capsys.readouterr().out)
    assert report["total_records"] == 1
    assert report["mutated_records"] == 0
    assert report["requires_data_quality_review"] is expected_quality
    assert report["requires_provenance_review"] is expected_provenance
    assert "synthetic-read-only" not in json.dumps(report)
    assert stored_record not in json.dumps(report)
