# nightshift/web.py
"""The page. It reads the database and it renders one template."""
import datetime as dt
import pathlib

from fastapi import FastAPI, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from nightshift import (backends, board, cascade, engines, jobs, knowledge,
                        life, mail, projects, quota, runner, signin, tick)

TEMPLATES = Jinja2Templates(
    directory=str(pathlib.Path(__file__).parent / "templates"))


# A measured cycle takes one to three minutes. Past this, a run that never
# ended is dead, not busy.
STALE_MINUTES = 15

# One flow at a time, in memory. A sign-in link is a credential, so it belongs
# in no table and in no log.
_FLOWS: dict = {}


def _trouble(last) -> str | None:
    """A run that dies between its start and its end leaves `ok` NULL and no
    error text. Without this branch that run reads as a quiet day."""
    if last is None:
        return None
    if last["ok"] is None:
        try:
            started = dt.datetime.fromisoformat(last["started_at"])
        except ValueError:
            started = None
        if started and dt.datetime.now() - started < dt.timedelta(
                minutes=STALE_MINUTES):
            return "A cycle is running now. It started at %s." % _when(
                last["started_at"])
        return ("The last cycle started at %s and it did not finish. Look at "
                "/tmp/nightshift.err.log." % last["started_at"])
    if not last["ok"]:
        return last["error"]
    return None


def _when(iso: str) -> str:
    """A stale page and a fresh page must not look the same."""
    try:
        return dt.datetime.fromisoformat(iso).strftime("%a %d %b %H:%M")
    except ValueError:
        return iso


# The template needs the same date format for a probe row. One function, one
# place: `_when` stays the only formatter, reached here as a filter.
TEMPLATES.env.filters["when"] = _when


