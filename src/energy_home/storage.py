import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS homes (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, address TEXT NOT NULL DEFAULT '',
 kind TEXT NOT NULL DEFAULT 'apartment', timezone TEXT NOT NULL DEFAULT 'Europe/Minsk'
);
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, home_id INTEGER NOT NULL REFERENCES homes(id),
 name TEXT NOT NULL, login TEXT NOT NULL COLLATE NOCASE UNIQUE, password TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('owner','family')), active INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rooms (
 id INTEGER PRIMARY KEY, home_id INTEGER NOT NULL REFERENCES homes(id),
 name TEXT NOT NULL COLLATE NOCASE, UNIQUE(home_id,name)
);
CREATE TABLE IF NOT EXISTS appliances (
 id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL REFERENCES rooms(id),
 name TEXT NOT NULL, rated_w REAL NOT NULL CHECK(rated_w>0),
 flexible INTEGER NOT NULL DEFAULT 0, essential INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS devices (
 id INTEGER PRIMARY KEY, appliance_id INTEGER NOT NULL UNIQUE REFERENCES appliances(id),
 serial TEXT NOT NULL COLLATE NOCASE UNIQUE,
 kind TEXT NOT NULL CHECK(kind IN ('meter','socket','sensor')),
 state TEXT NOT NULL CHECK(state IN ('off','idle','running','flexible','emergency','retired')),
 critical_w REAL NOT NULL CHECK(critical_w>0), counted INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tariffs (
 id INTEGER PRIMARY KEY, home_id INTEGER NOT NULL REFERENCES homes(id),
 effective_at TEXT NOT NULL, day_rate REAL NOT NULL CHECK(day_rate>=0),
 night_rate REAL NOT NULL CHECK(night_rate>=0),
 day_start INTEGER NOT NULL CHECK(day_start BETWEEN 0 AND 23),
 night_start INTEGER NOT NULL CHECK(night_start BETWEEN 0 AND 23)
);
CREATE INDEX IF NOT EXISTS tariffs_home_time ON tariffs(home_id,effective_at);
CREATE TABLE IF NOT EXISTS settings (
 home_id INTEGER PRIMARY KEY REFERENCES homes(id), monthly_budget REAL NOT NULL DEFAULT 100,
 daily_kwh REAL NOT NULL DEFAULT 25, notify_80 INTEGER NOT NULL DEFAULT 1,
 auto_protect INTEGER NOT NULL DEFAULT 1, scenario TEXT NOT NULL DEFAULT 'manual'
);
CREATE TABLE IF NOT EXISTS readings (
 id INTEGER PRIMARY KEY, device_id INTEGER NOT NULL REFERENCES devices(id),
 measured_at TEXT NOT NULL, interval_seconds INTEGER NOT NULL CHECK(interval_seconds>0),
 power_w REAL NOT NULL CHECK(power_w>=0), energy_kwh REAL NOT NULL CHECK(energy_kwh>=0),
 cost REAL NOT NULL CHECK(cost>=0), tariff_id INTEGER NOT NULL REFERENCES tariffs(id),
 UNIQUE(device_id,measured_at)
);
CREATE INDEX IF NOT EXISTS readings_device_time ON readings(device_id,measured_at);
CREATE TABLE IF NOT EXISTS reading_segments (
 id INTEGER PRIMARY KEY, reading_id INTEGER NOT NULL REFERENCES readings(id),
 start_at TEXT NOT NULL, end_at TEXT NOT NULL, energy_kwh REAL NOT NULL, cost REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS segments_time ON reading_segments(start_at,end_at);
CREATE TABLE IF NOT EXISTS anomalies (
 id INTEGER PRIMARY KEY, device_id INTEGER NOT NULL REFERENCES devices(id),
 reading_id INTEGER NOT NULL UNIQUE REFERENCES readings(id), kind TEXT NOT NULL,
 deviation REAL NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
 id INTEGER PRIMARY KEY, home_id INTEGER NOT NULL REFERENCES homes(id),
 kind TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL,
 dedupe_key TEXT, UNIQUE(home_id,dedupe_key)
);
CREATE TABLE IF NOT EXISTS notification_reads (
 notification_id INTEGER NOT NULL REFERENCES notifications(id),
 user_id INTEGER NOT NULL REFERENCES users(id), PRIMARY KEY(notification_id,user_id)
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, home_id INTEGER REFERENCES homes(id), user_id INTEGER REFERENCES users(id),
 device_id INTEGER REFERENCES devices(id), kind TEXT NOT NULL,
 message TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
 id INTEGER PRIMARY KEY, home_id INTEGER NOT NULL REFERENCES homes(id),
 user_id INTEGER NOT NULL REFERENCES users(id), start_at TEXT NOT NULL, end_at TEXT NOT NULL,
 format TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS buildings (
 id INTEGER PRIMARY KEY, home_id INTEGER NOT NULL UNIQUE REFERENCES homes(id), name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS building_apartments (
 id INTEGER PRIMARY KEY, building_id INTEGER NOT NULL REFERENCES buildings(id),
 name TEXT NOT NULL, area REAL NOT NULL CHECK(area>0),
 residents INTEGER NOT NULL CHECK(residents>0),
 own_kwh REAL NOT NULL DEFAULT 0 CHECK(own_kwh>=0), UNIQUE(building_id,name)
);
PRAGMA user_version = 1;
"""


class Database:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.executescript(SCHEMA)

    @contextmanager
    def transaction(self):
        with self.connection:
            yield self

    def execute(self, sql: str, params=()):
        return self.connection.execute(sql, params)

    def one(self, sql: str, params=()) -> dict | None:
        row = self.execute(sql, params).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, params=()) -> list[dict]:
        return [dict(row) for row in self.execute(sql, params).fetchall()]

    def event(self, home_id, kind, message, *, user_id=None, device_id=None):
        from .domain import now, stamp

        self.execute(
            "INSERT INTO events(home_id,user_id,device_id,kind,message,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (home_id, user_id, device_id, kind, message, stamp(now())),
        )

    def close(self):
        self.connection.close()
