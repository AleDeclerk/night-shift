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
            "error": last["error"] if last and not last["ok"] else None,
            "last_good": _when(good["started_at"]) if good else "never",
            "spent": round(spent, 2), "ceiling": ceiling_usd})

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
