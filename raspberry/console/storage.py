import json
import sqlite3
import time
import uuid
from pathlib import Path


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts REAL, session TEXT, source TEXT, type TEXT, data TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS baselines (kind TEXT PRIMARY KEY, data TEXT)")
        self.db.commit()
        self.session = uuid.uuid4().hex

    def add(self, source, kind, data):
        stamp = time.time()
        cur = self.db.execute("INSERT INTO events(ts,session,source,type,data) VALUES(?,?,?,?,?)",
                              (stamp, self.session, source, kind, json.dumps(data, ensure_ascii=False)))
        # Keep disk use bounded on a kiosk running indefinitely.
        if cur.lastrowid % 250 == 0:
            self.db.execute("DELETE FROM events WHERE id <= ?", (cur.lastrowid - 20000,))
        self.db.commit()
        return dict(id=cur.lastrowid, ts=stamp, session=self.session, source=source, type=kind, data=data)

    def events(self, after=0, limit=500, session=None):
        clause, values = "id > ?", [after]
        if session:
            clause += " AND session = ?"
            values.append(session)
        values.append(min(max(limit, 1), 20000))
        rows = self.db.execute(f"SELECT id,ts,session,source,type,data FROM events WHERE {clause} ORDER BY id LIMIT ?", values)
        return [dict(id=r[0], ts=r[1], session=r[2], source=r[3], type=r[4], data=json.loads(r[5])) for r in rows]

    def sessions(self):
        rows = self.db.execute("SELECT session, MIN(ts), MAX(ts), COUNT(*) FROM events GROUP BY session ORDER BY MAX(ts) DESC")
        return [dict(id=r[0], start=r[1], end=r[2], count=r[3]) for r in rows]

    def baseline(self, kind):
        row = self.db.execute("SELECT data FROM baselines WHERE kind=?", (kind,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_baseline(self, kind, data):
        self.db.execute("INSERT OR REPLACE INTO baselines(kind,data) VALUES(?,?)", (kind, json.dumps(data)))
        self.db.commit()

    def close(self):
        self.db.close()
