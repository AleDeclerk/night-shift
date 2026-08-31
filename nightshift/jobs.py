"""The job queue. Rule 4 of the spec: a stopped job stays stopped."""
import datetime as dt
import json
import pathlib
import sqlite3

from nightshift import engines

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

# Only Claude can force the schema above. Every other engine gets the same
# shape asked in plain words instead, and a tolerant parse on the way back.
JSON_SHAPE_HINT = """

Answer with a single JSON object and nothing else before or after it, shaped
exactly like this:
{"finished": true, "question": "", "summary": "..."}
Set `finished` to true or false, as described above. Leave `question` empty
unless `finished` is false."""


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


def answer(conn: sqlite3.Connection, job_id: int, text: str) -> None:
    """The user answered the question a stopped job asked. Back to the
    queue it goes, so the next cycle picks it up again."""
    conn.execute("UPDATE jobs SET answer=?, state='queued' WHERE id=?",
                 (text, job_id))
    conn.commit()


def retry(conn: sqlite3.Connection, job_id: int) -> None:
    """A failed job, tried again. `fail` never re-queues on its own, so a
    person has to ask for this by hand."""
    conn.execute("UPDATE jobs SET state='queued' WHERE id=?", (job_id,))
    conn.commit()


def run_next(conn, runner_module, workspace,
             engine: str = engines.DEFAULT_ENGINE) -> float:
    """Run the oldest queued job. It returns what the run spent.

    A failed job goes to 'failed', never back to 'queued': the scheduler would
    retry the same failure twice a day and burn the weekly quota.
    The `question` column carries whatever the page must show, so it holds the
    question of a stopped job and the error of a failed one.

    `claude` keeps the original path, with the schema forced by the runner.
    Every other engine goes through `nightshift.engines`, which asks for the
    same shape in words and reads whatever comes back with a tolerant parse.
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

    if engine == "claude":
        r = runner_module.run(prompt, cwd=workdir, schema=SCHEMA)
        if not r.ok:
            fail(conn, job_id, r.error)
            return r.cost_usd
        try:
            data = json.loads(r.text)
        except json.JSONDecodeError:
            fail(conn, job_id,
                 r.error or f"The answer was not JSON: {r.text[:200]}")
            return r.cost_usd
        cost = r.cost_usd
    else:
        er = engines.run(prompt + JSON_SHAPE_HINT, engine=engine, cwd=workdir)
        if not er.ok:
            fail(conn, job_id, er.error)
            return er.cost_usd
        data = engines.parse_job_answer(er.text)
        cost = er.cost_usd

    if not data.get("finished"):
        question = data.get("question") or \
            "The agent stopped and it gave no question."
        stop_and_ask(conn, job_id, question)
    else:
        finish(conn, job_id, str(workdir))
        conn.execute("UPDATE jobs SET answer=? WHERE id=?",
                     (data.get("summary"), job_id))
        conn.commit()
    return cost
