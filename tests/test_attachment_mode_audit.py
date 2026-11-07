"""Read-only tenant attachment policy inventory tests; no AWS calls."""
import json
import pytest

from tools.attachment_mode_audit import AuditIncompleteError, audit, classify_record, main


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
                ], "Count": 2, "LastEvaluatedKey": {"tenant_id": {"S": "hidden-id"}}}
            assert request["ExclusiveStartKey"] == {"tenant_id": {"S": "hidden-id"}}
            return {"Items": [
                {"record": {"S": '{"input_attachment_csv_mode":"auto","output_attachment_csv_mode":"off"}'}},
                {"record": {"S": "invalid json"}},
            ], "Count": 2}
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
            return {"Items": [], "Count": 0}
    result = audit(Client(), "guardoath-tenants")
    assert result["total_records"] == 0
    assert result["mutated_records"] == 0
    assert result["requires_provenance_review"] is False
    assert result["requires_data_quality_review"] is False


def test_invalid_field_values_counted_without_exposing_values():
    class Client:
        def scan(self, **kwargs):
            return {"Items": [{"record": {"S": '{"input_attachment_csv_mode":"bogus","output_attachment_csv_mode":null}'}}], "Count": 1}
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
            return {"Items": [{"record": {"S": stored_record}}], "Count": 1}

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



@pytest.mark.parametrize("pages", [
    [{"Count": 1}],  # omitted Items with a claimed returned item
    [{"Items": [], "Count": 1}],  # returned Count contradicts item list
    [{"Items": [], "Count": False}],  # boolean is not a valid count
    [{"Items": []}],  # omitted Count: no evidence of a complete zero-row page
    [{"Items": [{"record": {"S": "{}"}}]}],  # a row without Count is not authoritative
    [{"Items": "untrusted-page", "Count": 0}],  # never iterate a string as records
    [{"Items": [], "Count": 0, "LastEvaluatedKey": {}}],  # invalid empty cursor
    [
        {"Items": [], "Count": 0, "LastEvaluatedKey": {"tenant_id": {"S": "PRIVATE-ID"}}},
        {"Items": [], "Count": 0, "LastEvaluatedKey": {"tenant_id": {"S": "PRIVATE-ID"}}},
    ],  # a repeated continuation must not loop forever
])
def test_scan_integrity_never_certifies_missing_rows_or_repeating_pages(pages):
    class Client:
        def __init__(self):
            self.calls = 0

        def scan(self, **kwargs):
            result = pages[self.calls]
            self.calls += 1
            return result

    client = Client()
    with pytest.raises(AuditIncompleteError) as exc:
        audit(client, "synthetic-table")
    assert client.calls <= len(pages)
    assert "PRIVATE-ID" not in str(exc.value)
    assert "untrusted-page" not in str(exc.value)


def test_zero_count_scan_page_without_items_is_valid():
    class Client:
        def scan(self, **kwargs):
            return {"Count": 0, "ScannedCount": 0}

    report = audit(Client(), "synthetic-table")
    assert report["total_records"] == 0
    assert report["requires_data_quality_review"] is False


@pytest.mark.parametrize("missing_count", [False, True])
def test_cli_incomplete_scan_is_nonzero_privacy_safe_and_does_not_write(monkeypatch, capsys, missing_count):
    import sys
    import types

    class BadPage:
        def scan(self, **request):
            assert request["ConsistentRead"] is True
            assert "UpdateExpression" not in request
            page = {
                "Items": [{"record": {"S": '{"input_attachment_csv_mode":"off"}'}}],
                "LastEvaluatedKey": {"tenant_id": {"S": "SENSITIVE-ID"}},
            }
            if not missing_count:
                page["Count"] = 2
            return page

    class Session:
        def __init__(self, profile_name, region_name):
            assert profile_name == "synthetic-read-only"
            assert region_name == "us-east-1"

        def client(self, service, config):
            assert service == "dynamodb"
            return BadPage()

    boto3 = types.ModuleType("boto3")
    boto3.Session = Session
    botocore = types.ModuleType("botocore")
    botocore.__path__ = []
    botocore_config = types.ModuleType("botocore.config")
    botocore_config.Config = lambda **kwargs: kwargs
    monkeypatch.setitem(sys.modules, "boto3", boto3)
    monkeypatch.setitem(sys.modules, "botocore", botocore)
    monkeypatch.setitem(sys.modules, "botocore.config", botocore_config)

    assert main(["--profile", "synthetic-read-only", "--table", "synthetic-table"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report == {
        "audit_incomplete": True,
        "requires_data_quality_review": True,
        "mutated_records": 0,
        "reason": "scan_integrity_error",
    }
    assert "SENSITIVE-ID" not in json.dumps(report)
    assert "input_attachment_csv_mode" not in json.dumps(report)


@pytest.mark.parametrize("fail_page", [1, 2])
def test_dynamodb_scan_exceptions_are_sanitized_and_cannot_certify_partial_data(fail_page):
    secret = "PRIVATE-TENANT-OR-CREDENTIAL-MUST-NOT-ESCAPE"

    class Client:
        def __init__(self):
            self.calls = 0

        def scan(self, **request):
            self.calls += 1
            assert "UpdateExpression" not in request
            if self.calls == 1 and fail_page == 2:
                return {
                    "Items": [{"record": {"S": '{"input_attachment_csv_mode":"off"}'}}],
                    "Count": 1,
                    "LastEvaluatedKey": {"tenant_id": {"S": secret}},
                }
            raise RuntimeError("DynamoDB request failed for " + secret)

    client = Client()
    with pytest.raises(AuditIncompleteError) as exc:
        audit(client, "synthetic-table")
    assert client.calls == fail_page
    assert str(exc.value) == "DynamoDB scan request did not complete"
    assert secret not in str(exc.value)
    assert exc.value.__cause__ is None


@pytest.mark.parametrize("failure_point", ["session", "client", "scan"])
def test_cli_aws_failures_return_private_safe_incomplete_json_without_traceback(
    monkeypatch, capsys, failure_point,
):
    import sys
    import types

    secret = "PRIVATE-PROFILE-TENANT-KEY-MUST-NOT-ESCAPE"

    class Client:
        def scan(self, **request):
            assert request["ConsistentRead"] is True
            assert "UpdateExpression" not in request
            raise RuntimeError("private AWS response: " + secret)

    class Session:
        def __init__(self, profile_name, region_name):
            if failure_point == "session":
                raise RuntimeError("private profile failure: " + secret)

        def client(self, service, config):
            assert service == "dynamodb"
            if failure_point == "client":
                raise RuntimeError("private client failure: " + secret)
            return Client()

    boto3 = types.ModuleType("boto3")
    boto3.Session = Session
    botocore = types.ModuleType("botocore")
    botocore.__path__ = []
    config = types.ModuleType("botocore.config")
    config.Config = lambda **kwargs: kwargs
    monkeypatch.setitem(sys.modules, "boto3", boto3)
    monkeypatch.setitem(sys.modules, "botocore", botocore)
    monkeypatch.setitem(sys.modules, "botocore.config", config)

    assert main(["--profile", "synthetic-only", "--table", "synthetic-table"]) == 2
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report == {
        "audit_incomplete": True,
        "requires_data_quality_review": True,
        "mutated_records": 0,
        "reason": "scan_integrity_error" if failure_point == "scan" else "aws_client_unavailable",
    }
    assert output.err == ""
    assert secret not in output.out
    assert "synthetic-only" not in output.out
