# nightshift/web.py
"""The page. It reads the database and it renders one template."""
import datetime as dt
import pathlib

from fastapi import FastAPI, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from nightshift import jobs, quota

TEMPLATES = Jinja2Templates(
    directory=str(pathlib.Path(__file__).parent / "templates"))


def _trouble(last) -> str | None:
    """A run that dies between its start and its end leaves `ok` NULL and no
    error text. Without this branch that run reads as a quiet day."""
    if last is None:
        return None
    if last["ok"] is None:
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


def make_app(conn, ceiling_usd: float) -> FastAPI:
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
            "jobs_done": job_rows("done")})

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

    return app
