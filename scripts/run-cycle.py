#!/usr/bin/env python3
"""launchd calls this file. It runs one cycle and it exits."""
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from nightshift import cycle, db, engines, mail, quota, runner  # noqa: E402

HOME = pathlib.Path.home() / ".night-shift"
CEILING_USD = quota.ceiling_usd()

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
                   workspace=workspace,
                   mail_engine=engines.get_mail_engine(conn))
