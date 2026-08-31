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
