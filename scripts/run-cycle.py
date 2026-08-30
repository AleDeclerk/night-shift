#!/usr/bin/env python3
"""launchd calls this file. It runs one cycle and it exits."""
import datetime as dt
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from nightshift import cycle, db, mail, runner  # noqa: E402

HOME = pathlib.Path.home() / ".night-shift"
# A cycle with a triage and three drafts costs about 3 USD of equivalent
# spend. Two cycles each day give about 42 USD in a week, so this starts at
# 45. Lower it after you see one real week.
CEILING_USD = float(os.environ.get("NIGHTSHIFT_CEILING_USD", "45.0"))

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
