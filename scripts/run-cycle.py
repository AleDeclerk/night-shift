#!/usr/bin/env python3
"""launchd calls this file. It runs one cycle and it exits."""
import datetime as dt
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from nightshift import cycle, db, mail, runner  # noqa: E402

HOME = pathlib.Path.home() / ".night-shift"
# Measured on 2026-08-30 with three real cycles: a cycle that finds nothing
# costs 0.87 USD of equivalent spend, and each draft adds about 0.80. One cycle
# each day with three drafts is about 3.3 USD, so a week lands near 23. The
# ceiling sits a little under that on purpose: it must bite before the week
# ends if the mail volume grows.
CEILING_USD = float(os.environ.get("NIGHTSHIFT_CEILING_USD", "20.0"))

if __name__ == "__main__":
    HOME.mkdir(exist_ok=True)
    # An empty directory that this program owns. A -p session connects the MCP
    # servers and runs the hooks of its working directory with no trust
    # dialog, so this must never be a repository.
    workspace = HOME / "workspace"
    workspace.mkdir(exist_ok=True)
    conn = db.connect(HOME / "state.db")
    cycle.run_once(conn, runner_module=runner, mail_module=mail,
                   now=dt.datetime.now(), ceiling_usd=CEILING_USD,
                   workspace=workspace)
