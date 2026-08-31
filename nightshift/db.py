"""SQLite state. This module holds no business rules."""
import pathlib
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id          INTEGER PRIMARY KEY,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  kind        TEXT NOT NULL,
  ok          INTEGER,
  cost_usd    REAL NOT NULL DEFAULT 0,
  error       TEXT
);
CREATE TABLE IF NOT EXISTS items (
  id         INTEGER PRIMARY KEY,
  run_id     INTEGER NOT NULL REFERENCES runs(id),
  created_at TEXT NOT NULL,
  bucket     TEXT NOT NULL,
  title      TEXT NOT NULL,
  body       TEXT,
  source_url TEXT,
  opened_at  TEXT,
  excerpt    TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
  id          INTEGER PRIMARY KEY,
  created_at  TEXT NOT NULL,
  prompt      TEXT NOT NULL,
  state       TEXT NOT NULL,
  question    TEXT,
  answer      TEXT,
  result_path TEXT
);
CREATE TABLE IF NOT EXISTS probes (
  id       INTEGER PRIMARY KEY,
  engine   TEXT NOT NULL,
  at       TEXT NOT NULL,
  ok       INTEGER NOT NULL,
  can_mail INTEGER,
  cost_usd REAL NOT NULL DEFAULT 0,
  detail   TEXT
);
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def connect(path: pathlib.Path) -> sqlite3.Connection:
    """Open the database and make the schema if it is absent."""
    # check_same_thread=False: FastAPI runs sync routes in a thread pool.
    # One user, one process, so the risk of a race is small.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn
