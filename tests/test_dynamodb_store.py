"""DynamoDB tenant store. Most tests use a small in-memory test double that implements exactly the
expressions the store sends (the constants in guardoath.dynamodb_store). With the aws extra installed,
a botocore Stubber test checks every request against the DynamoDB API model. With
GUARDOATH_DYNAMODB_TEST_TABLE set, one test runs the store against that real table."""
import copy
import os
import secrets as random_secrets

import pytest

from guardoath import dynamodb_store as ddb
from guardoath.dynamodb_store import DynamoDBTenantStore, ensure_demo_records, from_item, to_item
from guardoath.tenant_record import TenantRecord, demo_key, demo_records, key_hash, new_key
from guardoath.tenant_store import TenantConflict, TenantNotFound, UsageLimitReached

SECRET = "dynamodb-test-secret-" + "s" * 32
MONTH = "2026-10"


class ConditionFailed(Exception):
    def __init__(self):
        super().__init__("The conditional request failed")
        self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class FakeDynamoDB:
    """Implements the store's expressions only; anything else is a test failure."""

    def __init__(self, page_size=3):
        self.items: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self.page_size = page_size
        self.before_update = None  # optional hook: runs just before an update is applied

    def _table(self, kwargs):
        assert kwargs["TableName"] == "guardoath-tenants"

    def get_item(self, **kwargs):
        self.calls.append(("get_item", kwargs)); self._table(kwargs)
        assert kwargs["ConsistentRead"] is True and set(kwargs) == {"TableName", "Key", "ConsistentRead"}
        item = self.items.get(kwargs["Key"]["tenant_id"]["S"])
        return {"Item": copy.deepcopy(item)} if item else {}

    def scan(self, **kwargs):
        self.calls.append(("scan", kwargs)); self._table(kwargs)
        assert kwargs["ProjectionExpression"] == "tenant_id" and kwargs["ConsistentRead"] is True
        ids = sorted(self.items, reverse=True)  # deliberately not in order
        start = ids.index(kwargs["ExclusiveStartKey"]["tenant_id"]["S"]) + 1 if "ExclusiveStartKey" in kwargs else 0
        page = ids[start:start + self.page_size]
        result = {"Items": [{"tenant_id": {"S": tid}} for tid in page]}
        if start + self.page_size < len(ids):
            result["LastEvaluatedKey"] = {"tenant_id": {"S": page[-1]}}
        return result

    def put_item(self, **kwargs):
        self.calls.append(("put_item", kwargs)); self._table(kwargs)
        assert kwargs["ConditionExpression"] == ddb.CREATE_CONDITION
        tenant_id = kwargs["Item"]["tenant_id"]["S"]
        if tenant_id in self.items:
            raise ConditionFailed()
        self.items[tenant_id] = copy.deepcopy(kwargs["Item"])
        return {}

    def update_item(self, **kwargs):
        self.calls.append(("update_item", kwargs)); self._table(kwargs)
        if self.before_update:
            hook, self.before_update = self.before_update, None
            hook()
        tenant_id = kwargs["Key"]["tenant_id"]["S"]
        item = self.items.get(tenant_id)
        names, values = kwargs["ExpressionAttributeNames"], kwargs["ExpressionAttributeValues"]
        update, condition = kwargs["UpdateExpression"], kwargs["ConditionExpression"]
        if update == ddb.CHANGE_UPDATE:
            assert condition == ddb.CHANGE_CONDITION and names == {"#record": "record", "#revision": "revision"}
            if item is None or item["revision"]["N"] != values[":expected"]["N"]:
                raise ConditionFailed()
            item["record"] = values[":record"]; item["revision"] = values[":next"]
            return {}
        assert names["#usage"] == "usage"
        month = names["#month"]
        if update == ddb.COUNT_UPDATE:
            assert condition in (ddb.COUNT_CONDITION, ddb.COUNT_LIMITED_CONDITION)
            assert kwargs["ReturnValues"] == "UPDATED_NEW"
            if item is None:
                raise ConditionFailed()
            used = int(item["usage"]["M"].get(month, {"N": "0"})["N"])
            if condition == ddb.COUNT_LIMITED_CONDITION and month in item["usage"]["M"] and not used < int(values[":limit"]["N"]):
                raise ConditionFailed()
            item["usage"]["M"][month] = {"N": str(used + int(values[":one"]["N"]))}
            return {"Attributes": {"usage": {"M": {month: dict(item["usage"]["M"][month])}}}}
        if update == ddb.RELEASE_UPDATE:
            assert condition == ddb.RELEASE_CONDITION
            if item is None or month not in item["usage"]["M"] or not int(item["usage"]["M"][month]["N"]) > 0:
                raise ConditionFailed()
            item["usage"]["M"][month] = {"N": str(int(item["usage"]["M"][month]["N"]) - 1)}
            return {}
        raise AssertionError("Unexpected update expression: " + update)


