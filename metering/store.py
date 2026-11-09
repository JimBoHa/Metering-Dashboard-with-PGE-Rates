"""Own SQLite database only; source databases are opened separately in read-only mode."""
import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from .models import Configuration

class Store:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self.db = sqlite3.connect(self.directory / "metering.sqlite3", check_same_thread=False)
        os.chmod(self.directory / "metering.sqlite3", 0o600)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA busy_timeout=5000;
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS readings (meter_id TEXT NOT NULL, timestamp REAL NOT NULL,
          value REAL NOT NULL, quality TEXT NOT NULL, error TEXT, PRIMARY KEY(meter_id,timestamp));
        CREATE TABLE IF NOT EXISTS expenses (id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tariffs (id TEXT PRIMARY KEY, fetched_at REAL NOT NULL, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, timestamp REAL NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL);
        ''')
        if "stream_id" not in {r[1] for r in self.db.execute("PRAGMA table_info(readings)")}:
            self.db.execute("ALTER TABLE readings ADD COLUMN stream_id TEXT")
        self.db.commit()
        if self.get("configuration") is None: self.set("configuration", Configuration().model_dump())
    def get(self, key, default=None):
        with self.lock:
            r = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default
    def set(self, key, value):
        with self.lock, self.db:
            self.db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key,json.dumps(value, allow_nan=False)))
    def config(self): return Configuration.model_validate(self.get("configuration"))
    def revision(self): return hashlib.sha256(json.dumps({"configuration":self.get("configuration"),"sql_credential_revision":self.get("sql_credential_revision",0)},sort_keys=True).encode()).hexdigest()
    def audit(self, action, details):
        with self.lock, self.db:
            self.db.execute("INSERT INTO audit(timestamp,action,details) VALUES(?,?,?)", (time.time(),action,json.dumps(details)))
    def readings(self, mid, start, end):
        with self.lock:
            before = self.db.execute("SELECT * FROM readings WHERE meter_id=? AND timestamp<=? ORDER BY timestamp DESC LIMIT 1", (mid,start)).fetchall()
            within = self.db.execute("SELECT * FROM readings WHERE meter_id=? AND timestamp>? AND timestamp<? ORDER BY timestamp", (mid,start,end)).fetchall()
            after = self.db.execute("SELECT * FROM readings WHERE meter_id=? AND timestamp>=? ORDER BY timestamp LIMIT 1", (mid,end)).fetchall()
        return [dict(r) for r in before+within+after]
    def add_reading(self, r):
        with self.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO readings(meter_id,timestamp,value,quality,error,stream_id) VALUES(?,?,?,?,?,?)", (r.meter_id,r.timestamp,r.value,r.quality,r.error,r.stream_id))
    def latest(self):
        with self.lock:
            rows=self.db.execute("SELECT r.* FROM readings r JOIN (SELECT meter_id,max(timestamp) t FROM readings GROUP BY meter_id) s ON r.meter_id=s.meter_id AND r.timestamp=s.t").fetchall()
        return {r["meter_id"]:dict(r) for r in rows}
    def expenses(self):
        with self.lock: rows=self.db.execute("SELECT data FROM expenses ORDER BY id").fetchall()
        return [json.loads(r[0]) for r in rows]
    def tariffs(self):
        with self.lock: rows=self.db.execute("SELECT data FROM tariffs ORDER BY fetched_at DESC").fetchall()
        return [json.loads(r[0]) for r in rows]
    def save_tariff(self, data):
        with self.lock,self.db:
            self.db.execute("INSERT OR IGNORE INTO tariffs VALUES(?,?,?)", (data["id"],data["fetched_at"],json.dumps(data)))
    def close(self): self.db.close()
