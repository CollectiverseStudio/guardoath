"""Durable local SQLite usage event ledger (not Stripe settlement).

Use a dedicated database path with restricted OS permissions. The host must
authenticate tenants; this module only stores already validated UsageEvent values.
"""
import sqlite3
from pathlib import Path

from valinator.billing import UsageEvent, BillingMode
from valinator.usage_ledger import UsageConflict


class SqliteUsageLedger:
    def __init__(self, path: str | Path):
        self.path = str(path)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS usage_events (
                event_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                payer_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                units INTEGER NOT NULL,
                event_type TEXT NOT NULL
            )""")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def record(self, event: UsageEvent) -> bool:
        if not event.event_id or not event.tenant_id or not event.payer_id:
            raise ValueError("Invalid usage event identity")
        if type(event.units) is not int or event.units < 1:
            raise ValueError("Invalid usage units")
        row = (event.event_id, event.tenant_id, event.payer_id,
               event.mode.value, event.units, event.event_type)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT event_id,tenant_id,payer_id,mode,units,event_type "
                "FROM usage_events WHERE event_id=?", (event.event_id,)
            ).fetchone()
            if existing is not None:
                if existing != row:
                    raise UsageConflict("Conflicting usage event ID")
                return False
            db.execute(
                "INSERT INTO usage_events "
                "(event_id,tenant_id,payer_id,mode,units,event_type) "
                "VALUES (?,?,?,?,?,?)", row)
            return True

    def events_for(self, tenant_id: str) -> tuple[UsageEvent, ...]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT event_id,tenant_id,payer_id,mode,units,event_type "
                "FROM usage_events WHERE tenant_id=? ORDER BY rowid",
                (tenant_id,),
            ).fetchall()
        return tuple(UsageEvent(event_id=r[0], tenant_id=r[1],
                                payer_id=r[2], mode=BillingMode(r[3]),
                                units=r[4], event_type=r[5]) for r in rows)