@pytest.fixture
def fake():
    return FakeDynamoDB()


@pytest.fixture
def store(fake):
    store = DynamoDBTenantStore("guardoath-tenants", fake)
    for record in demo_records(key_secret=SECRET):
        store.create(record)
    return store


def customer(**fields):
    _, digest = new_key("acme")
    return TenantRecord(**{"tenant_id": "acme", "display_name": "Acme", "key_hashes": (digest,), "check_secrets": "flag",
                           "monthly_allowance": 2, **fields})


# Item format


@pytest.mark.parametrize("record", [*demo_records(key_secret=SECRET), customer(usage={"2026-09": 7, MONTH: 1})],
                         ids=lambda r: r.tenant_id)
def test_every_record_survives_the_item_format_unchanged(record):
    item = to_item(record)
    assert set(item) == {"tenant_id", "revision", "record", "usage"}
    assert from_item(item) == record


def test_the_item_keeps_usage_and_revision_out_of_the_record_json():
    item = to_item(customer(usage={MONTH: 3}, revision=4))
    assert '"usage"' not in item["record"]["S"] and '"revision"' not in item["record"]["S"]
    assert item["usage"] == {"M": {MONTH: {"N": "3"}}} and item["revision"] == {"N": "4"}


def test_a_new_record_always_has_a_usage_map(fake):
    DynamoDBTenantStore("guardoath-tenants", fake).create(customer())
    assert fake.items["acme"]["usage"] == {"M": {}}


# Reading and creating


def test_created_records_read_back_with_revision_one(store):
    record = store.get("demo1")
    assert record.revision == 1 and record.updated_at and record.demo is True
    assert record.key_hashes == (key_hash(demo_key("demo1", SECRET)),)
    assert store.get("nobody") is None


def test_creating_an_existing_tenant_is_refused(store):
    with pytest.raises(TenantConflict, match="already exists"):
        store.create(demo_records(key_secret=SECRET)[0])


def test_tenant_ids_read_every_page_in_order(store, fake):
    assert store.tenant_ids() == tuple(sorted(f"demo{n}" for n in range(1, 11)))
    assert [name for name, _ in fake.calls].count("scan") == 4  # 10 ids, 3 per page


# Changing a record


def test_a_change_bumps_the_revision_and_keeps_usage(store):
    store.count_check("demo1", MONTH)
    updated = store.update_checks("demo1", {"check_pii": "flag", "default_profile": "pii-v1"}, 1)
    assert updated.revision == 2 and updated.check_pii == "flag" and updated.default_profile == "pii-v1"
    assert updated.used(MONTH) == 1 and store.get("demo1") == updated


def test_a_stale_revision_is_refused_and_nothing_changes(store):
    before = store.get("demo1")
    store.update_checks("demo1", {"check_pii": "flag"}, 1)
    with pytest.raises(TenantConflict):
        store.update_checks("demo1", {"check_pii": None}, 1)
    assert store.get("demo1").check_pii == "flag" and store.get("demo1").revision == before.revision + 1


