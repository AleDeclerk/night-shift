import datetime as dt
import json

from nightshift import db, quota, usage

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


# --- the real quota, read from the CLI and stored -----------------------

NOW = dt.datetime(2026, 9, 1, 18, 0)
READING = usage.Usage(week_pct=4,
                      week_resets=dt.datetime(2026, 9, 8, 5, 59),
                      session_pct=35,
                      session_resets=dt.datetime(2026, 9, 1, 20, 39))


def _answers(monkeypatch, reading):
    """The CLI, replaced. A page load and a test never shell out."""
    monkeypatch.setattr(quota.usage, "read", lambda **kw: reading)


def test_read_usage_stores_the_four_numbers(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "s.db")
    _answers(monkeypatch, READING)

    got = quota.read_usage(conn, cwd=tmp_path, now=NOW)

    assert got == READING
    row = conn.execute(
        "SELECT * FROM events WHERE kind='usage_read'").fetchone()
    stored = json.loads(row["detail"])
    assert stored["week_pct"] == 4
    assert stored["session_pct"] == 35
    assert stored["week_resets"] == "2026-09-08T05:59:00"
    assert stored["session_resets"] == "2026-09-01T20:39:00"


def test_last_usage_rebuilds_the_reading_from_the_event(tmp_path, monkeypatch):
    """A page load must never shell out, and one `/usage` call takes about
    three seconds. The tick reads and the page rebuilds."""
    conn = db.connect(tmp_path / "s.db")
    _answers(monkeypatch, READING)
    quota.read_usage(conn, cwd=tmp_path, now=NOW)

    back = quota.last_usage(conn, NOW + dt.timedelta(minutes=30))

    assert back == READING
    assert back.allowance_pct(NOW, reserve_per_day=10) == 26


def test_a_stale_reading_is_no_reading(tmp_path, monkeypatch):
    """The week moves. A reading of two hours ago says nothing about the
    room of now, so the governor falls back to counting."""
    conn = db.connect(tmp_path / "s.db")
    _answers(monkeypatch, READING)
    quota.read_usage(conn, cwd=tmp_path, now=NOW)

    assert quota.last_usage(conn, NOW + dt.timedelta(minutes=91)) is None


def test_a_reading_of_a_week_that_ended_is_no_reading(tmp_path, monkeypatch):
    """The reset moment ends the week that the reading describes.

    A reading made minutes before the reset stays fresh for an hour and a
    half after it. Past the reset `days_left` gives zero, zero empties the
    reserve, and the allowance jumps to almost the whole week. So a reading
    that outlives its week reads as room that the system does not have.
    """
    conn = db.connect(tmp_path / "s.db")
    _answers(monkeypatch, READING)
    quota.read_usage(conn, cwd=tmp_path,
                     now=READING.week_resets - dt.timedelta(minutes=10))

    after = READING.week_resets + dt.timedelta(minutes=31)

    assert quota.last_usage(conn, after) is None


def test_last_usage_gives_none_when_nothing_was_ever_read(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    assert quota.last_usage(conn, NOW) is None


def test_a_failed_read_records_why_and_gives_none(tmp_path, monkeypatch):
    """Rule 1 of the design: a blank `/usage` is not permission. It leaves a
    record, so the page can say the system is counting again."""
    conn = db.connect(tmp_path / "s.db")
    _answers(monkeypatch, None)

    assert quota.read_usage(conn, cwd=tmp_path, now=NOW) is None

    row = conn.execute(
        "SELECT * FROM events WHERE kind='usage_unavailable'").fetchone()
    assert row is not None and row["detail"]
    assert quota.last_usage(conn, NOW) is None


def test_read_usage_asks_the_cli_with_the_workspace_and_the_clock(
        tmp_path, monkeypatch):
    seen = {}

    def spy(**kw):
        seen.update(kw)
        return READING

    monkeypatch.setattr(quota.usage, "read", spy)
    quota.read_usage(conn := db.connect(tmp_path / "s.db"), cwd=tmp_path,
                     now=NOW)
    assert seen["cwd"] == tmp_path
    assert seen["now"] == NOW
    assert conn is not None
