#!/usr/bin/env python3
"""Run the web server. Usage: python scripts/serve.py"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from nightshift import backends, db, web  # noqa: E402

HOME = pathlib.Path.home() / ".night-shift"
CEILING_USD = 20.0

if __name__ == "__main__":
    HOME.mkdir(exist_ok=True)
    backends.warm_up()   # fill the cheap checks before the first request
    conn = db.connect(HOME / "state.db")
    app = web.make_app(conn, CEILING_USD)

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8899)
