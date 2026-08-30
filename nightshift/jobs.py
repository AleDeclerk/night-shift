"""The job queue. Rule 4 of the spec: a stopped job stays stopped."""
import datetime as dt
import json
import pathlib
import sqlite3

SCHEMA = {
    "type": "object",
    "properties": {
        "finished": {"type": "boolean"},
        "question": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["finished", "summary"],
}

PROMPT = """Do this job for Alejandro Declerk.

The job: {job}

Write every file that you produce into the directory {workdir}.

Answer with three fields. Set `finished` to true only when the work is done.
If you need a decision that only Alejandro can give, set `finished` to false
and put the one question in `question`. Do not guess and do not choose for him.
Put a short report of what you did in `summary`."""


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


def fail(conn: sqlite3.Connection, job_id: int, error: str) -> None:
    """A failed job never goes back to 'queued': the scheduler would retry
    the same failure twice a day and burn the weekly quota on it."""
    conn.execute("UPDATE jobs SET state='failed', question=? WHERE id=?",
                 (error, job_id))
    conn.commit()


def run_next(conn, runner_module, workspace) -> float:
    """Run the oldest queued job. It returns what the run spent.

    A failed job goes to 'failed', never back to 'queued': the scheduler would
    retry the same failure twice a day and burn the weekly quota.
    The `question` column carries whatever the page must show, so it holds the
    question of a stopped job and the error of a failed one.
    """
    job = next_queued(conn)
    if job is None:
        return 0.0

    job_id = job["id"]
    workdir = pathlib.Path(workspace) / f"job-{job_id}"
    workdir.mkdir(parents=True, exist_ok=True)

    conn.execute("UPDATE jobs SET state='running' WHERE id=?", (job_id,))
    conn.commit()

    prompt = PROMPT.format(job=job["prompt"], workdir=workdir)
    r = runner_module.run(prompt, cwd=workdir, schema=SCHEMA)
    if not r.ok:
        fail(conn, job_id, r.error)
        return r.cost_usd

    try:
        data = json.loads(r.text)
    except json.JSONDecodeError:
        fail(conn, job_id, r.error or f"The answer was not JSON: {r.text[:200]}")
        return r.cost_usd

    if not data.get("finished"):
        question = data.get("question") or \
            "The agent stopped and it gave no question."
        stop_and_ask(conn, job_id, question)
    else:
        finish(conn, job_id, str(workdir))
        conn.execute("UPDATE jobs SET answer=? WHERE id=?",
                     (data.get("summary"), job_id))
        conn.commit()
    return r.cost_usd
