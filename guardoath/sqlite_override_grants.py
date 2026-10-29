"""Single-use override grants stored transactionally in SQLite.

This is a local grant store. It does not issue approval, authenticate reviewers,
encrypt PHI, or authorize downstream AI egress.
"""
import hashlib
import sqlite3
from pathlib import Path
from time import time


def _digest(token: str) -> str:
    if not token:
        raise ValueError("Override token required")
    return hashlib.sha256(token.encode()).hexdigest()


class SqliteOverrideGrants:
    def __init__(self, path: str | Path):
        self.path = str(path)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS override_grants (
                token_digest TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                application_id TEXT NOT NULL,
                content_digest TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                consumed INTEGER NOT NULL DEFAULT 0
            )""")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def issue(self, *, token: str, tenant_id: str, application_id: str,
              content_digest: str, expires_at: int) -> None:
        if (not tenant_id or not application_id or not content_digest
                or type(expires_at) is not int or expires_at <= int(time())):
            raise ValueError("Invalid override grant")
        with self._connect() as db:
            db.execute(
                "INSERT INTO override_grants "
                "(token_digest,tenant_id,application_id,content_digest,expires_at) "
                "VALUES (?,?,?,?,?)",
                (_digest(token), tenant_id, application_id, content_digest, expires_at),
            )

    def consume(self, *, token: str, tenant_id: str, application_id: str,
                content_digest: str) -> bool:
        if not tenant_id or not application_id or not content_digest:
            return False
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "UPDATE override_grants SET consumed=1 WHERE token_digest=? "
                "AND tenant_id=? AND application_id=? AND content_digest=? "
                "AND expires_at>? AND consumed=0",
                (_digest(token), tenant_id, application_id, content_digest, int(time())),
            )
            return cursor.rowcount == 1
