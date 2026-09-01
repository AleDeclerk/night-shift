import datetime as dt

from nightshift import db, quota

MON = dt.datetime(2026, 8, 24, 9, 0)   # Monday
WED = dt.datetime(2026, 8, 26, 9, 0)   # same week
NEXT_MON = dt.datetime(2026, 8, 31, 9, 0)  # next week


def _run(conn, when, cost):
    conn.execute(
        "INSERT INTO runs (started_at, kind, ok, cost_usd) VALUES (?,?,1,?)",
        (when.isoformat(), "mail", cost))
    conn.commit()


def test_an_empty_week_lets_the_system_run(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    assert quota.may_run(conn, now=WED, ceiling_usd=5.0).allowed is True


def test_the_ceiling_stops_the_system(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    _run(conn, MON, 3.0)
    _run(conn, WED, 2.5)
    decision = quota.may_run(conn, now=WED, ceiling_usd=5.0)
    assert decision.allowed is False
    assert "5.5" in decision.reason and "5.0" in decision.reason


def test_last_week_does_not_count(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    _run(conn, MON, 9.0)
    assert quota.may_run(conn, now=NEXT_MON, ceiling_usd=5.0).allowed is True


def test_the_ceiling_comes_from_the_environment(monkeypatch):
    """`run-cycle.py`, `tick.py` and the page each held their own copy of
    20.0, and the page ignored the variable that the other two read. The
    budget card told the user to raise it, and the page did not listen."""
    monkeypatch.setenv("NIGHTSHIFT_CEILING_USD", "45.0")
    assert quota.ceiling_usd() == 45.0


def test_the_ceiling_falls_back_to_twenty(monkeypatch):
    monkeypatch.delenv("NIGHTSHIFT_CEILING_USD", raising=False)
    assert quota.ceiling_usd() == 20.0


def test_a_ceiling_that_is_not_a_number_does_not_stop_the_cycle(monkeypatch):
    """A bad value in a plist would crash the scheduled cycle at 06:30."""
    monkeypatch.setenv("NIGHTSHIFT_CEILING_USD", "twenty")
    assert quota.ceiling_usd() == 20.0
