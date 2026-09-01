"""The job queue. Rule 4 of the spec: a stopped job stays stopped."""
import calendar
import datetime as dt
import json
import pathlib
import sqlite3

from nightshift import life

from nightshift import engines

SCHEDULES = ("once", "hourly", "every_3h", "twice_daily", "daily",
             "weekdays", "weekly", "biweekly", "monthly")

# The label the page shows next to each schedule. `once` says what it does,
# nothing more; the rest read as a person would say them out loud.
LABELS = {
    "once": "Una vez",
    "hourly": "Cada hora",
    "every_3h": "Cada 3 horas",
    "twice_daily": "Dos veces por día",
    "daily": "Todos los días",
    "weekdays": "Días de semana",
    "weekly": "Todas las semanas",
    "biweekly": "Cada dos semanas",
    "monthly": "Todos los meses",
}

# A job outside these two states is still open: a template whose last job
# is open gets skipped instead of piling a second, identical job on top.
_CLOSED_JOB_STATES = ("done", "failed")

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


def _spawn_job(conn: sqlite3.Connection, *, prompt: str,
               project_id: int | None, template_id: int | None,
               now: dt.datetime) -> int:
    """One concrete row of work: it belongs to a project or not, and it
    happens once, whether a person queued it by hand or a template's turn
    made it."""
    cur = conn.execute(
        "INSERT INTO jobs (created_at, prompt, state, project_id, schedule,"
        " template_id) VALUES (?,?,'queued',?,'once',?)",
        (now.isoformat(), prompt, project_id, template_id))
    conn.commit()
    job_id = cur.lastrowid
    # The weekly board reads events, never this table. A state change with no
    # event is work that the board reports as zero.
    life.record(conn, "job_queued", job_id=job_id, detail=prompt[:200], now=now)
    return job_id


def add(conn: sqlite3.Connection, prompt: str, *, project_id: int | None = None,
        schedule: str = "once", now: dt.datetime | None = None) -> int:
    """Queue a job. `once` is the old behaviour: one row, done for good.

    Any other schedule makes a template (section 4 of the design) and, at
    the same time, the first job it stands for. From then on
    `jobs.fire_templates` makes the rest, one at a time, as each turn comes
    due.
    """
    if schedule not in SCHEDULES:
        raise ValueError(f"Unknown schedule: {schedule}")
    now = now or dt.datetime.now()

    if schedule == "once":
        return _spawn_job(conn, prompt=prompt, project_id=project_id,
                          template_id=None, now=now)

    next_run = next_after(schedule, now)
    cur = conn.execute(
        "INSERT INTO jobs (created_at, prompt, state, project_id, schedule,"
        " next_run) VALUES (?,?,'template',?,?,?)",
        (now.isoformat(), prompt, project_id, schedule, next_run.isoformat()))
    conn.commit()
    template_id = cur.lastrowid
    life.record(conn, "job_queued", job_id=template_id, detail=prompt[:200],
               now=now)

    return _spawn_job(conn, prompt=prompt, project_id=project_id,
                      template_id=template_id, now=now)


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
    life.record(conn, "job_done", job_id=job_id, detail=result_path)


def fail(conn: sqlite3.Connection, job_id: int, error: str) -> None:
    """A failed job never goes back to 'queued': the scheduler would retry
    the same failure twice a day and burn the weekly quota on it."""
    conn.execute("UPDATE jobs SET state='failed', question=? WHERE id=?",
                 (error, job_id))
    conn.commit()
    life.record(conn, "job_failed", job_id=job_id, detail=str(error)[:200])


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


# --- schedules: a recurring task is a template, not a copy ---------------

def _next_clock(now: dt.datetime, hours: tuple[int, ...]) -> dt.datetime:
    """The next moment, minute zero, whose hour is one of `hours`, strictly
    after `now`. `hourly` and `every_3h` are a fixed interval, so they add a
    timedelta; a clock time is not an interval, since the interval it stands
    for would depend on when the job was first queued instead of the wall
    clock everybody else reads."""
    today = [now.replace(hour=h, minute=0, second=0, microsecond=0)
             for h in hours]
    later_today = sorted(c for c in today if c > now)
    if later_today:
        return later_today[0]
    tomorrow = now + dt.timedelta(days=1)
    return tomorrow.replace(hour=min(hours), minute=0, second=0,
                            microsecond=0)


def next_after(schedule: str, now: dt.datetime) -> dt.datetime | None:
    """The next moment a schedule falls due, counted from `now`.

    `once` never recurs, so it has no next moment: this gives None instead
    of a made-up date that nothing would ever read.
    """
    if schedule == "once":
        return None
    if schedule == "hourly":
        return now + dt.timedelta(hours=1)
    if schedule == "every_3h":
        return now + dt.timedelta(hours=3)
    if schedule == "twice_daily":
        return _next_clock(now, (9, 18))
    if schedule == "daily":
        return _next_clock(now, (9,))
    if schedule == "weekdays":
        candidate = _next_clock(now, (9,))
        while candidate.weekday() >= 5:      # Saturday=5, Sunday=6
            candidate += dt.timedelta(days=1)
        return candidate
    if schedule == "weekly":
        return now + dt.timedelta(weeks=1)
    if schedule == "biweekly":
        return now + dt.timedelta(weeks=2)
    if schedule == "monthly":
        month = now.month % 12 + 1
        year = now.year + (now.month // 12)
        # 31 January has no 31 February: land on the last day the target
        # month has instead of overflowing into the month after it.
        day = min(now.day, calendar.monthrange(year, month)[1])
        return now.replace(year=year, month=month, day=day)
    raise ValueError(f"Unknown schedule: {schedule}")


def due_templates(conn: sqlite3.Connection, now: dt.datetime) -> list[sqlite3.Row]:
    """The templates whose turn has arrived."""
    return conn.execute(
        "SELECT * FROM jobs WHERE state='template' AND next_run <= ?"
        " ORDER BY id", (now.isoformat(),)).fetchall()


def fire_templates(conn: sqlite3.Connection, now: dt.datetime) -> dict:
    """Give every due template its turn.

    A turn whose previous job is still open is skipped, and the card says
    so (section 4 of the design): piling identical jobs is how a queue
    fills with rubbish nobody reads. Firing or skipping, the template's
    `next_run` always moves forward, and an event is always written (rule 2
    of the design): a silent skip is a task that seems to run and does not.
    """
    made, skipped = 0, 0
    for template in due_templates(conn, now):
        template_id = template["id"]
        last_job = conn.execute(
            "SELECT state FROM jobs WHERE template_id=? ORDER BY id DESC"
            " LIMIT 1", (template_id,)).fetchone()
        if last_job is not None and last_job["state"] not in _CLOSED_JOB_STATES:
            life.record(conn, "template_skipped", job_id=template_id,
                       detail=f"the last job is still {last_job['state']}",
                       now=now)
            skipped += 1
        else:
            _spawn_job(conn, prompt=template["prompt"],
                      project_id=template["project_id"],
                      template_id=template_id, now=now)
            life.record(conn, "template_fired", job_id=template_id,
                       detail=template["prompt"][:200], now=now)
            made += 1

        next_run = next_after(template["schedule"], now)
        conn.execute("UPDATE jobs SET next_run=? WHERE id=?",
                     (next_run.isoformat() if next_run else None, template_id))
        conn.commit()
    return {"made": made, "skipped": skipped}


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
