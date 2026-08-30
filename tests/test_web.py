# tests/test_web.py
from fastapi.testclient import TestClient

from nightshift import db, web


def test_the_page_shows_the_three_buckets(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO runs (started_at, kind, ok, cost_usd)"
                 " VALUES ('2026-08-26T03:00:00','mail',1,0.4)")
    conn.execute("INSERT INTO items (run_id, created_at, bucket, title, body,"
                 " source_url) VALUES (1,'2026-08-26T03:00:00','needs_you',"
                 "'Shannon: deck','She asks for 3 layouts.','https://x/1')")
    conn.commit()
    client = TestClient(web.make_app(conn, ceiling_usd=5.0))
    body = client.get("/").text
    assert "Shannon: deck" in body
    # The link goes through /open so the page can count what you read. The
    # real address is checked in test_opening_an_item_records_the_time.
    assert "/open/1" in body
    assert "Needs you" in body


def test_a_new_job_goes_to_the_queue(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    client = TestClient(web.make_app(conn, ceiling_usd=5.0))
    client.post("/jobs", data={"prompt": "Prepare the sprint review"},
                follow_redirects=False)
    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert row["prompt"] == "Prepare the sprint review"
    assert row["state"] == "queued"


def test_the_page_shows_the_error_of_the_last_run(tmp_path):
    """A scheduled system fails in silence. The page must say so."""
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO runs (started_at, kind, ok, error)"
                 " VALUES ('2026-08-26T03:00:00','mail',0,"
                 "'401 OAuth access token has expired')")
    conn.commit()
    client = TestClient(web.make_app(conn, ceiling_usd=5.0))
    assert "401" in client.get("/").text


def test_the_page_says_when_the_last_good_cycle_ran(tmp_path):
    """A stale page and a fresh page must not look the same."""
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO runs (started_at, kind, ok)"
                 " VALUES ('2026-08-30T06:30:00','mail',1)")
    conn.commit()
    body = TestClient(web.make_app(conn, ceiling_usd=5.0)).get("/").text
    assert "30 Aug" in body
    assert "06:30" in body


def test_the_page_says_never_when_no_cycle_ever_ran(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    body = TestClient(web.make_app(conn, ceiling_usd=5.0)).get("/").text
    assert "never" in body.lower()


def test_a_failed_cycle_does_not_count_as_the_last_good_one(tmp_path):
    """The point of the line is to show that work stopped."""
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO runs (started_at, kind, ok)"
                 " VALUES ('2026-08-30T06:30:00','mail',1)")
    conn.execute("INSERT INTO runs (started_at, kind, ok, error)"
                 " VALUES ('2026-08-31T06:30:00','mail',0,'401 expired')")
    conn.commit()
    body = TestClient(web.make_app(conn, ceiling_usd=5.0)).get("/").text
    assert "30 Aug" in body  # the good one, not the failed one
    assert "401" in body


def test_opening_an_item_records_the_time(tmp_path):
    """Section 9 of the spec: the count of what you read lives on the page."""
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO runs (started_at, kind, ok) VALUES ('x','mail',1)")
    conn.execute("INSERT INTO items (run_id, created_at, bucket, title,"
                 " source_url) VALUES (1,'x','needs_you','t','https://x/1')")
    conn.commit()
    client = TestClient(web.make_app(conn, ceiling_usd=5.0))
    r = client.get("/open/1", follow_redirects=False)
    assert r.headers["location"] == "https://x/1"
    assert conn.execute("SELECT opened_at FROM items WHERE id=1"
                        ).fetchone()[0] is not None


def test_a_job_that_needs_you_shows_its_question(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO jobs (created_at, prompt, state, question)"
                 " VALUES ('x','Prepare the sprint review','needs_you',"
                 "'Which sprint number?')")
    conn.commit()
    body = TestClient(web.make_app(conn, ceiling_usd=5.0)).get("/").text
    assert "Prepare the sprint review" in body
    assert "Which sprint number?" in body


def test_a_queued_job_is_visible_on_the_page(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO jobs (created_at, prompt, state)"
                 " VALUES ('x','Prepare the sprint review','queued')")
    conn.commit()
    body = TestClient(web.make_app(conn, ceiling_usd=5.0)).get("/").text
    assert "Prepare the sprint review" in body


def test_a_cycle_that_never_finished_is_visible(tmp_path):
    """A crash between the start and the end leaves `ok` NULL, with no error
    text. The page must not look like a quiet day."""
    conn = db.connect(tmp_path / "s.db")
    conn.execute("INSERT INTO runs (started_at, kind)"
                 " VALUES ('2026-08-30T06:30:00','mail')")
    conn.commit()
    body = TestClient(web.make_app(conn, ceiling_usd=5.0)).get("/").text
    assert "did not finish" in body.lower()
