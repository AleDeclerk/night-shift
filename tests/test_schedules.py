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
    now = dt.datetime(2026, 9, 1, 6, 30)
    assert jobs.next_after("daily", now) == dt.datetime(2026, 9, 2, 6, 30)


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
