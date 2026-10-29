"""Single-use override grant persistence and tenant isolation tests."""
from concurrent.futures import ThreadPoolExecutor
from time import time

from guardoath.sqlite_override_grants import SqliteOverrideGrants


def test_grant_consumed_once_and_survives_restart(tmp_path):
    path = tmp_path / "overrides.sqlite3"
    grants = SqliteOverrideGrants(path)
    grants.issue(token="random-test-token", tenant_id="tenant-a",
                 application_id="app-a", content_digest="content-hash",
                 expires_at=int(time()) + 300)
    reopened = SqliteOverrideGrants(path)
    args = dict(token="random-test-token", tenant_id="tenant-a",
                application_id="app-a", content_digest="content-hash")
    assert not reopened.consume(**{**args, "tenant_id": "tenant-b"})
    assert not reopened.consume(**{**args, "content_digest": "changed"})
    assert reopened.consume(**args)
    assert not reopened.consume(**args)


def test_concurrent_grant_consumed_at_most_once(tmp_path):
    grants = SqliteOverrideGrants(tmp_path / "overrides.sqlite3")
    grants.issue(token="another-test-token", tenant_id="tenant-a",
                 application_id="app-a", content_digest="digest",
                 expires_at=int(time()) + 300)
    args = dict(token="another-test-token", tenant_id="tenant-a",
                application_id="app-a", content_digest="digest")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: grants.consume(**args), range(30)))
    assert sum(results) == 1
