from nightshift import db, jobs


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
