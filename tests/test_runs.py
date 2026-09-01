import datetime as dt

from nightshift import db, runs


def test_end_stamps_finished_at_with_the_injected_clock(tmp_path):
    """The cascade reads `events.at`, and `runs.finished_at` used the real
    clock even when the caller gave `now`. The two columns of one exit must
    agree on what time it was."""
    conn = db.connect(tmp_path / "s.db")
    when = dt.datetime(2026, 8, 26, 3, 0)
    run_id = runs.start(conn, when, "mail")
    runs.end(conn, run_id, True, cost=1.0, now=when)
    row = conn.execute("SELECT finished_at FROM runs WHERE id=?",
                       (run_id,)).fetchone()
    assert row["finished_at"].startswith("2026-08-26T03:00")
