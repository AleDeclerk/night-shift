from nightshift import db, jobs


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
