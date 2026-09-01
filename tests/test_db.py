from nightshift import db


def test_connect_makes_the_three_tables(tmp_path):
    conn = db.connect(tmp_path / "state.db")
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"runs", "items", "jobs"} <= names


def test_connect_runs_twice_without_error(tmp_path):
    path = tmp_path / "state.db"
    db.connect(path).close()
    conn = db.connect(path)
    assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] == 0


def test_connect_adds_excerpt_to_a_database_from_before_it_existed(tmp_path):
    """A live state.db already has an `items` table with no `excerpt`
    column. CREATE TABLE IF NOT EXISTS skips it, so connect() must add the
    column itself or the next insert crashes."""
    import sqlite3
    path = tmp_path / "state.db"
    old = sqlite3.connect(path)
    old.execute("""CREATE TABLE items (
        id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, created_at TEXT,
        bucket TEXT, title TEXT, body TEXT, source_url TEXT, opened_at TEXT)""")
    old.commit()
    old.close()

    conn = db.connect(path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
    assert "excerpt" in cols


def test_connect_adds_the_life_columns_to_an_old_items_table(tmp_path):
    """The life of a task, 2026-08-31: a live database predates `state`,
    `closed_at` and `snoozed_until`. Without this the next scheduled cycle
    crashes on the first insert."""
    import sqlite3
    path = tmp_path / "state.db"
    old = sqlite3.connect(path)
    old.execute("""CREATE TABLE items (
        id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, created_at TEXT,
        bucket TEXT, title TEXT, body TEXT, source_url TEXT, opened_at TEXT,
        excerpt TEXT)""")
    old.commit()
    old.close()

    conn = db.connect(path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
    assert {"state", "closed_at", "snoozed_until"} <= cols
    conn.execute("INSERT INTO items (run_id, created_at, bucket, title)"
                 " VALUES (1, 'x', 'needs_you', 't')")
    assert conn.execute("SELECT state FROM items").fetchone()[0] == "pending"


def test_connect_makes_the_events_table(tmp_path):
    conn = db.connect(tmp_path / "state.db")
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "events" in names


def test_connect_makes_the_projects_table(tmp_path):
    conn = db.connect(tmp_path / "state.db")
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "projects" in names


def test_connect_adds_the_schedule_columns_to_an_old_jobs_table(tmp_path):
    """Tasks and projects, 2026-09-01: a live database predates
    `project_id`, `schedule`, `template_id` and `next_run` on `jobs`.
    Without this the next scheduled cycle crashes at 06:30."""
    import sqlite3
    path = tmp_path / "state.db"
    old = sqlite3.connect(path)
    old.execute("""CREATE TABLE jobs (
        id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, prompt TEXT NOT NULL,
        state TEXT NOT NULL, question TEXT, answer TEXT, result_path TEXT)""")
    old.commit()
    old.close()

    conn = db.connect(path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
    assert {"project_id", "schedule", "template_id", "next_run"} <= cols
    conn.execute("INSERT INTO jobs (created_at, prompt, state)"
                 " VALUES ('x', 'p', 'queued')")
    assert conn.execute("SELECT schedule FROM jobs").fetchone()[0] == "once"
