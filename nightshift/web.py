# nightshift/web.py
"""The page. It reads the database and it renders one template."""
import datetime as dt
import pathlib

from fastapi import FastAPI, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from nightshift import backends, engines, jobs, quota, signin

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
        spent = quota.spent_this_week(conn, dt.datetime.now())
        return TEMPLATES.TemplateResponse(request, "brief.html", {
            "needs_you": bucket("needs_you"),
            "done": bucket("done"),
            "no_action": bucket("no_action"),
            "error": _trouble(last),
            "last_good": _when(good["started_at"]) if good else "never",
            "spent": round(spent, 2), "ceiling": ceiling_usd,
            "jobs_waiting": job_rows("needs_you", "failed"),
            "jobs_queued": job_rows("queued", "running"),
            "jobs_done": job_rows("done"),
            "engines": engines_of(),
            "probes": backends.last_probes(conn),
            "job_engine": engines.get_engine(conn),
            "job_engines": engines.JOB_ENGINES})

    @app.post("/jobs")
    def add_job(prompt: str = Form(...)):
        jobs.add(conn, prompt)
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

    return app
