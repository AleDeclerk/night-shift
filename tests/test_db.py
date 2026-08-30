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
