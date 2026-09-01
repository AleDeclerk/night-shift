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
  excerpt    TEXT,
  state         TEXT NOT NULL DEFAULT 'pending',
  closed_at     TEXT,
  snoozed_until TEXT,
  score         INTEGER,
  comment       TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
  id          INTEGER PRIMARY KEY,
  created_at  TEXT NOT NULL,
  prompt      TEXT NOT NULL,
  state       TEXT NOT NULL,
  question    TEXT,
  answer      TEXT,
  result_path TEXT,
  project_id  INTEGER,
  schedule    TEXT NOT NULL DEFAULT 'once',
  template_id INTEGER,
  next_run    TEXT,
  started_at  TEXT
);
CREATE TABLE IF NOT EXISTS projects (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,
  scope      TEXT NOT NULL,          -- personal | veritas
  vault_path TEXT,
  graph_path TEXT,
  active     INTEGER NOT NULL DEFAULT 1,
  merged_into INTEGER
);
CREATE TABLE IF NOT EXISTS project_paths (
  id         INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  path       TEXT NOT NULL UNIQUE,
  graph_path TEXT
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
CREATE TABLE IF NOT EXISTS events (
  id       INTEGER PRIMARY KEY,
  at       TEXT NOT NULL,
  kind     TEXT NOT NULL,
  item_id  INTEGER,
  job_id   INTEGER,
  verb     TEXT,
  engine   TEXT,
  cost_usd REAL NOT NULL DEFAULT 0,
  detail   TEXT
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """CREATE TABLE IF NOT EXISTS never adds a column to a table that
    already exists. Measured on 2026-08-31: the live state.db under
    ~/.night-shift predates `excerpt`, and the next scheduled cycle would
    crash on the first insert without this. The same is true of `state`,
    `closed_at` and `snoozed_until`, added for the life of a task, and of
    `score` and `comment`, added for the feedback that follows it: a live
    database has real rows, and a missing column would crash the next
    scheduled cycle at 06:30.
    """
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
    if "excerpt" not in cols:
        conn.execute("ALTER TABLE items ADD COLUMN excerpt TEXT")
    if "state" not in cols:
        conn.execute(
            "ALTER TABLE items ADD COLUMN state TEXT NOT NULL DEFAULT 'pending'")
    if "closed_at" not in cols:
        conn.execute("ALTER TABLE items ADD COLUMN closed_at TEXT")
    if "snoozed_until" not in cols:
        conn.execute("ALTER TABLE items ADD COLUMN snoozed_until TEXT")
    if "score" not in cols:
        conn.execute("ALTER TABLE items ADD COLUMN score INTEGER")
    if "comment" not in cols:
        conn.execute("ALTER TABLE items ADD COLUMN comment TEXT")

    # Tasks and projects, 2026-09-01: a live jobs table predates the project
    # and the schedule. `projects` itself is a brand-new table, so
    # `CREATE TABLE IF NOT EXISTS` already makes it on an old database; only
    # a column added to a table that already exists needs a line here.
    job_cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
    if "project_id" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN project_id INTEGER")
    if "schedule" not in job_cols:
        conn.execute(
            "ALTER TABLE jobs ADD COLUMN schedule TEXT NOT NULL DEFAULT 'once'")
    if "template_id" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN template_id INTEGER")
    if "next_run" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN next_run TEXT")

    # `started_at`, 2026-09-01: the moment a job entered 'running', so a
    # process that dies without closing it can be told apart from one still
    # at work. A live jobs table predates the column.
    if "started_at" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN started_at TEXT")

    # Several folders, one project, 2026-09-01: `project_paths` is a
    # brand-new table, so `CREATE TABLE IF NOT EXISTS` already makes it on
    # an old database. Only the column added to the existing `projects`
    # table needs a line here.
    proj_cols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)")}
    if "merged_into" not in proj_cols:
        conn.execute("ALTER TABLE projects ADD COLUMN merged_into INTEGER")


def connect(path: pathlib.Path) -> sqlite3.Connection:
    """Open the database and make the schema if it is absent."""
    # check_same_thread=False: FastAPI runs sync routes in a thread pool.
    # One user, one process, so the risk of a race is small.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn
