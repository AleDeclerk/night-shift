import datetime as dt

from nightshift import db, life

NOW = dt.datetime(2026, 8, 31, 7, 0)


def _item(conn, *, bucket="needs_you", created_at="2026-08-30T07:00:00"):
    conn.execute("INSERT INTO runs (started_at, kind, ok) VALUES ('x','mail',1)")
    cur = conn.execute(
        "INSERT INTO items (run_id, created_at, bucket, title, body,"
        " source_url) VALUES (1,?,?,?,?,?)",
        (created_at, bucket, "t", "w", "https://x/1"))
    conn.commit()
    return cur.lastrowid


def test_each_verb_leaves_the_state_that_verbs_names(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    for verb, (state, _reason) in life.VERBS.items():
        item_id = _item(conn)
        got = life.apply_verb(conn, item_id, verb, now=NOW)
        assert got == state
        row = conn.execute("SELECT state FROM items WHERE id=?",
                           (item_id,)).fetchone()
        assert row["state"] == state


def test_an_unknown_verb_raises_and_changes_nothing(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    item_id = _item(conn)
    try:
        life.apply_verb(conn, item_id, "send_it", now=NOW)
        assert False, "should have raised"
    except ValueError:
        pass
    row = conn.execute("SELECT state FROM items WHERE id=?",
                       (item_id,)).fetchone()
    assert row["state"] == "pending"
    assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 0


def test_apply_verb_writes_the_event_before_it_returns(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    item_id = _item(conn)
    life.apply_verb(conn, item_id, "listo", now=NOW)
    row = conn.execute(
        "SELECT * FROM events WHERE item_id=? AND kind='item_closed'",
        (item_id,)).fetchone()
    assert row is not None
    assert row["verb"] == "listo"


def test_state_from_events_agrees_with_the_column_for_every_verb(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    for verb in life.VERBS:
        item_id = _item(conn)
        life.apply_verb(conn, item_id, verb, now=NOW)
        column = conn.execute("SELECT state FROM items WHERE id=?",
                              (item_id,)).fetchone()["state"]
        assert life.state_from_events(conn, item_id) == column


def test_state_from_events_with_no_history_is_pending(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    item_id = _item(conn)
    assert life.state_from_events(conn, item_id) == "pending"


def test_manana_sets_snoozed_until_in_the_future_and_hides_the_item(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    item_id = _item(conn)
    life.apply_verb(conn, item_id, "manana")  # real now, so it is in the future
    row = conn.execute("SELECT snoozed_until FROM items WHERE id=?",
                       (item_id,)).fetchone()
    assert row["snoozed_until"] is not None
    parsed = dt.datetime.fromisoformat(row["snoozed_until"])
    assert parsed > dt.datetime.now()
    assert item_id not in {r["id"] for r in life.open_items(conn)}


def test_open_items_shows_a_snoozed_item_once_its_time_passed(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    item_id = _item(conn)
    # A `now` far in the past: `snoozed_until` (one day later) still lands
    # long before the real clock, so it reads as passed with no time travel.
    life.apply_verb(conn, item_id, "manana", now=dt.datetime(2000, 1, 1))
    assert item_id in {r["id"] for r in life.open_items(conn)}


def test_a_closed_item_never_appears_in_open_items(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    for verb in ("listo", "lo_hago_yo", "no_era_nada"):
        item_id = _item(conn)
        life.apply_verb(conn, item_id, verb, now=NOW)
        assert item_id not in {r["id"] for r in life.open_items(conn)}


def test_a_no_action_item_never_appears_in_open_items(tmp_path):
    """The life cycle is for mail that needed an answer. `no_action` mail
    stays in its own read-only pile."""
    conn = db.connect(tmp_path / "s.db")
    item_id = _item(conn, bucket="no_action")
    assert item_id not in {r["id"] for r in life.open_items(conn)}


def test_closed_items_gives_newest_first_with_its_verb(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    first = _item(conn)
    life.apply_verb(conn, first, "listo",
                    now=dt.datetime(2026, 8, 30, 8, 0))
    second = _item(conn)
    life.apply_verb(conn, second, "no_era_nada",
                    now=dt.datetime(2026, 8, 30, 9, 0))
    rows = life.closed_items(conn)
    assert [r["id"] for r in rows] == [second, first]
    assert rows[0]["verb"] == "no_era_nada"
    assert rows[1]["verb"] == "listo"


def test_false_alarm_rate_counts_only_no_era_nada_as_false(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    since = dt.datetime.now() - dt.timedelta(days=1)
    created = since.isoformat()

    dismissed = _item(conn, created_at=created)
    life.apply_verb(conn, dismissed, "no_era_nada", now=dt.datetime.now())

    handled = _item(conn, created_at=created)
    life.apply_verb(conn, handled, "listo", now=dt.datetime.now())

    still_open = _item(conn, created_at=created)

    rate = life.false_alarm_rate(conn, days=14)
    assert rate == {"raised": 3, "false": 1}


def test_apply_verb_stamps_its_event_with_the_injected_clock(tmp_path):
    """`record` fell back to the real clock here, so the cascade read
    `events.at` with one clock while the caller worked with another."""
    conn = db.connect(tmp_path / "s.db")
    item_id = _item(conn)
    when = dt.datetime(2026, 8, 26, 3, 0)
    life.apply_verb(conn, item_id, "listo", now=when)
    row = conn.execute(
        "SELECT at FROM events WHERE item_id=? AND kind='item_closed'",
        (item_id,)).fetchone()
    assert row["at"].startswith("2026-08-26T03:00")


def test_an_event_can_be_stamped_with_a_given_time(tmp_path):
    """The cycle takes a `now` and the cascade reads `events.at` to decide if
    there is room. If the record ignores that clock, the governor judges with
    one time while the cycle works with another."""
    import datetime as dt
    from nightshift import db, life
    conn = db.connect(tmp_path / "s.db")
    when = dt.datetime(2026, 8, 26, 3, 0)
    life.record(conn, "cycle_ran", cost_usd=1.5, now=when)
    stored = conn.execute("SELECT at FROM events").fetchone()[0]
    assert stored.startswith("2026-08-26T03:00")


def test_an_event_with_no_time_uses_the_clock(tmp_path):
    import datetime as dt
    from nightshift import db, life
    conn = db.connect(tmp_path / "s.db")
    life.record(conn, "cycle_ran")
    stored = conn.execute("SELECT at FROM events").fetchone()[0]
    assert stored.startswith(dt.datetime.now().strftime("%Y-%m-%d"))


# --- the feedback ---------------------------------------------------------

def test_rate_stores_the_score_and_the_comment(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    item_id = _item(conn)
    life.rate(conn, item_id, 7, "Good draft.", now=NOW)
    row = conn.execute("SELECT score, comment FROM items WHERE id=?",
                       (item_id,)).fetchone()
    assert row["score"] == 7
    assert row["comment"] == "Good draft."


def test_rate_works_on_a_closed_item_too(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    item_id = _item(conn)
    life.apply_verb(conn, item_id, "listo", now=NOW)
    life.rate(conn, item_id, 9, now=NOW)
    row = conn.execute("SELECT score FROM items WHERE id=?",
                       (item_id,)).fetchone()
    assert row["score"] == 9


def test_rate_refuses_a_score_below_the_range(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    item_id = _item(conn)
    try:
        life.rate(conn, item_id, 0, now=NOW)
        assert False, "should have raised"
    except ValueError:
        pass
    row = conn.execute("SELECT score FROM items WHERE id=?",
                       (item_id,)).fetchone()
    assert row["score"] is None


def test_rate_refuses_a_score_above_the_range(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    item_id = _item(conn)
    try:
        life.rate(conn, item_id, 11, now=NOW)
        assert False, "should have raised"
    except ValueError:
        pass
    row = conn.execute("SELECT score FROM items WHERE id=?",
                       (item_id,)).fetchone()
    assert row["score"] is None


def test_rate_twice_replaces_the_previous_score(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    item_id = _item(conn)
    life.rate(conn, item_id, 3, "First pass.", now=NOW)
    life.rate(conn, item_id, 8, "Changed my mind.", now=NOW)
    row = conn.execute("SELECT score, comment FROM items WHERE id=?",
                       (item_id,)).fetchone()
    assert row["score"] == 8
    assert row["comment"] == "Changed my mind."


def test_rate_writes_an_item_rated_event_with_the_score(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    item_id = _item(conn)
    life.rate(conn, item_id, 6, now=NOW)
    row = conn.execute(
        "SELECT * FROM events WHERE item_id=? AND kind='item_rated'",
        (item_id,)).fetchone()
    assert row is not None
    assert row["detail"] == "6"


def test_rate_twice_leaves_both_events_in_the_history(tmp_path):
    """Rule 3 of the life design: the events never disappear. The item keeps
    only the latest score, but the history keeps every rating."""
    conn = db.connect(tmp_path / "s.db")
    item_id = _item(conn)
    life.rate(conn, item_id, 3, now=NOW)
    life.rate(conn, item_id, 8, now=NOW)
    rows = conn.execute(
        "SELECT detail FROM events WHERE item_id=? AND kind='item_rated'"
        " ORDER BY id", (item_id,)).fetchall()
    assert [r["detail"] for r in rows] == ["3", "8"]
