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
    assert "https://x/1" in body
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
