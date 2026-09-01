#!/usr/bin/env python3
"""Run the web server. Usage: python scripts/serve.py"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from nightshift import backends, db, projects, quota, web  # noqa: E402

HOME = pathlib.Path.home() / ".night-shift"

if __name__ == "__main__":
    HOME.mkdir(exist_ok=True)
    # Reading directories is free, so the page never waits for a cycle to
    # show a project that already exists on disk.
    backends.warm_up()   # fill the cheap checks before the first request
    conn = db.connect(HOME / "state.db")
    # Reading directories is free, so the page never waits for a cycle to
    # show a project that already exists on disk.
    projects.sync(conn)
    # The same ceiling the cycle and the tick read. The page used to hold
    # its own copy of 20.0 and ignore NIGHTSHIFT_CEILING_USD, while the
    # budget card told the user to raise it.
    app = web.make_app(conn, quota.ceiling_usd())

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8899)
