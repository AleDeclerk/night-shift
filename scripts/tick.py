#!/usr/bin/env python3
"""launchd calls this file every hour. It runs one tick and it exits."""
import datetime as dt
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from nightshift import db, runner, tick  # noqa: E402

HOME = pathlib.Path.home() / ".night-shift"
CEILING_USD = float(os.environ.get("NIGHTSHIFT_CEILING_USD", "20.0"))

if __name__ == "__main__":
    HOME.mkdir(exist_ok=True)
    # The same workspace the mail cycle uses: one directory this program
    # owns, never a repository, so a -p session opens with no trust dialog.
    workspace = HOME / "workspace"
    workspace.mkdir(exist_ok=True)
    conn = db.connect(HOME / "state.db")
    result = tick.run(conn, runner_module=runner, workspace=workspace,
                      now=dt.datetime.now(), ceiling_usd=CEILING_USD)
    print(f"fired={result['fired']} skipped={result['skipped']} "
          f"jobs_run={result['jobs_run']} cost_usd={result['cost_usd']} "
          f"reason={result['reason']!r}")