def make_app(conn, ceiling_usd: float, engine_source=None) -> FastAPI:
    """`engine_source` lets a test inject engines. Without it the page runs the
    cheap checks, which take about five seconds when the cache is cold."""
    engines_of = engine_source or backends.check_all
    app = FastAPI()

    @app.get("/")
    def brief(request: Request):
        def bucket(name):
            return conn.execute(
                "SELECT * FROM items WHERE bucket = ? ORDER BY id DESC LIMIT 50",
                (name,)).fetchall()

        def job_rows(*states):
            marks = ",".join("?" * len(states))
            return conn.execute(
                f"SELECT * FROM jobs WHERE state IN ({marks}) ORDER BY id DESC",
                states).fetchall()

        last = conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        good = conn.execute(
            "SELECT * FROM runs WHERE ok = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        now = dt.datetime.now()
        spent = quota.spent_this_week(conn, now)
        claude_ceiling = cascade.ceiling_of(cascade.LADDER[0])
        ladder = [{
            "step": step, "spent": cascade.spent_by(conn, step.engine, now),
            "ok": cascade.has_room(conn, step, now)[0],
            "ceiling": cascade.ceiling_of(step),
        } for step in cascade.LADDER]
        claude_reserve = cascade.reserve_for(now, claude_ceiling)

        jobs_queued = job_rows("queued", "running")
        all_projects_rows = projects.all_projects(conn)
        projects_by_id = {p["id"]: p for p in all_projects_rows}
        # A handful of projects at most: one query per row costs nothing here.
        path_counts = {p["id"]: len(projects.paths_of(conn, p["id"]))
                       for p in all_projects_rows}

        def job_knowledge(job):
            """What the graph of a job's project already knows about it.
            Section 5 of the design: read only, and shown before it is used.
            A job with no project, or a project with no graph, still runs;
            this just says so instead of guessing."""
            project_id = job["project_id"]
            if not project_id:
                return None
            project = projects_by_id.get(project_id)
            if project is None:
                return None
            graph_path = project["graph_path"]
            if not graph_path:
                return {"has_graph": False, "hits": [], "name": project["name"]}
            return {"has_graph": True,
                    "hits": knowledge.about(graph_path, job["prompt"]),
                    "name": project["name"]}

        return TEMPLATES.TemplateResponse(request, "brief.html", {
            "needs_you": life.open_items(conn),
            "done": life.closed_items(conn),
            "no_action": bucket("no_action"),
            "error": _trouble(last),
            "last_good": _when(good["started_at"]) if good else "never",
            "spent": round(spent, 2), "ceiling": ceiling_usd,
            "jobs_waiting": job_rows("needs_you", "failed"),
            "jobs_queued": jobs_queued,
            "jobs_done": job_rows("done"),
            "engines": engines_of(),
            "probes": backends.last_probes(conn),
            "job_engine": engines.get_engine(conn),
            "all_projects": all_projects_rows,
            "path_counts": path_counts,
            "job_engines": engines.JOB_ENGINES,
            "mail_engine": engines.get_mail_engine(conn),
            "mail_engines": engines.MAIL_ENGINES,
            "ladder": ladder, "claude_ceiling": claude_ceiling,
            "claude_reserve": claude_reserve,
            "projects": all_projects_rows,
            "schedules": jobs.SCHEDULES,
            "schedule_labels": jobs.LABELS,
            "queued_knowledge": {j["id"]: job_knowledge(j) for j in jobs_queued}})

    @app.get("/semana")
    def week_page(request: Request):
        """The board is for looking at the week. Section 6 of the design
        keeps it off the shift report on purpose: numbers that ask for
        nothing do not belong on the morning page."""
        return TEMPLATES.TemplateResponse(request, "week.html", {
            "week": board.week(conn), "by_day": board.by_day(conn)})

    @app.post("/jobs")
    def add_job(prompt: str = Form(...), project_id: str = Form(""),
                schedule: str = Form("once")):
        try:
            jobs.add(conn, prompt,
                    project_id=int(project_id) if project_id else None,
                    schedule=schedule)
        except ValueError:
            pass          # an unknown schedule changes nothing
        return RedirectResponse("/", status_code=303)

    @app.post("/queue/run")
    def run_queue():
        """The cheap tick, on request: it fires due templates and runs the
        queue, for whoever does not want to wait for the next hour."""
        tick.run(conn, runner_module=runner, workspace=backends.WORKSPACE,
                now=dt.datetime.now(), ceiling_usd=ceiling_usd)
        return RedirectResponse("/", status_code=303)

    @app.post("/projects")
    def add_project(name: str = Form(...), scope: str = Form(...)):
        try:
            projects.add(conn, name, scope)
        except ValueError:
            pass          # an unknown scope changes nothing
        return RedirectResponse("/", status_code=303)

    @app.get("/open/{item_id}")
    def open_item(item_id: int):
        """Section 9 of the spec: the page counts what you read."""
        row = conn.execute("SELECT source_url FROM items WHERE id=?",
                           (item_id,)).fetchone()
        conn.execute("UPDATE items SET opened_at=? WHERE id=?",
                     (dt.datetime.now().isoformat(), item_id))
        conn.commit()
        return RedirectResponse(row["source_url"] if row else "/",
                                status_code=303)

    # Registered before the generic /items/{item_id}/{verb} below: Starlette
    # matches routes in the order they were added, and "rehacer" needs its
    # own handler because it writes a new draft, not only a new state.
    @app.post("/items/{item_id}/rehacer")
    def redo_item(item_id: int):
        row = conn.execute("SELECT * FROM items WHERE id=?",
                           (item_id,)).fetchone()
        if row is not None:
            item = mail.Item(bucket=row["bucket"], title=row["title"],
                             body=row["body"] or "",
                             source_url=row["source_url"] or "",
                             excerpt=row["excerpt"] or "")
            mail_engine = engines.get_mail_engine(conn)
            compose_engine, compose_model = mail_engine, None
            if mail_engine == "auto":
                step, _fallen_from = cascade.choose(conn, dt.datetime.now())
                compose_engine, compose_model = step.engine, step.model
            # The same call the cycle makes: Claude composes and saves in one
            # go, any other engine composes and Claude saves it.
            cost, ok, note = mail.reply_cost_and_trace(
                mail, runner, item, engine=compose_engine,
                model=compose_model, cwd=backends.WORKSPACE)
            if ok:
                life.record(conn, "draft_written", item_id=item_id,
                            engine=compose_engine, cost_usd=cost, detail=note)
            life.apply_verb(conn, item_id, "rehacer")
        return RedirectResponse("/", status_code=303)

    # Registered before the generic /items/{item_id}/{verb} below, the same
    # way "rehacer" is: "rate" is not a verb in life.VERBS, so the generic
    # route would silently swallow it as an unknown one.
    @app.post("/items/{item_id}/rate")
    def rate_item(item_id: int, score: int = Form(...), comment: str = Form("")):
        try:
            life.rate(conn, item_id, score, comment or None)
        except ValueError:
            pass          # a score outside 1-10 changes nothing
        return RedirectResponse("/", status_code=303)

    @app.post("/items/{item_id}/{verb}")
    def act(item_id: int, verb: str):
        try:
            life.apply_verb(conn, item_id, verb)
        except ValueError:
            pass          # an unknown verb changes nothing
        return RedirectResponse("/", status_code=303)

    @app.post("/jobs/{job_id}/answer")
    def answer_job(job_id: int, answer: str = Form(...)):
        jobs.answer(conn, job_id, answer)
        life.record(conn, "job_queued", job_id=job_id, detail=answer)
        return RedirectResponse("/", status_code=303)

    @app.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: int):
        jobs.fail(conn, job_id, "The user cancelled it.")
        life.record(conn, "job_failed", job_id=job_id,
                    detail="cancelled by the user")
        return RedirectResponse("/", status_code=303)

    @app.post("/jobs/{job_id}/retry")
    def retry_job(job_id: int):
        jobs.retry(conn, job_id)
        life.record(conn, "job_queued", job_id=job_id, detail="retry")
        return RedirectResponse("/", status_code=303)

    @app.get("/machines/cursor/login/status")
    def signin_status():
        flow = _FLOWS.get("cursor")
        if flow is None:
            return {"state": "idle", "has_link": False, "seconds": 0}
        return flow.status()

    @app.post("/machines/cursor/login")
    def signin_start():
        old = _FLOWS.get("cursor")
        if old is not None:
            old.cancel()          # one attempt at a time
        flow = signin.Flow()
        _FLOWS["cursor"] = flow
        flow.start()
        # The link travels to the page here and nowhere else.
        link = flow.wait_for_link(timeout=25)
        backends.invalidate()   # the panel must show the new state at once
        return {"link": link, "state": flow.status()["state"]}

    @app.post("/machines/cursor/login/cancel")
    def signin_cancel():
        flow = _FLOWS.get("cursor")
        if flow is not None:
            flow.cancel()
        return {"state": "cancelled"}

    @app.post("/machines/{name}/probe")
    def probe_engine(name: str):
        result = backends.probe(name)
        backends.save_probe(conn, result)
        backends.invalidate()
        return {"ok": result.ok, "can_mail": result.can_mail,
                "cost_usd": result.cost_usd, "detail": result.detail}

    @app.post("/machines/engine")
    def choose_engine(name: str = Form(...)):
        try:
            engines.set_engine(conn, name)
        except ValueError:
            pass          # an unknown name changes nothing
        return RedirectResponse("/", status_code=303)

    @app.post("/machines/mail-engine")
    def choose_mail_engine(name: str = Form(...)):
        try:
            engines.set_mail_engine(conn, name)
        except ValueError:
            pass          # an unknown name changes nothing
        return RedirectResponse("/", status_code=303)

    @app.post("/projects/{project_id}")
    def edit_project(project_id: int, scope: str = Form(None),
                     name: str = Form(None), active: str = Form(None)):
        projects.edit(conn, project_id, scope=scope, name=name,
                      active=None if active is None else active == "1")
        return RedirectResponse("/", status_code=303)

    @app.post("/projects/{project_id}/merge")
    def merge_project(project_id: int, into: str = Form(...)):
        try:
            target_id = int(into)
        except ValueError:
            return RedirectResponse("/", status_code=303)
        try:
            projects.merge(conn, target_id, project_id)
        except ValueError:
            pass          # merging a project into itself changes nothing
        return RedirectResponse("/", status_code=303)

    return app