def test_a_change_racing_another_change_is_refused(store, fake):
    # Another writer saves between this store's read and its conditional write.
    fake.before_update = lambda: store.update_checks("demo1", {"check_text_security": None}, 1)
    with pytest.raises(TenantConflict):
        store.update_checks("demo1", {"check_pii": "flag"}, 1)
    record = store.get("demo1")
    assert record.revision == 2 and record.check_text_security is None and record.check_pii == "block"


def test_a_count_during_a_change_is_not_lost(store, fake):
    fake.before_update = lambda: store.count_check("demo1", MONTH)
    updated = store.update_checks("demo1", {"check_pii": "flag"}, 1)
    assert updated.used(MONTH) == 1 and store.get("demo1").used(MONTH) == 1


@pytest.mark.parametrize("changes", [{"monthly_allowance": 5}, {"usage": {}}, {"key_hashes": ()}, {"revision": 9}])
def test_account_fields_cannot_be_changed(store, changes):
    with pytest.raises(ValueError, match="Only check fields"):
        store.update_checks("demo1", changes, 1)


def test_changing_a_missing_tenant_is_not_found(store):
    with pytest.raises(TenantNotFound):
        store.update_checks("nobody", {"check_pii": "flag"}, 1)
    with pytest.raises(TenantNotFound):
        store.set_key_hashes("nobody", ())


def test_key_hashes_are_replaced_and_the_revision_bumped(store):
    _, digest = new_key("demo1")
    updated = store.set_key_hashes("demo1", (digest,))
    assert updated.key_hashes == (digest,) and updated.revision == 2


# Counting checks


def test_checks_are_counted_per_month(store):
    assert store.count_check("demo1", MONTH) == (1, False)
    assert store.count_check("demo1", MONTH) == (2, False)
    assert store.count_check("demo1", "2026-11") == (1, False)
    record = store.get("demo1")
    assert record.usage == {MONTH: 2, "2026-11": 1} and record.revision == 1


def test_a_hard_limit_stops_counting_at_the_allowance(fake):
    store = DynamoDBTenantStore("guardoath-tenants", fake)
    store.create(customer(overage="block"))
    assert [store.count_check("acme", MONTH) for _ in range(2)] == [(1, False), (2, False)]
    with pytest.raises(UsageLimitReached):
        store.count_check("acme", MONTH)
    assert store.get("acme").used(MONTH) == 2


def test_a_zero_allowance_hard_limit_refuses_the_first_check(fake):
    store = DynamoDBTenantStore("guardoath-tenants", fake)
    store.create(customer(overage="block", monthly_allowance=0))
    # The month is not on the record yet, so the store must still refuse it.
    with pytest.raises(UsageLimitReached):
        store.count_check("acme", MONTH)
    assert store.get("acme").used(MONTH) == 0


def test_customers_are_counted_past_their_allowance(fake):
    store = DynamoDBTenantStore("guardoath-tenants", fake)
    store.create(customer())
    assert [store.count_check("acme", MONTH) for _ in range(3)] == [(1, False), (2, False), (3, True)]


def test_counting_a_missing_tenant_is_not_found(store):
    with pytest.raises(TenantNotFound):
        store.count_check("nobody", MONTH)


def test_a_count_for_a_tenant_deleted_meanwhile_is_not_found(store, fake):
    fake.before_update = lambda: fake.items.pop("demo1")
    with pytest.raises(TenantNotFound):
        store.count_check("demo1", MONTH)


def test_a_released_check_is_given_back_but_never_below_zero(store):
    store.count_check("demo1", MONTH)
    store.release_check("demo1", MONTH)
    store.release_check("demo1", MONTH)
    store.release_check("demo1", "2026-12")
    assert store.get("demo1").usage == {MONTH: 0}


