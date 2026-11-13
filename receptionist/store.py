"""Call log: every simulated call or Vapi end-of-call report is stored for the dashboard."""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path


class CallStore:
    def __init__(self, path: str | Path):
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""CREATE TABLE IF NOT EXISTS calls(
            id TEXT PRIMARY KEY, started TEXT, channel TEXT, caller TEXT, phone TEXT, outcome TEXT,
            emergency TEXT, booking_id TEXT, summary TEXT, transcript TEXT, events TEXT, slots TEXT)""")
        self.conn.commit()

    def save(self, *, started: str, channel: str, caller: str | None, phone: str | None, outcome: str,
             emergency: str | None, booking_id: str | None, summary: str, transcript: list, events: list,
             slots: dict, call_id: str | None = None) -> str:
        call_id = call_id or "CA-" + uuid.uuid4().hex[:6].upper()
        self.conn.execute("INSERT OR REPLACE INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          (call_id, started, channel, caller, phone, outcome, emergency, booking_id, summary,
                           json.dumps(transcript), json.dumps(events), json.dumps(slots, default=str)))
        self.conn.commit()
        return call_id

    def all(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM calls ORDER BY started DESC")]

    def get(self, call_id: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        for k in ("transcript", "events", "slots"):
            d[k] = json.loads(d[k])
        return d
