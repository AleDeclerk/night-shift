import datetime as dt
import json

import pytest

from nightshift import db, jobs, quota, tick, usage
from nightshift.runner import RunResult

NOW = dt.datetime(2026, 9, 1, 12, 0)
READING = usage.Usage(week_pct=4,
                      week_resets=dt.datetime(2026, 9, 8, 5, 59),
                      session_pct=35,
                      session_resets=dt.datetime(2026, 9, 1, 20, 39))


class FakeRunner:
    """The same shape as jobs.run_next expects. No test here ever touches a
    real engine."""

    def __init__(self, payload=None, ok=True, error=None, cost=0.4):
        self.payload, self.ok, self.error, self.cost = payload, ok, error, cost
        self.calls = []

    def run(self, prompt, **kw):
        self.calls.append((prompt, kw))
        text = json.dumps(self.payload) if self.payload is not None else "junk"
        return RunResult(self.ok, text=text, cost_usd=self.cost, error=self.error)


def _events(conn, kind):
    return conn.execute("SELECT * FROM events WHERE kind=?", (kind,)).fetchall()


def test_a_tick_with_nothing_to_do_writes_no_event_and_spends_nothing(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    result = tick.run(conn, runner_module=FakeRunner(), workspace=tmp_path,
                      now=NOW, ceiling_usd=20.0)
    assert result == {"fired": 0, "skipped": 0, "jobs_run": 0,
                      "cost_usd": 0.0, "reason": ""}
    assert _events(conn, "tick_ran") == []


def test_a_tick_with_a_due_template_fires_it(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    earlier = NOW - dt.timedelta(hours=2)
    jobs.add(conn, "check inbox", schedule="hourly", now=earlier)
    # the job the first add() made is already closed, so this turn is free
    jobs.finish(conn, jobs.next_queued(conn)["id"], "/tmp/out")

    fake = FakeRunner({"finished": True, "summary": "done"})
    result = tick.run(conn, runner_module=fake, workspace=tmp_path,
                      now=NOW, ceiling_usd=20.0)
    assert result["fired"] == 1
    assert result["skipped"] == 0


def test_a_tick_with_a_queued_job_runs_it_and_reports_its_cost(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    jobs.add(conn, "one", now=NOW)
    fake = FakeRunner({"finished": True, "summary": "done"}, cost=0.42)

    result = tick.run(conn, runner_module=fake, workspace=tmp_path,
                      now=NOW, ceiling_usd=20.0)
    assert result["jobs_run"] == 1
    assert result["cost_usd"] == 0.42
    assert conn.execute("SELECT state FROM jobs WHERE prompt='one'"
                        ).fetchone()["state"] == "done"


def test_what_one_tick_spent_stops_the_next_one(tmp_path):
    """The tick recorded its cost only in a `tick_ran` event, whose engine is
    NULL. `quota.spent_this_week` reads `runs` and `cascade.spent_by` reads
    the events that name an engine, so neither governor ever saw it. With the
    plist at 3600 seconds that is 24 jobs a day past any ceiling."""
    conn = db.connect(tmp_path / "s.db")
    jobs.add(conn, "one", now=NOW)
    jobs.add(conn, "two", now=NOW)

    first = FakeRunner({"finished": True, "summary": "done"}, cost=20.0)
    spender = tick.run(conn, runner_module=first, workspace=tmp_path,
                       now=NOW, ceiling_usd=20.0)
    assert spender["jobs_run"] == 1
    assert quota.spent_this_week(conn, NOW) == 20.0

    second = FakeRunner({"finished": True, "summary": "done"})
    result = tick.run(conn, runner_module=second, workspace=tmp_path,
                      now=NOW, ceiling_usd=20.0)
    assert result["jobs_run"] == 0
    assert result["cost_usd"] == 0.0
    assert result["reason"]
    assert second.calls == []            # the runner never ran


def test_a_tick_is_not_the_last_good_mail_cycle(tmp_path):
    """The mail window starts where the last good MAIL cycle started. A tick
    now writes a run too, and an hourly tick would cut that window to one
    hour and hide every message older than it."""
    from nightshift import cycle
    from nightshift.mail import TriageResult

    class Stub:
        def __init__(self):
            self.since = "not asked"

        def triage(self, *a, **kw):
            self.since = kw.get("since")
            return TriageResult([], 0.5, None)

    conn = db.connect(tmp_path / "s.db")
    first = Stub()
    cycle.run_once(conn, runner_module=first, mail_module=first, now=NOW,
                   ceiling_usd=45.0, workspace=tmp_path)

    tick.run(conn, runner_module=FakeRunner(), workspace=tmp_path,
             now=NOW + dt.timedelta(hours=1), ceiling_usd=45.0)

    second = Stub()
    cycle.run_once(conn, runner_module=second, mail_module=second,
                   now=NOW + dt.timedelta(hours=2), ceiling_usd=45.0,
                   workspace=tmp_path)
    assert second.since == NOW.isoformat()


def test_max_jobs_caps_how_many_run_in_one_tick(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    for i in range(3):
        jobs.add(conn, f"job {i}", now=NOW)
    fake = FakeRunner({"finished": True, "summary": "done"}, cost=0.1)

    result = tick.run(conn, runner_module=fake, workspace=tmp_path,
                      now=NOW, ceiling_usd=20.0, max_jobs=2)
    assert result["jobs_run"] == 2
    assert result["cost_usd"] == pytest.approx(0.2)
    assert jobs.next_queued(conn) is not None   # one job still waits


def test_a_tick_that_did_something_writes_one_tick_ran_event(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    jobs.add(conn, "one", now=NOW)
    fake = FakeRunner({"finished": True, "summary": "done"})

    tick.run(conn, runner_module=fake, workspace=tmp_path, now=NOW,
             ceiling_usd=20.0)
    assert len(_events(conn, "tick_ran")) == 1


def test_a_tick_that_spent_leaves_a_run_the_governor_reads(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    jobs.add(conn, "one", now=NOW)
    fake = FakeRunner({"finished": True, "summary": "done"}, cost=0.42)

    tick.run(conn, runner_module=fake, workspace=tmp_path, now=NOW,
             ceiling_usd=20.0)
    row = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    assert row["kind"] == "tick"
    assert row["cost_usd"] == 0.42
    assert row["ok"] == 1
    assert row["finished_at"] is not None


def test_a_tick_that_did_nothing_still_closes_its_run(tmp_path):
    """A run that never ends reads as a cycle that died, and the page says
    so. Every exit of the tick closes the row it opened."""
    conn = db.connect(tmp_path / "s.db")
    tick.run(conn, runner_module=FakeRunner(), workspace=tmp_path, now=NOW,
             ceiling_usd=20.0)
    row = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    assert row["kind"] == "tick"
    assert row["ok"] == 1
    assert row["cost_usd"] == 0.0


def test_a_tick_reads_the_real_quota_and_stores_it(tmp_path, monkeypatch):
    """A page load may not shell out, and one `/usage` call takes about three
    seconds. The tick makes the call and the page reads what it left."""
    conn = db.connect(tmp_path / "s.db")
    monkeypatch.setattr(quota.usage, "read", lambda **kw: READING)

    tick.run(conn, runner_module=FakeRunner(), workspace=tmp_path, now=NOW,
             ceiling_usd=20.0)

    assert quota.last_usage(conn, NOW) == READING


def test_a_tick_records_that_the_cli_could_not_answer(tmp_path):
    """The conftest answers None, the way a machine with no session does."""
    conn = db.connect(tmp_path / "s.db")
    tick.run(conn, runner_module=FakeRunner(), workspace=tmp_path, now=NOW,
             ceiling_usd=20.0)
    assert len(_events(conn, "usage_unavailable")) == 1
    assert quota.last_usage(conn, NOW) is None


# --- when_idle: the tick gives it the allowance of the real week --------

def _idle_template(conn, prompt="draft the sprint review"):
    """A `when_idle` template with no open job of its own, so the next turn
    is free to fire."""
    jobs.add(conn, prompt, schedule="when_idle", now=NOW)
    template = conn.execute(
        "SELECT * FROM jobs WHERE state='template' AND prompt=?",
        (prompt,)).fetchone()
    conn.execute("UPDATE jobs SET state='done' WHERE state='queued'")
    conn.commit()
    return template


def _reading(week_pct):
    return usage.Usage(week_pct=week_pct,
                       week_resets=dt.datetime(2026, 9, 8, 5, 59),
                       session_pct=35,
                       session_resets=dt.datetime(2026, 9, 1, 20, 39))


def test_a_quiet_week_fires_the_standing_work(tmp_path, monkeypatch):
    """Week 4% used and seven days left: the allowance is 26, above the
    threshold of 15."""
    conn = db.connect(tmp_path / "s.db")
    _idle_template(conn)
    monkeypatch.setattr(quota.usage, "read", lambda **kw: _reading(4))

    result = tick.run(conn, runner_module=FakeRunner(
        {"finished": True, "summary": "done"}), workspace=tmp_path, now=NOW,
        ceiling_usd=20.0, max_jobs=0)

    assert result["fired"] == 1


def test_a_busy_week_leaves_the_standing_work_waiting(tmp_path, monkeypatch):
    """Week 25% used and seven days left: the allowance is 5, below 15."""
    conn = db.connect(tmp_path / "s.db")
    template = _idle_template(conn)
    monkeypatch.setattr(quota.usage, "read", lambda **kw: _reading(25))

    result = tick.run(conn, runner_module=FakeRunner(), workspace=tmp_path,
                      now=NOW, ceiling_usd=20.0)

    assert result == {"fired": 0, "skipped": 1, "jobs_run": 0,
                      "cost_usd": 0.0, "reason": ""}
    row = conn.execute(
        "SELECT detail FROM events WHERE kind='template_skipped'").fetchone()
    assert row["detail"] == "allowance 5 below 15"
    # It waits, it does not lose its turn: the next tick asks again.
    assert [r["id"] for r in jobs.due_templates(conn, NOW)] \
        == [template["id"]]


def test_without_a_reading_the_standing_work_waits(tmp_path):
    """The conftest answers None, the way a machine with no session does."""
    conn = db.connect(tmp_path / "s.db")
    _idle_template(conn)

    result = tick.run(conn, runner_module=FakeRunner(), workspace=tmp_path,
                      now=NOW, ceiling_usd=20.0)

    assert result["fired"] == 0 and result["skipped"] == 1