def test_other_dynamodb_errors_are_not_hidden(store, fake):
    class Throttled(Exception):
        response = {"Error": {"Code": "ProvisionedThroughputExceededException"}}

    def throttled(**kwargs):
        raise Throttled()

    fake.update_item = throttled
    with pytest.raises(Throttled):
        store.count_check("demo1", MONTH)
    with pytest.raises(Throttled):
        store.release_check("demo1", MONTH)
    with pytest.raises(Throttled):
        store.update_checks("demo1", {"check_pii": "flag"}, 1)


# Demo users


def test_missing_demo_users_are_created(fake):
    store = DynamoDBTenantStore("guardoath-tenants", fake)
    ensure_demo_records(store, demo_records(key_secret=SECRET))
    assert store.tenant_ids() == tuple(sorted(f"demo{n}" for n in range(1, 11)))


def test_existing_demo_users_keep_their_checks_and_usage(store):
    store.update_checks("demo1", {"check_pii": "flag"}, 1)
    store.count_check("demo1", MONTH)
    ensure_demo_records(store, demo_records(key_secret=SECRET))
    record = store.get("demo1")
    assert record.check_pii == "flag" and record.used(MONTH) == 1 and record.revision == 2


def test_a_new_demo_secret_replaces_the_key_hash_only(store):
    store.update_checks("demo1", {"check_pii": "flag"}, 1)
    other = "another-dynamodb-secret-" + "t" * 32
    ensure_demo_records(store, demo_records(key_secret=other))
    record = store.get("demo1")
    assert record.key_hashes == (key_hash(demo_key("demo1", other)),) and record.check_pii == "flag"


# The hosted API on the DynamoDB store


def test_the_hosted_api_runs_on_the_dynamodb_store(store):
    pytest.importorskip("tldextract")
    pytest.importorskip("phonenumbers")
    from fastapi.testclient import TestClient
    from guardoath.app import create_app, demo_engine
    from guardoath.checkers import default_checkers
    from guardoath.config import load_policies
    from guardoath.hosted import TenantService
    tenants = TenantService(store, default_checkers(enable_pii=True), SECRET, tuple(load_policies("policies/playground.json")))
    client = TestClient(create_app(engine=demo_engine(), credentials={}, tenants=tenants))
    token = client.post("/v1/demo-users/demo2/key", json={}).json()["api_key"]
    headers = {"Authorization": "Bearer " + token}
    body = {"application_id": "demo2", "content": "Please email jane@example.com about my order."}
    assert client.post("/v1/checks/input", headers=headers, json=body).json()["effective_action"] == "block"
    tenant = client.get("/v1/applications/current", headers=headers).json()["tenant"]
    assert tenant["used"] == 1 and tenant["revision"] == 1
    saved = client.post("/v1/tenants/current/checks", headers=headers,
                        json={"revision": 1, "changes": {"default_profile": "secrets-v1"}})
    assert saved.status_code == 200 and saved.json()["default_profile"] == "secrets-v1" and saved.json()["used"] == 1
    assert client.post("/v1/checks/input", headers=headers, json=body).json()["policy_version"] == "secrets-v1"
    stale = client.post("/v1/tenants/current/checks", headers=headers, json={"revision": 1, "changes": {"check_pii": None}})
    assert stale.status_code == 409
    assert store.get("demo2").used(ddb.usage_month()) == 2


def test_the_demo_user_list_is_read_from_the_store_at_most_once_a_minute(store, fake, monkeypatch):
    from guardoath import hosted
    from guardoath.hosted import TenantService
    now = [1000.0]
    monkeypatch.setattr(hosted, "monotonic", lambda: now[0])
    tenants = TenantService(store, {}, SECRET, demo_list_seconds=60.0)
    first = tenants.demo_users()
    scans = [name for name, _ in fake.calls].count("scan")
    now[0] += 59
    assert tenants.demo_users() == first and [name for name, _ in fake.calls].count("scan") == scans
    now[0] += 2
    tenants.demo_users()
    assert [name for name, _ in fake.calls].count("scan") > scans
    assert len(first) == 10


