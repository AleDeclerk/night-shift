import datetime as dt

from nightshift import board, db

NOW = dt.datetime(2026, 8, 31, 12, 0)


def _event(conn, kind, *, item_id=None, job_id=None, verb=None, engine=None,
           cost_usd=0.0, detail=None, at=NOW):
    conn.execute(
        "INSERT INTO events (at, kind, item_id, job_id, verb, engine,"
        " cost_usd, detail) VALUES (?,?,?,?,?,?,?,?)",
        (at.isoformat(), kind, item_id, job_id, verb, engine, cost_usd, detail))
    conn.commit()


# --- reviewed / raised ------------------------------------------------

def test_reviewed_counts_item_found_events_in_the_window(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    _event(conn, "item_found", item_id=1, detail="needs_you")
    _event(conn, "item_found", item_id=2, detail="no_action")
    _event(conn, "draft_written", item_id=1, engine="claude")  # not a review
    got = board.week(conn, now=NOW)
    assert got["reviewed"] == 2


def test_raised_counts_only_needs_you_from_item_found_detail(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    _event(conn, "item_found", item_id=1, detail="needs_you")
    _event(conn, "item_found", item_id=2, detail="needs_you")
    _event(conn, "item_found", item_id=3, detail="no_action")
    got = board.week(conn, now=NOW)
    assert got["reviewed"] == 3
    assert got["raised"] == 2


# --- false_alarm_rate ---------------------------------------------------

def test_false_alarm_rate_is_none_under_five_closed(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    for i, verb in enumerate(["no_era_nada", "listo", "listo", "manana"]):
        _event(conn, "item_closed", item_id=i, verb=verb)
    got = board.week(conn, now=NOW)
    assert got["false_alarm_rate"] is None


def test_false_alarm_rate_is_the_right_share_at_five_or_more(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    verbs = ["no_era_nada", "no_era_nada", "listo", "listo", "lo_hago_yo"]
    for i, verb in enumerate(verbs):
        _event(conn, "item_closed", item_id=i, verb=verb)
    got = board.week(conn, now=NOW)
    assert got["false_alarm_rate"] == 2 / 5


# --- hours_to_close -------------------------------------------------------

def test_hours_to_close_is_the_median_wait(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    _event(conn, "item_found", item_id=1, at=NOW - dt.timedelta(hours=2))
    _event(conn, "item_closed", item_id=1, verb="listo", at=NOW)  # 2h
    _event(conn, "item_found", item_id=2, at=NOW - dt.timedelta(hours=6))
    _event(conn, "item_closed", item_id=2, verb="listo", at=NOW)  # 6h
    _event(conn, "item_found", item_id=3, at=NOW - dt.timedelta(hours=4))
    _event(conn, "item_closed", item_id=3, verb="listo", at=NOW)  # 4h
    got = board.week(conn, now=NOW)
    assert got["hours_to_close"] == 4.0


def test_hours_to_close_is_none_when_nothing_closed(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    _event(conn, "item_found", item_id=1)
    got = board.week(conn, now=NOW)
    assert got["hours_to_close"] is None


# --- spend ------------------------------------------------------------

def test_spend_groups_by_engine_and_counts_the_calls(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    _event(conn, "draft_written", item_id=1, engine="claude", cost_usd=0.5)
    _event(conn, "draft_written", item_id=2, engine="claude", cost_usd=0.3)
    _event(conn, "draft_written", item_id=3, engine="cursor", cost_usd=0.0)
    _event(conn, "cycle_ran", cost_usd=1.0)  # no engine: not grouped
    got = board.week(conn, now=NOW)
    assert got["spend"]["claude"] == {"cost_usd": 0.8, "calls": 2}
    assert got["spend"]["cursor"] == {"cost_usd": 0.0, "calls": 1}
    assert "cycle_ran" not in got["spend"]


# --- jobs ------------------------------------------------------------

def test_jobs_counts_done_and_failed(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    _event(conn, "job_done", job_id=1)
    _event(conn, "job_done", job_id=2)
    _event(conn, "job_failed", job_id=3)
    got = board.week(conn, now=NOW)
    assert got["jobs"] == {"job_done": 2, "job_failed": 1}


# --- enough ------------------------------------------------------------

def test_enough_is_false_on_a_thin_week(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    for i in range(4):
        _event(conn, "item_found", item_id=i, detail="needs_you")
    got = board.week(conn, now=NOW)
    assert got["enough"] is False


def test_enough_is_true_on_a_full_week(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    for i in range(5):
        _event(conn, "item_found", item_id=i, detail="needs_you")
    got = board.week(conn, now=NOW)
    assert got["enough"] is True


# --- by_day ------------------------------------------------------------

def test_by_day_gives_one_row_per_day_including_empty_days(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    _event(conn, "item_found", item_id=1, at=NOW - dt.timedelta(days=3))
    _event(conn, "item_found", item_id=2, at=NOW)
    _event(conn, "draft_written", item_id=1, engine="claude", cost_usd=1.5,
          at=NOW - dt.timedelta(days=1))
    rows = board.by_day(conn, now=NOW, days=7)
    assert len(rows) == 7
    by_date = {r["date"]: r for r in rows}
    assert by_date[(NOW - dt.timedelta(days=3)).date().isoformat()]["found"] == 1
    assert by_date[NOW.date().isoformat()]["found"] == 1
    assert by_date[(NOW - dt.timedelta(days=1)).date().isoformat()]["spend"] == 1.5
    # A day with nothing still gets its row, at zero.
    empty_day = (NOW - dt.timedelta(days=5)).date().isoformat()
    assert by_date[empty_day] == {"date": empty_day, "found": 0, "spend": 0.0}


# --- the window ----------------------------------------------------------

def test_a_seven_day_window_ignores_an_event_from_thirty_days_ago(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    _event(conn, "item_found", item_id=1, detail="needs_you",
          at=NOW - dt.timedelta(days=30))
    _event(conn, "item_found", item_id=2, detail="needs_you", at=NOW)
    got = board.week(conn, now=NOW, days=7)
    assert got["reviewed"] == 1
    assert got["raised"] == 1


def test_rating_the_same_item_twice_counts_once(tmp_path):
    """A second rating replaces the first, so counting both would weigh one
    item twice and drag the average."""
    import datetime as dt
    from nightshift import board, db, life
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO runs (started_at, kind, ok) VALUES ('x','mail',1)")
    conn.execute("INSERT INTO items (run_id, created_at, bucket, title,"
                 " source_url) VALUES (1,'x','needs_you','t','https://x/1')")
    conn.commit()
    life.rate(conn, 1, 3)
    life.rate(conn, 1, 9)      # the user changed their mind
    scores = board.week(conn, now=dt.datetime.now())["scores"]
    assert scores["count"] == 1, scores
    assert scores["average"] is None or scores["average"] == 9
