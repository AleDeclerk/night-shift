"""The job queue. Rule 4 of the spec: a stopped job stays stopped."""
import datetime as dt
import sqlite3


def add(conn: sqlite3.Connection, prompt: str) -> int:
    cur = conn.execute(
        "INSERT INTO jobs (created_at, prompt, state) VALUES (?,?,'queued')",
        (dt.datetime.now().isoformat(), prompt))
    conn.commit()
    return cur.lastrowid


def get(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def next_queued(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM jobs WHERE state = 'queued' ORDER BY id LIMIT 1"
    ).fetchone()


def stop_and_ask(conn: sqlite3.Connection, job_id: int, question: str) -> None:
    conn.execute("UPDATE jobs SET state='needs_you', question=? WHERE id=?",
                 (question, job_id))
    conn.commit()


def finish(conn: sqlite3.Connection, job_id: int, result_path: str) -> None:
    conn.execute("UPDATE jobs SET state='done', result_path=? WHERE id=?",
                 (result_path, job_id))
    conn.commit()