# Environment


def test_the_dynamodb_store_needs_a_table_name(monkeypatch):
    from guardoath.hosted import TenantService
    monkeypatch.setenv("GUARDOATH_TENANT_STORE", "dynamodb")
    monkeypatch.delenv("GUARDOATH_TENANT_TABLE", raising=False)
    monkeypatch.delenv("GUARDOATH_DEMO_KEY_SECRET", raising=False)
    monkeypatch.delenv("GUARDOATH_TENANT_PROFILE_FILE", raising=False)
    with pytest.raises(ValueError, match="requires GUARDOATH_TENANT_TABLE"):
        TenantService.from_environment({})
    with pytest.raises(ValueError, match="table name is required"):
        DynamoDBTenantStore("", FakeDynamoDB())


def test_the_store_is_built_from_the_environment_without_calling_aws(monkeypatch):
    pytest.importorskip("boto3")
    monkeypatch.setenv("GUARDOATH_TENANT_TABLE", "guardoath-tenants")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    store = DynamoDBTenantStore.from_environment()
    assert store.table_name == "guardoath-tenants"


# Every request matches the DynamoDB API model (needs the aws extra)


def test_every_request_is_valid_for_the_dynamodb_api(monkeypatch):
    boto3 = pytest.importorskip("boto3")
    from botocore.stub import Stubber
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    client = boto3.client("dynamodb", region_name="us-east-1")
    store = DynamoDBTenantStore("guardoath-tenants", client)
    record = customer(overage="block")
    item = to_item(record.model_copy(update={"revision": 1}))
    with Stubber(client) as stub:
        stub.add_response("put_item", {})
        store.create(record)
        stub.add_response("get_item", {"Item": item})
        stub.add_response("update_item", {})
        stub.add_response("get_item", {"Item": {**item, "revision": {"N": "2"}}})
        store.update_checks("acme", {"check_pii": "flag"}, 1)
        stub.add_response("get_item", {"Item": item})
        stub.add_response("update_item", {"Attributes": {"usage": {"M": {MONTH: {"N": "1"}}}}})
        assert store.count_check("acme", MONTH) == (1, False)
        stub.add_response("update_item", {})
        store.release_check("acme", MONTH)
        stub.add_response("scan", {"Items": [{"tenant_id": {"S": "acme"}}]})
        assert store.tenant_ids() == ("acme",)
        stub.assert_no_pending_responses()


# Against a real table (opt-in)


@pytest.mark.skipif(not os.getenv("GUARDOATH_DYNAMODB_TEST_TABLE"),
                    reason="Set GUARDOATH_DYNAMODB_TEST_TABLE (and AWS credentials) to run against a real table")
def test_the_store_against_a_real_table():
    boto3 = pytest.importorskip("boto3")
    table = os.environ["GUARDOATH_DYNAMODB_TEST_TABLE"]
    client = boto3.client("dynamodb")
    store = DynamoDBTenantStore(table, client)
    tenant_id = "itest-" + random_secrets.token_hex(6)
    record = customer(overage="block", monthly_allowance=2).model_copy(update={"tenant_id": tenant_id})
    try:
        store.create(record)
        assert store.get(tenant_id).revision == 1
        assert store.update_checks(tenant_id, {"check_pii": "flag"}, 1).revision == 2
        with pytest.raises(TenantConflict):
            store.update_checks(tenant_id, {"check_pii": None}, 1)
        assert [store.count_check(tenant_id, MONTH) for _ in range(2)] == [(1, False), (2, False)]
        with pytest.raises(UsageLimitReached):
            store.count_check(tenant_id, MONTH)
        store.release_check(tenant_id, MONTH)
        assert store.get(tenant_id).used(MONTH) == 1 and tenant_id in store.tenant_ids()
    finally:
        client.delete_item(TableName=table, Key={"tenant_id": {"S": tenant_id}})
