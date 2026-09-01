import datetime as dt

from nightshift import db, jobs, projects


def test_add_with_once_makes_no_template(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    job_id = jobs.add(conn, "one-shot")
    row = jobs.get(conn, job_id)
    assert row["state"] == "queued"
    assert row["schedule"] == "once"
    assert row["template_id"] is None
    templates = conn.execute(
        "SELECT count(*) FROM jobs WHERE state='template'").fetchone()[0]
    assert templates == 0


def test_add_with_weekly_makes_a_template_and_a_job(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    now = dt.datetime(2026, 9, 1, 6, 30)
    job_id = jobs.add(conn, "sprint review", schedule="weekly", now=now)

    job = jobs.get(conn, job_id)
    assert job["state"] == "queued"

    template = conn.execute(
        "SELECT * FROM jobs WHERE state='template'").fetchone()
    assert template is not None
    assert template["schedule"] == "weekly"
    assert template["prompt"] == "sprint review"
    # the first job is made right away; the template's own next_run points
    # at the *next* turn, one week after this one, not at this one again
    assert template["next_run"] == dt.datetime(2026, 9, 8, 6, 30).isoformat()
    assert job["template_id"] == template["id"]


def test_add_keeps_the_old_call_working_with_no_keywords(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    job_id = jobs.add(conn, "Prepare the sprint review")
    assert jobs.get(conn, job_id)["prompt"] == "Prepare the sprint review"


def test_add_stores_the_project(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    project_id = projects.add(conn, "aph-knowledge", "veritas")
    job_id = jobs.add(conn, "one", project_id=project_id)
    assert jobs.get(conn, job_id)["project_id"] == project_id


def test_next_after_daily(tmp_path):
    """`daily` anchors to 09:00, the same clock time every person reads,
    instead of the hour the job happened to be queued at."""
    now = dt.datetime(2026, 9, 1, 6, 30)
    assert jobs.next_after("daily", now) == dt.datetime(2026, 9, 1, 9, 0)


def test_next_after_daily_after_nine_rolls_to_tomorrow(tmp_path):
    now = dt.datetime(2026, 9, 1, 9, 30)
    assert jobs.next_after("daily", now) == dt.datetime(2026, 9, 2, 9, 0)


def test_next_after_hourly(tmp_path):
    now = dt.datetime(2026, 9, 1, 6, 30)
    assert jobs.next_after("hourly", now) == dt.datetime(2026, 9, 1, 7, 30)


def test_next_after_every_3h(tmp_path):
    now = dt.datetime(2026, 9, 1, 6, 30)
    assert jobs.next_after("every_3h", now) == dt.datetime(2026, 9, 1, 9, 30)


def test_next_after_twice_daily_before_nine_gives_nine(tmp_path):
    now = dt.datetime(2026, 9, 1, 8, 0)
    assert jobs.next_after("twice_daily", now) == dt.datetime(2026, 9, 1, 9, 0)


def test_next_after_twice_daily_between_nine_and_six_gives_six(tmp_path):
    now = dt.datetime(2026, 9, 1, 10, 0)
    assert jobs.next_after("twice_daily", now) == dt.datetime(2026, 9, 1, 18, 0)


def test_next_after_twice_daily_after_six_rolls_to_tomorrow_nine(tmp_path):
    now = dt.datetime(2026, 9, 1, 19, 0)
    assert jobs.next_after("twice_daily", now) == dt.datetime(2026, 9, 2, 9, 0)


def test_next_after_weekdays_from_a_friday_gives_the_monday(tmp_path):
    """2026-09-04 is a Friday. Past its own 09:00, the next 09:00 falls on
    Saturday, and Saturday is not a weekday: it rolls to Monday."""
    now = dt.datetime(2026, 9, 4, 14, 0)
    assert jobs.next_after("weekdays", now) == dt.datetime(2026, 9, 7, 9, 0)


def test_next_after_weekdays_stays_within_the_week_when_it_can(tmp_path):
    """2026-09-1 is a Tuesday: the next weekday 09:00 is the same day."""
    now = dt.datetime(2026, 9, 1, 6, 30)
    assert jobs.next_after("weekdays", now) == dt.datetime(2026, 9, 1, 9, 0)


def test_next_after_biweekly(tmp_path):
    now = dt.datetime(2026, 9, 1, 6, 30)
    assert jobs.next_after("biweekly", now) == dt.datetime(2026, 9, 15, 6, 30)


def test_next_after_weekly(tmp_path):
    now = dt.datetime(2026, 9, 1, 6, 30)
    assert jobs.next_after("weekly", now) == dt.datetime(2026, 9, 8, 6, 30)


def test_next_after_monthly(tmp_path):
    now = dt.datetime(2026, 9, 1, 6, 30)
    assert jobs.next_after("monthly", now) == dt.datetime(2026, 10, 1, 6, 30)


def test_next_after_monthly_handles_a_short_month(tmp_path):
    """31 January has no 31 February. The next run lands on the last day of
    the month instead of raising."""
    now = dt.datetime(2026, 1, 31, 6, 30)
    assert jobs.next_after("monthly", now) == dt.datetime(2026, 2, 28, 6, 30)


def test_next_after_monthly_rolls_the_year(tmp_path):
    now = dt.datetime(2026, 12, 15, 6, 30)
    assert jobs.next_after("monthly", now) == dt.datetime(2027, 1, 15, 6, 30)


def test_next_after_once_has_no_next_occurrence(tmp_path):
    now = dt.datetime(2026, 9, 1, 6, 30)
    assert jobs.next_after("once", now) is None


def test_due_templates_gives_only_templates_whose_turn_arrived(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    now = dt.datetime(2026, 9, 1, 6, 30)
    jobs.add(conn, "not due yet", schedule="weekly", now=now)
    due = jobs.due_templates(conn, now)
    assert due == []

    due_later = jobs.due_templates(conn, now + dt.timedelta(days=8))
    assert len(due_later) == 1


def test_fire_templates_makes_a_job_and_moves_next_run(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    now = dt.datetime(2026, 9, 1, 6, 30)
    jobs.add(conn, "sprint review", schedule="weekly", now=now)
    template = conn.execute(
        "SELECT * FROM jobs WHERE state='template'").fetchone()
    # its own job is already done, so the next turn is free to fire
    conn.execute("UPDATE jobs SET state='done' WHERE id != ?",
                 (template["id"],))
    conn.commit()

    later = now + dt.timedelta(days=8)
    result = jobs.fire_templates(conn, later)
    assert result == {"made": 1, "skipped": 0}

    fresh_jobs = conn.execute(
        "SELECT * FROM jobs WHERE template_id=? ORDER BY id",
        (template["id"],)).fetchall()
    assert len(fresh_jobs) == 2  # the one add() made, plus this one
    assert fresh_jobs[-1]["state"] == "queued"

    moved = conn.execute("SELECT next_run FROM jobs WHERE id=?",
                         (template["id"],)).fetchone()["next_run"]
    assert moved != template["next_run"]
    assert dt.datetime.fromisoformat(moved) > later

    kinds = [r[0] for r in conn.execute(
        "SELECT kind FROM events WHERE job_id=?", (template["id"],))]
    assert "template_fired" in kinds


def test_fire_templates_skips_a_turn_whose_last_job_is_still_open(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    now = dt.datetime(2026, 9, 1, 6, 30)
    jobs.add(conn, "sprint review", schedule="weekly", now=now)
    template = conn.execute(
        "SELECT * FROM jobs WHERE state='template'").fetchone()
    # the job add() made is still queued: the turn must be skipped
    assert conn.execute(
        "SELECT state FROM jobs WHERE template_id=?",
        (template["id"],)).fetchone()["state"] == "queued"

    later = now + dt.timedelta(days=8)
    result = jobs.fire_templates(conn, later)
    assert result == {"made": 0, "skipped": 1}

    fresh_jobs = conn.execute(
        "SELECT count(*) FROM jobs WHERE template_id=?",
        (template["id"],)).fetchone()[0]
    assert fresh_jobs == 1  # no new job: the old one is still open

    moved = conn.execute("SELECT next_run FROM jobs WHERE id=?",
                         (template["id"],)).fetchone()["next_run"]
    assert dt.datetime.fromisoformat(moved) > later

    kinds = [r[0] for r in conn.execute(
        "SELECT kind, detail FROM events WHERE job_id=?", (template["id"],))]
    assert "template_skipped" in kinds


def test_fire_templates_writes_an_event_even_when_it_skips(tmp_path):
    """Rule 2 of the design: a silent skip is a task that seems to run and
    does not."""
    conn = db.connect(tmp_path / "s.db")
    now = dt.datetime(2026, 9, 1, 6, 30)
    jobs.add(conn, "sprint review", schedule="weekly", now=now)
    later = now + dt.timedelta(days=8)
    jobs.fire_templates(conn, later)
    row = conn.execute(
        "SELECT detail FROM events WHERE kind='template_skipped'").fetchone()
    assert row is not None
    assert row["detail"]  # the reason is not empty


def test_fire_templates_fires_again_once_the_last_job_is_done(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    now = dt.datetime(2026, 9, 1, 6, 30)
    jobs.add(conn, "sprint review", schedule="weekly", now=now)
    template_id = conn.execute(
        "SELECT id FROM jobs WHERE state='template'").fetchone()[0]
    jobs.finish(conn, jobs.next_queued(conn)["id"], "/tmp/out")

    later = now + dt.timedelta(days=8)
    result = jobs.fire_templates(conn, later)
    assert result == {"made": 1, "skipped": 0}
    count = conn.execute(
        "SELECT count(*) FROM jobs WHERE template_id=?",
        (template_id,)).fetchone()[0]
    assert count == 2


def test_fire_templates_reaps_a_dead_job_and_fires_the_turn(tmp_path):
    """launchd can kill the process, or the Mac can sleep, mid-job. Nothing
    then moves the job out of 'running', and every later turn of the
    template used to be skipped for ever."""
    conn = db.connect(tmp_path / "s.db")
    now = dt.datetime(2026, 9, 1, 6, 30)
    jobs.add(conn, "sprint review", schedule="weekly", now=now)
    template_id = conn.execute(
        "SELECT id FROM jobs WHERE state='template'").fetchone()[0]
    stuck_id = jobs.next_queued(conn)["id"]
    conn.execute("UPDATE jobs SET state='running', started_at=? WHERE id=?",
                (now.isoformat(), stuck_id))
    conn.commit()

    later = now + dt.timedelta(days=8)  # far past both the turn and 30 min
    result = jobs.fire_templates(conn, later)

    assert result == {"made": 1, "skipped": 0}
    assert jobs.get(conn, stuck_id)["state"] == "failed"
    count = conn.execute(
        "SELECT count(*) FROM jobs WHERE template_id=?",
        (template_id,)).fetchone()[0]
    assert count == 2


# --- when_idle: the work that fills the room the week leaves ------------

IDLE_NOW = dt.datetime(2026, 9, 1, 18, 0)


def _idle_template(conn, now=IDLE_NOW, prompt="watch the CFP deadlines"):
    """A `when_idle` template whose first job is already closed, so the next
    turn is free to fire."""
    jobs.add(conn, prompt, schedule="when_idle", now=now)
    template = conn.execute(
        "SELECT * FROM jobs WHERE state='template' AND prompt=?",
        (prompt,)).fetchone()
    conn.execute("UPDATE jobs SET state='done' WHERE state='queued'"
                 " AND template_id=?", (template["id"],))
    conn.commit()
    return template


def test_when_idle_is_a_schedule_the_page_can_offer():
    assert "when_idle" in jobs.SCHEDULES
    assert jobs.LABELS["when_idle"] == "Cuando sobre"


def test_when_idle_is_always_due():
    """The gate is the allowance, not the clock."""
    assert jobs.next_after("when_idle", IDLE_NOW) == IDLE_NOW


def test_when_idle_fires_with_an_allowance_of_twenty(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    template = _idle_template(conn)

    result = jobs.fire_templates(conn, IDLE_NOW, allowance_pct=20)

    assert result == {"made": 1, "skipped": 0}
    fresh = conn.execute(
        "SELECT count(*) FROM jobs WHERE template_id=? AND state='queued'",
        (template["id"],)).fetchone()[0]
    assert fresh == 1


def test_when_idle_does_not_fire_with_an_allowance_of_five(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    template = _idle_template(conn)

    result = jobs.fire_templates(conn, IDLE_NOW, allowance_pct=5)

    assert result == {"made": 0, "skipped": 1}
    row = conn.execute(
        "SELECT detail FROM events WHERE kind='template_skipped'").fetchone()
    assert row["detail"] == "allowance 5 below 15"
    queued = conn.execute(
        "SELECT count(*) FROM jobs WHERE template_id=? AND state='queued'",
        (template["id"],)).fetchone()[0]
    assert queued == 0


def test_when_idle_does_not_fire_without_a_reading(tmp_path):
    """Rule 1 of the design: a blank `/usage` is not permission."""
    conn = db.connect(tmp_path / "s.db")
    _idle_template(conn)

    result = jobs.fire_templates(conn, IDLE_NOW)

    assert result == {"made": 0, "skipped": 1}
    row = conn.execute(
        "SELECT detail FROM events WHERE kind='template_skipped'").fetchone()
    assert row["detail"] == "no reading"


def test_a_skipped_idle_template_stays_due(tmp_path):
    """Every other schedule moves `next_run` on a skip. This one may not: it
    waits for room, and a moved turn would be a turn that never comes."""
    conn = db.connect(tmp_path / "s.db")
    template = _idle_template(conn)

    jobs.fire_templates(conn, IDLE_NOW, allowance_pct=5)

    after = conn.execute("SELECT next_run FROM jobs WHERE id=?",
                         (template["id"],)).fetchone()["next_run"]
    assert after == template["next_run"]
    assert [r["id"] for r in jobs.due_templates(conn, IDLE_NOW)] \
        == [template["id"]]


def test_only_one_idle_template_fires_in_one_turn(tmp_path):
    """Standing work must fill the room, not empty it in one go."""
    conn = db.connect(tmp_path / "s.db")
    first = _idle_template(conn, prompt="one")
    second = _idle_template(conn, prompt="two")

    result = jobs.fire_templates(conn, IDLE_NOW, allowance_pct=40)

    assert result == {"made": 1, "skipped": 1}
    assert conn.execute(
        "SELECT count(*) FROM jobs WHERE template_id=? AND state='queued'",
        (first["id"],)).fetchone()[0] == 1
    assert conn.execute(
        "SELECT count(*) FROM jobs WHERE template_id=? AND state='queued'",
        (second["id"],)).fetchone()[0] == 0
    # The one that waited is still due, so the next turn is its own.
    assert second["id"] in [r["id"] for r in
                            jobs.due_templates(conn, IDLE_NOW)]


def test_an_allowance_never_stops_the_other_schedules(tmp_path):
    """The threshold belongs to `when_idle` alone. A daily task keeps its
    clock, whatever the week looks like."""
    conn = db.connect(tmp_path / "s.db")
    now = dt.datetime(2026, 9, 1, 6, 30)
    jobs.add(conn, "sprint review", schedule="daily", now=now)
    jobs.finish(conn, jobs.next_queued(conn)["id"], "/tmp/out")

    result = jobs.fire_templates(conn, now + dt.timedelta(days=1),
                                 allowance_pct=0)

    assert result == {"made": 1, "skipped": 0}
