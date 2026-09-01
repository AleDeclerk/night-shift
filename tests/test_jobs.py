import datetime as dt

import pytest

from nightshift import db, engines, jobs


class FakeRunner:
    def __init__(self, payload=None, ok=True, error=None, cost=0.7):
        self.payload, self.ok, self.error, self.cost = payload, ok, error, cost
        self.calls = []

    def run(self, prompt, **kw):
        import json
        from nightshift.runner import RunResult
        self.calls.append((prompt, kw))
        text = json.dumps(self.payload) if self.payload is not None else "junk"
        return RunResult(self.ok, text=text, cost_usd=self.cost,
                         error=self.error)


def test_an_empty_queue_costs_nothing(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    assert jobs.run_next(conn, FakeRunner(), tmp_path) == 0.0


def test_a_finished_job_records_its_summary(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    job_id = jobs.add(conn, "Prepare the sprint review")
    spent = jobs.run_next(conn, FakeRunner(
        {"finished": True, "summary": "I wrote the draft."}), tmp_path)
    row = jobs.get(conn, job_id)
    assert row["state"] == "done"
    assert row["answer"] == "I wrote the draft."
    assert spent == 0.7


def test_a_job_that_needs_a_decision_stops_and_asks(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    job_id = jobs.add(conn, "Prepare the sprint review")
    jobs.run_next(conn, FakeRunner(
        {"finished": False, "question": "Which sprint number?",
         "summary": "I stopped."}), tmp_path)
    row = jobs.get(conn, job_id)
    assert row["state"] == "needs_you"
    assert row["question"] == "Which sprint number?"
    assert jobs.next_queued(conn) is None


def test_a_failed_job_never_returns_to_the_queue(tmp_path):
    """A retry on every cycle would burn the weekly quota on one failure."""
    conn = db.connect(tmp_path / "s.db")
    job_id = jobs.add(conn, "one")
    spent = jobs.run_next(conn, FakeRunner(ok=False, error="401 expired"),
                          tmp_path)
    assert jobs.get(conn, job_id)["state"] == "failed"
    assert jobs.next_queued(conn) is None
    assert spent == 0.7


def test_an_answer_that_is_not_json_fails_the_job(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    job_id = jobs.add(conn, "one")
    jobs.run_next(conn, FakeRunner(payload=None), tmp_path)
    assert jobs.get(conn, job_id)["state"] == "failed"


def test_each_job_gets_its_own_directory(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    job_id = jobs.add(conn, "one")
    fake = FakeRunner({"finished": True, "summary": "done"})
    jobs.run_next(conn, fake, tmp_path)
    _, kw = fake.calls[0]
    assert str(kw["cwd"]).endswith(f"job-{job_id}")


def test_a_new_job_is_queued(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    job_id = jobs.add(conn, "Prepare the sprint review")
    assert jobs.get(conn, job_id)["state"] == "queued"


def test_next_queued_returns_the_oldest_job(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    first = jobs.add(conn, "one")
    jobs.add(conn, "two")
    assert jobs.next_queued(conn)["id"] == first


def test_a_stopped_job_holds_its_question(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    job_id = jobs.add(conn, "one")
    jobs.stop_and_ask(conn, job_id, "Which sprint number is this?")
    row = jobs.get(conn, job_id)
    assert row["state"] == "needs_you"
    assert row["question"] == "Which sprint number is this?"


def test_a_stopped_job_is_not_picked_again(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    job_id = jobs.add(conn, "one")
    jobs.stop_and_ask(conn, job_id, "why?")
    assert jobs.next_queued(conn) is None


# --- which engine runs a job -------------------------------------------

def test_a_non_claude_engine_uses_engines_run_not_the_claude_runner(
        tmp_path, monkeypatch):
    job_id = None
    calls = []

    def fake_engines_run(prompt, *, engine, cwd, timeout=900):
        calls.append((prompt, engine, cwd))
        return engines.EngineRun(True, text='{"finished": true, '
                                             '"summary": "done"}', cost_usd=0.0)

    monkeypatch.setattr(jobs.engines, "run", fake_engines_run)

    conn = db.connect(tmp_path / "s.db")
    job_id = jobs.add(conn, "one")
    fake_claude_runner = FakeRunner({"finished": True, "summary": "unused"})
    jobs.run_next(conn, fake_claude_runner, tmp_path, engine="ollama")

    assert len(calls) == 1
    assert calls[0][1] == "ollama"
    assert fake_claude_runner.calls == []  # the claude runner was never used
    assert jobs.get(conn, job_id)["state"] == "done"
    assert jobs.get(conn, job_id)["answer"] == "done"


def test_the_default_engine_still_uses_the_claude_runner_with_a_schema(
        tmp_path):
    conn = db.connect(tmp_path / "s.db")
    jobs.add(conn, "one")
    fake = FakeRunner({"finished": True, "summary": "done"})
    jobs.run_next(conn, fake, tmp_path)  # no engine given: falls to default
    assert len(fake.calls) == 1
    _, kw = fake.calls[0]
    assert kw["schema"] == jobs.SCHEMA


# --- remembering the engine choice --------------------------------------

def test_get_engine_gives_claude_when_nothing_is_stored(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    assert engines.get_engine(conn) == "claude"


def test_set_engine_then_get_engine_round_trips(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    engines.set_engine(conn, "ollama")
    assert engines.get_engine(conn) == "ollama"


def test_set_engine_gemini_is_refused(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    with pytest.raises(ValueError):
        engines.set_engine(conn, "gemini")


def test_a_finished_job_leaves_an_event(tmp_path):
    """The weekly board counts `job_done`. Without the event it shows zero
    for ever, and a zero reads as "nothing happened"."""
    conn = db.connect(tmp_path / "s.db")
    job_id = jobs.add(conn, "Prepare the sprint review")
    jobs.finish(conn, job_id, "/tmp/job-1")
    kinds = [r[0] for r in conn.execute("SELECT kind FROM events")]
    assert "job_done" in kinds


def test_a_failed_job_leaves_an_event(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    job_id = jobs.add(conn, "one")
    jobs.fail(conn, job_id, "401 expired")
    rows = conn.execute("SELECT kind, detail FROM events").fetchall()
    assert any(r[0] == "job_failed" for r in rows)


def test_a_queued_job_leaves_an_event(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    jobs.add(conn, "one")
    assert "job_queued" in [r[0] for r in conn.execute("SELECT kind FROM events")]


def test_finish_stamps_its_event_with_the_injected_clock(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    job_id = jobs.add(conn, "one")
    when = dt.datetime(2026, 8, 26, 3, 0)
    jobs.finish(conn, job_id, "/tmp/out", now=when)
    row = conn.execute(
        "SELECT at FROM events WHERE job_id=? AND kind='job_done'",
        (job_id,)).fetchone()
    assert row["at"].startswith("2026-08-26T03:00")


def test_fail_stamps_its_event_with_the_injected_clock(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    job_id = jobs.add(conn, "one")
    when = dt.datetime(2026, 8, 26, 3, 0)
    jobs.fail(conn, job_id, "401 expired", now=when)
    row = conn.execute(
        "SELECT at FROM events WHERE job_id=? AND kind='job_failed'",
        (job_id,)).fetchone()
    assert row["at"].startswith("2026-08-26T03:00")


# --- reap_stale: a job that dies in 'running' must not freeze for ever ----

def test_reap_stale_fails_a_job_running_past_the_limit(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    job_id = jobs.add(conn, "one")
    now = dt.datetime(2026, 9, 1, 12, 0)
    started = now - dt.timedelta(minutes=31)
    conn.execute("UPDATE jobs SET state='running', started_at=? WHERE id=?",
                (started.isoformat(), job_id))
    conn.commit()

    reaped = jobs.reap_stale(conn, now, minutes=30)

    assert reaped == 1
    row = jobs.get(conn, job_id)
    assert row["state"] == "failed"
    assert row["question"] == "El proceso murió sin cerrar el encargo"
    kinds = [r[0] for r in conn.execute(
        "SELECT kind FROM events WHERE job_id=?", (job_id,))]
    assert "job_failed" in kinds


def test_reap_stale_leaves_a_job_running_under_the_limit(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    job_id = jobs.add(conn, "one")
    now = dt.datetime(2026, 9, 1, 12, 0)
    started = now - dt.timedelta(minutes=5)
    conn.execute("UPDATE jobs SET state='running', started_at=? WHERE id=?",
                (started.isoformat(), job_id))
    conn.commit()

    reaped = jobs.reap_stale(conn, now, minutes=30)

    assert reaped == 0
    assert jobs.get(conn, job_id)["state"] == "running"


def test_run_next_stamps_started_at_when_a_job_starts(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    job_id = jobs.add(conn, "one")
    when = dt.datetime(2026, 9, 1, 12, 0)
    fake = FakeRunner({"finished": False, "question": "which one?",
                       "summary": "I stopped."})
    jobs.run_next(conn, fake, tmp_path, now=when)
    row = jobs.get(conn, job_id)
    assert row["started_at"] == when.isoformat()


def test_run_next_reaps_a_stale_job_before_picking_the_next_one(tmp_path):
    """A job stuck in 'running' must not sit forever: the next call to
    run_next is also a chance to notice it died."""
    conn = db.connect(tmp_path / "s.db")
    stuck_id = jobs.add(conn, "stuck")
    started = dt.datetime.now() - dt.timedelta(minutes=45)
    conn.execute("UPDATE jobs SET state='running', started_at=? WHERE id=?",
                (started.isoformat(), stuck_id))
    conn.commit()
    jobs.add(conn, "new one")

    fake = FakeRunner({"finished": True, "summary": "done"})
    jobs.run_next(conn, fake, tmp_path)

    assert jobs.get(conn, stuck_id)["state"] == "failed"
