# nightshift/web.py
"""The page. It reads the database and it renders one template."""
import datetime as dt
import pathlib

from fastapi import FastAPI, Form
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from nightshift import (backends, board, cascade, drafts, engines, jobs,
                        knowledge, life, mail, projects, quota, runner,
                        runs, signin, tick)

TEMPLATES = Jinja2Templates(
    directory=str(pathlib.Path(__file__).parent / "templates"))


# A measured cycle takes one to three minutes. Past this, a run that never
# ended is dead, not busy.
STALE_MINUTES = 15

# The port the server binds, defined once. `scripts/serve.py` reads it, and so
# does the guard below: a second copy would drift and open the page to a host
# that the guard no longer knows.
PORT = 8899

# The names of this machine, and nothing else.
LOCAL_NAMES = ("127.0.0.1", "localhost")

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


def make_app(conn, ceiling_usd: float, engine_source=None,
             port: int = PORT) -> FastAPI:
    """`engine_source` lets a test inject engines. Without it the page runs the
    cheap checks, which take about five seconds when the cache is cold."""
    engines_of = engine_source or backends.check_all
    app = FastAPI()

    hosts = {f"{name}:{port}" for name in LOCAL_NAMES}
    origins = {f"http://{name}:{port}" for name in LOCAL_NAMES}

    @app.middleware("http")
    async def only_this_machine(request: Request, call_next):
        """The page moves money, so it answers this machine alone.

        A cross-site form POST is a simple request: the browser sends it with
        no preflight, and the effect happens before any answer is read. Four
        routes spend the weekly quota or start a sign-in, and one of them
        answers with a sign-in link, which is a credential.

        A `Host` that this server does not bind is a rebinding attack: a name
        that resolves to 127.0.0.1 would reach the page from any tab.

        A POST that carries neither `Origin` nor `Sec-Fetch-Site` passes.
        This is a personal tool and curl is how the owner drives it, and a
        browser always sends at least one of the two.
        """
        if request.headers.get("host") not in hosts:
            return PlainTextResponse(
                "This page answers this machine only.", status_code=403)
        if request.method == "POST":
            origin = request.headers.get("origin")
            if origin is not None and origin not in origins:
                return PlainTextResponse(
                    "This page takes orders from itself only.",
                    status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return PlainTextResponse(
                    "This page takes orders from itself only.",
                    status_code=403)
        return await call_next(request)

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

        # Both lines speak about the mail. The tick writes a run of its own
        # once an hour, and a clean tick would otherwise cover the error of
        # the last cycle and pass as the last good one.
        last = conn.execute(
            "SELECT * FROM runs WHERE kind = 'mail' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        good = conn.execute(
            "SELECT * FROM runs WHERE ok = 1 AND kind = 'mail'"
            " ORDER BY id DESC LIMIT 1").fetchone()
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
        if row is None:
            return RedirectResponse("/", status_code=303)

        now = dt.datetime.now()
        # The same two checks the cycle makes before a draft. A redo is one
        # more call on the same quota, and it is the only call that a person
        # can ask for again and again in a row.
        decision = quota.may_run(conn, now, ceiling_usd)
        room_ok, room_reason = cascade.has_room(conn, cascade.LADDER[0], now)
        if not decision.allowed or not room_ok:
            life.record(conn, "redo_refused", item_id=item_id, now=now,
                        detail=(decision.reason if not decision.allowed
                                else room_reason)[:200])
            return RedirectResponse("/", status_code=303)

        item = mail.Item(bucket=row["bucket"], title=row["title"],
                         body=drafts.reason_of(row["body"] or ""),
                         source_url=row["source_url"] or "",
                         excerpt=row["excerpt"] or "")
        mail_engine = engines.get_mail_engine(conn)
        compose_engine, compose_model = mail_engine, None
        if mail_engine == "auto":
            step, _fallen_from = cascade.choose(conn, now)
            compose_engine, compose_model = step.engine, step.model

        def keep(body) -> int:
            conn.execute("UPDATE items SET body=? WHERE id=?", (body, item_id))
            conn.commit()
            return item_id

        # The cycle wraps its own drafts in a run, and this one needs its
        # own: `quota.spent_this_week` reads the `runs` table alone, so a
        # redo that opened no run would be invisible to the weekly ceiling.
        run_id = runs.start(conn, now, "redo")
        # The same function the cycle calls: Claude composes and saves in one
        # go, any other engine composes and Claude saves it. The cost is
        # recorded whether or not the draft worked.
        answer, _item_id = drafts.for_item(
            conn, mail, runner, item, engine=compose_engine,
            model=compose_model, cwd=backends.WORKSPACE, save=keep, now=now)
        runs.end(conn, run_id, answer.ok, cost=answer.cost_usd,
                 error=None if answer.ok else answer.note[:400], now=now)
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
        # A probe is a real call, and it costs 0.17 to 0.34 USD of the same
        # weekly quota. `save_probe` writes the `probes` table, which neither
        # governor reads, so the price has to land in both of the places that
        # they do read: a run for `quota`, an event with an engine for the
        # cascade. Without them the machine room could spend a whole week of
        # quota one button at a time.
        now = dt.datetime.now()
        run_id = runs.start(conn, now, "probe")
        result = backends.probe(name)
        backends.save_probe(conn, result)
        life.record(conn, "probe_ran", engine=name,
                    cost_usd=result.cost_usd, detail=result.detail[:120],
                    now=now)
        runs.end(conn, run_id, result.ok, cost=result.cost_usd,
                 error=None if result.ok else result.detail[:400], now=now)
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
