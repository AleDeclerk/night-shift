import datetime as dt
import json

import pytest

from nightshift import db, jobs, tick
from nightshift.runner import RunResult

NOW = dt.datetime(2026, 9, 1, 12, 0)


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


def test_a_tick_over_the_ceiling_runs_no_job_and_says_why(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO runs (started_at, kind, ok, cost_usd)"
                 " VALUES (?, 'mail', 1, 20.0)", (NOW.isoformat(),))
    conn.commit()
    jobs.add(conn, "one", now=NOW)
    fake = FakeRunner({"finished": True, "summary": "done"})

    result = tick.run(conn, runner_module=fake, workspace=tmp_path,
                      now=NOW, ceiling_usd=20.0)
    assert result["jobs_run"] == 0
    assert result["cost_usd"] == 0.0
    assert result["reason"]
    assert fake.calls == []            # the runner never ran


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
